"""Minimal, read-mostly GitLab REST v4 client used for MR <-> Jira sync.

Mirrors the style of ``jira_weekly_report.JiraClient``: standard-library
``urllib`` only, no extra dependencies, and a small ``GitLabError`` used to
surface actionable messages to the FastAPI layer.

The client is intentionally narrow in scope. It only exposes what the Jira
GitLab-sync feature needs: listing merge requests for a group and fetching
one merge request's full detail (used to grab the freshest description right
before a Jira comment is posted).
"""

from __future__ import annotations

import json
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = "jira-friday-status-report-gitlab-sync/1.0"


class GitLabError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def normalize_group_url(value: str) -> str:
    """Trim a pasted GitLab group/project URL down to scheme+host+path."""
    raw = value.strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class GitLabClient:
    def __init__(
        self,
        group_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.group_url = normalize_group_url(group_url)
        if not self.group_url:
            raise GitLabError("Set the GitLab group URL before syncing.")
        if not self.group_url.startswith(("https://", "http://")):
            raise GitLabError("GitLab group URL must start with https:// or http://.")
        self.token = token.strip()
        if not self.token:
            raise GitLabError("A GitLab access token is required.")
        parsed = urlsplit(self.group_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.group_path = parsed.path.strip("/")
        if not self.group_path:
            raise GitLabError("The GitLab URL must include the group path, e.g. .../pia_restricted.")
        self.timeout = timeout
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()  # noqa: SLF001

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.origin}/api/v4{path}{query}",
            method=method,
            headers={
                "PRIVATE-TOKEN": self.token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                raw = response.read()
                headers = dict(response.headers)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code == 401:
                detail = "GitLab rejected the token. Check the token and its scopes (needs at least read_api)."
            elif exc.code == 403:
                detail = "The GitLab token does not have access to this group."
            elif exc.code == 404:
                detail = "GitLab group was not found for this token. Check the group URL."
            raise GitLabError(f"GitLab returned HTTP {exc.code}: {detail}", status=exc.code) from exc
        except URLError as exc:
            raise GitLabError(f"Could not reach GitLab at {self.origin}: {exc.reason}") from exc
        if not raw:
            return {}, headers
        try:
            return json.loads(raw.decode("utf-8")), headers
        except json.JSONDecodeError as exc:
            raise GitLabError("GitLab returned a non-JSON response. Check the group URL.") from exc

    def current_user(self) -> dict[str, Any]:
        data, _ = self._request("GET", "/user")
        return data if isinstance(data, dict) else {}

    def _paginated(self, path: str, params: dict[str, Any], max_pages: int = 20) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query = {**params, "per_page": 100, "page": 1}
        for _ in range(max_pages):
            page, headers = self._request("GET", path, query)
            if not isinstance(page, list):
                break
            items.extend(item for item in page if isinstance(item, dict))
            next_page = headers.get("x-next-page") or headers.get("X-Next-Page")
            if not next_page:
                break
            query["page"] = int(next_page)
        return items

    def group_merge_requests(self, state: str = "opened", updated_after: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "state": state,
            "scope": "all",
            "order_by": "updated_at",
            "sort": "desc",
        }
        if updated_after:
            params["updated_after"] = updated_after
        return self._paginated(f"/groups/{quote(self.group_path, safe='')}/merge_requests", params)

    def merge_request(self, project_id: int, iid: int) -> dict[str, Any]:
        data, _ = self._request(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}",
        )
        if not isinstance(data, dict):
            raise GitLabError("Unexpected response fetching the merge request detail.")
        return data

    def merge_request_notes(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        """First page of comments/notes on a merge request (newest first)."""
        data, _ = self._request(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            {"per_page": 100, "order_by": "created_at", "sort": "desc"},
        )
        return data if isinstance(data, list) else []
