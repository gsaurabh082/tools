from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings, build_verify

_MR_URL_RE = re.compile(r"^(https?://[^/]+)/(.+?)/-/merge_requests/(\d+)")


class GitLabError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_mr_url(mr_url: str) -> tuple[str, str, int]:
    """Split a merge-request URL into (origin, project_path, iid)."""
    match = _MR_URL_RE.match(mr_url.strip())
    if not match:
        raise ValueError(
            "That does not look like a GitLab merge request URL "
            "(expected .../<project>/-/merge_requests/<iid>)."
        )
    origin, project_path, iid = match.groups()
    return origin, project_path, int(iid)


class GitLabClient:
    """Minimal GitLab client: look up and merge a merge request by URL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        verify = build_verify(settings.gitlab_verify_ssl, settings.gitlab_ca_bundle)
        self._client = httpx.AsyncClient(
            headers={"PRIVATE-TOKEN": settings.gitlab_token, "Accept": "application/json"},
            timeout=settings.request_timeout,
            follow_redirects=False,
            verify=verify,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise GitLabError(f"Could not reach GitLab: {exc}") from exc
        if response.status_code == 401:
            raise GitLabError("GitLab rejected the token. Check GITLAB_TOKEN and its scopes.", 401)
        if response.status_code == 403:
            raise GitLabError("The GitLab token does not have access to this merge request.", 403)
        if response.status_code == 404:
            raise GitLabError("GitLab merge request was not found for this token.", 404)
        if response.status_code == 405:
            detail = response.text[:300]
            raise GitLabError(f"GitLab refused to merge (not mergeable): {detail}", 405)
        if response.status_code == 406:
            raise GitLabError(
                "GitLab refused to merge: the source branch has moved since the tested "
                "commit was built (someone pushed new commits). Merging was blocked to avoid "
                "merging code that was never actually tested by Jenkins.",
                406,
            )
        if response.status_code >= 400:
            raise GitLabError(
                f"GitLab API returned {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response

    @staticmethod
    def _api_base(origin: str) -> str:
        return f"{origin.rstrip('/')}/api/v4"

    @staticmethod
    def _project_ref(project_path: str) -> str:
        return quote(project_path, safe="")

    async def get_merge_request(self, origin: str, project_path: str, iid: int) -> dict[str, Any]:
        url = f"{self._api_base(origin)}/projects/{self._project_ref(project_path)}/merge_requests/{iid}"
        response = await self._request("GET", url)
        return response.json()

    async def merge_merge_request(
        self, origin: str, project_path: str, iid: int, *, sha: str | None = None
    ) -> dict[str, Any]:
        url = (
            f"{self._api_base(origin)}/projects/{self._project_ref(project_path)}"
            f"/merge_requests/{iid}/merge"
        )
        params: dict[str, str] = {"should_remove_source_branch": "false"}
        if sha:
            # Pins the merge to the exact commit Jenkins built. If someone pushed
            # new commits to the source branch after the build started, this SHA
            # won't match the branch HEAD anymore and GitLab refuses with 406 -
            # instead of silently merging code that was never actually tested.
            params["sha"] = sha
        response = await self._request("PUT", url, params=params)
        return response.json() if response.content else {}

    async def get_branch(self, origin: str, project_path: str, branch: str) -> dict[str, Any]:
        url = (
            f"{self._api_base(origin)}/projects/{self._project_ref(project_path)}"
            f"/repository/branches/{quote(branch, safe='')}"
        )
        response = await self._request("GET", url)
        return response.json()

    async def search_merge_requests(
        self, origin: str, project_path: str, query: str, *, state: str = "opened"
    ) -> list[dict[str, Any]]:
        url = f"{self._api_base(origin)}/projects/{self._project_ref(project_path)}/merge_requests"
        params = {"state": state, "order_by": "updated_at", "sort": "desc", "per_page": "20"}
        if query.strip():
            params["search"] = query.strip()
        response = await self._request("GET", url, params=params)
        result = response.json()
        return result if isinstance(result, list) else []
