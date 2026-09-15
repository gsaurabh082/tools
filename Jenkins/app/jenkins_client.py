from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from .config import Settings, build_verify


class JenkinsError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _root_url(job_url: str) -> str:
    parsed = urlparse(job_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def branch_name_from_job_url(job_url: str) -> str | None:
    """Best-effort branch name for a multibranch pipeline job, decoded straight
    from the URL - it's already encoded there (.../job/release%252F2026.2.0/),
    so this works without an extra Jenkins call and regardless of whether the
    slash was single- or double-percent-encoded."""
    path = urlparse(job_url).path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if not segments or segments[-1] == "job":
        return None
    decoded = unquote(segments[-1])
    if "%2f" in decoded.lower():
        decoded = unquote(decoded)
    return decoded or None


def _extract_git_info(build_json: dict[str, Any], expected_branch: str | None) -> dict[str, str] | None:
    """Pull (branch, commit SHA) out of a build's actions.

    A build can carry several hudson.plugins.git.util.BuildData entries - one
    per git repo involved (shared libraries, e2e libs, the app repo itself) -
    so the first one isn't reliable. Prefer whichever entry's branch matches
    the branch already encoded in the job URL (git-plugin often prefixes it,
    e.g. "origin/release/2026.2.0"); fall back to the first entry found.
    """
    candidates: list[tuple[str, str]] = []
    for action in build_json.get("actions") or []:
        if action.get("_class") != "hudson.plugins.git.util.BuildData":
            continue
        revision = action.get("lastBuiltRevision") or {}
        for branch in revision.get("branch") or []:
            name, sha = branch.get("name"), branch.get("SHA1")
            if name and sha:
                candidates.append((name, sha))

    if not candidates:
        return None
    if expected_branch:
        for name, sha in candidates:
            if name == expected_branch or name.endswith(f"/{expected_branch}"):
                return {"branch": name, "commit": sha}
    name, sha = candidates[0]
    return {"branch": name, "commit": sha}


class JenkinsClient:
    """Thin wrapper around the Jenkins REST API.

    Job URLs are treated as opaque strings copied straight from the browser
    (Jenkins multibranch jobs percent-encode branch names, sometimes twice,
    e.g. .../job/release%252F2026.2.0/) - we never re-parse or re-encode the
    path, we only ever append known Jenkins suffixes to it.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        verify = build_verify(settings.jenkins_verify_ssl, settings.jenkins_ca_bundle)
        auth = (
            (settings.jenkins_user, settings.jenkins_token)
            if settings.jenkins_configured
            else None
        )
        self._client = httpx.AsyncClient(
            auth=auth,
            timeout=settings.request_timeout,
            follow_redirects=False,
            verify=verify,
        )
        self._crumb_cache: dict[str, dict[str, str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _crumb_headers(self, job_url: str) -> dict[str, str]:
        root = _root_url(job_url)
        if root in self._crumb_cache:
            return self._crumb_cache[root]
        try:
            response = await self._client.get(f"{root}/crumbIssuer/api/json")
        except httpx.RequestError as exc:
            raise JenkinsError(f"Could not reach Jenkins at {root}: {exc}") from exc
        if response.status_code != 200:
            # CSRF protection may simply be disabled on this instance.
            self._crumb_cache[root] = {}
            return {}
        data = response.json()
        headers = {data["crumbRequestField"]: data["crumb"]}
        self._crumb_cache[root] = headers
        return headers

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise JenkinsError(f"Could not reach Jenkins: {exc}") from exc
        if response.status_code == 401:
            raise JenkinsError("Jenkins rejected the credentials. Check JENKINS_USER / JENKINS_API_TOKEN.", 401)
        if response.status_code == 403:
            raise JenkinsError("Jenkins denied access to this job for this user/token.", 403)
        if response.status_code == 404:
            raise JenkinsError(f"Jenkins job was not found: {url}", 404)
        if response.status_code >= 400:
            raise JenkinsError(
                f"Jenkins returned {response.status_code} for {url}: {response.text[:300]}",
                response.status_code,
            )
        return response

    async def check_job(self, job_url: str) -> dict[str, Any]:
        """Validate a job URL and return basic info, used when adding a chain step."""
        job_url = _ensure_trailing_slash(job_url)
        response = await self._request("GET", f"{job_url}api/json")
        data = response.json()
        last_build = data.get("lastBuild") or {}
        return {
            "name": data.get("fullDisplayName") or data.get("displayName") or data.get("name"),
            "buildable": data.get("buildable", True),
            "last_build_number": last_build.get("number"),
            "last_build_url": last_build.get("url"),
        }

    async def trigger_build(self, job_url: str, params: dict[str, str] | None) -> str:
        """Trigger a build, returns the queue item URL (with trailing slash)."""
        job_url = _ensure_trailing_slash(job_url)
        headers = await self._crumb_headers(job_url)
        if params:
            response = await self._request(
                "POST", f"{job_url}buildWithParameters", params=params, headers=headers
            )
        else:
            response = await self._request("POST", f"{job_url}build", headers=headers)
        location = response.headers.get("Location")
        if not location:
            raise JenkinsError("Jenkins did not return a queue item location for the triggered build.")
        return _ensure_trailing_slash(location)

    async def queue_item(self, queue_url: str) -> dict[str, Any]:
        response = await self._request("GET", f"{queue_url}api/json")
        return response.json()

    async def build_status(self, build_url: str) -> dict[str, Any]:
        build_url = _ensure_trailing_slash(build_url)
        response = await self._request("GET", f"{build_url}api/json")
        return response.json()

    async def build_git_info(self, build_url: str, job_url: str) -> dict[str, str] | None:
        """The branch/commit this build actually checked out, if determinable."""
        data = await self.build_status(build_url)
        return _extract_git_info(data, branch_name_from_job_url(job_url))

    async def search_jobs(self, origin: str, query: str) -> list[dict[str, str]]:
        """Typeahead job search via Jenkins' own header search box endpoint."""
        query = query.strip()
        if not query:
            return []
        response = await self._request(
            "GET", f"{origin.rstrip('/')}/search/suggest", params={"query": query}
        )
        data = response.json()
        results = []
        for item in data.get("suggestions") or []:
            name = item.get("name")
            url = item.get("url") or ""
            if not name:
                continue
            if url and not url.startswith("http"):
                url = f"{origin.rstrip('/')}/{url.lstrip('/')}"
            results.append({"name": name, "url": url})
        return results

    async def running_build(self, job_url: str) -> dict[str, Any] | None:
        """Any of the job's currently in-progress builds, if one exists.

        Used by steps with "wait for existing build" enabled, so a step
        attaches to a build someone already started (manually, or by a
        previous run) instead of queuing a redundant new one.

        Deliberately scans the recent builds list rather than trusting
        lastBuild: when a job allows concurrent builds, an older build
        (e.g. #4) can still be running while newer ones (#5-#8) were
        queued alongside it and already finished - lastBuild would then
        point at a finished build and hide the one actually in progress.
        """
        job_url = _ensure_trailing_slash(job_url)
        response = await self._request(
            "GET", f"{job_url}api/json", params={"tree": "builds[number,url,building]"}
        )
        data = response.json()
        for build in data.get("builds") or []:
            if build.get("building") and build.get("url"):
                return {"number": build.get("number"), "url": _ensure_trailing_slash(build["url"])}
        return None
