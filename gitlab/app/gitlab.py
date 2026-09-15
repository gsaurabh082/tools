from __future__ import annotations

import asyncio
import ssl
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings


class GitLabError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitLabClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        verify: bool | ssl.SSLContext = settings.verify_ssl
        if settings.ca_bundle:
            verify = ssl.create_default_context(cafile=settings.ca_bundle)
        self._client = httpx.AsyncClient(
            base_url=settings.api_base,
            headers={"PRIVATE-TOKEN": settings.token, "Accept": "application/json"},
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            follow_redirects=False,
            verify=verify,
        )
        self._semaphore = asyncio.Semaphore(8)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        async with self._semaphore:
            try:
                response = await self._client.request(method, path, params=params)
            except httpx.RequestError as exc:
                raise GitLabError(f"Could not reach GitLab: {exc}") from exc

        if response.status_code == 401:
            raise GitLabError("GitLab rejected the token. Check GITLAB_TOKEN and its scopes.", 401)
        if response.status_code == 403:
            raise GitLabError("The GitLab token does not have access to this resource.", 403)
        if response.status_code == 404:
            raise GitLabError("GitLab group or project was not found for this token.", 404)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise GitLabError(f"GitLab API returned {response.status_code}: {detail}", response.status_code)
        return response

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self._request("GET", path, params=params)).json()

    async def post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._request("POST", path, params=params)
        return response.json() if response.content else {}

    async def paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        query = {**(params or {}), "per_page": 100, "page": 1}
        items: list[dict[str, Any]] = []
        for _ in range(max_pages):
            response = await self._request("GET", path, params=query)
            page_items = response.json()
            if not isinstance(page_items, list):
                break
            items.extend(page_items)
            next_page = response.headers.get("x-next-page")
            if not next_page:
                break
            query["page"] = int(next_page)
        return items

    @property
    def group_id(self) -> str:
        return quote(self.settings.group_path, safe="")

    async def current_user(self) -> dict[str, Any]:
        return await self.get("/user")

    async def group(self) -> dict[str, Any]:
        return await self.get(f"/groups/{self.group_id}")

    async def projects(self) -> list[dict[str, Any]]:
        return await self.paginated(
            f"/groups/{self.group_id}/projects",
            {
                "include_subgroups": "true",
                "archived": "false",
                "simple": "true",
                "order_by": "path",
                "sort": "asc",
            },
        )

    async def merge_requests(self) -> list[dict[str, Any]]:
        return await self.paginated(
            f"/groups/{self.group_id}/merge_requests",
            {
                "state": "opened",
                "scope": "all",
                "order_by": "updated_at",
                "sort": "desc",
            },
        )

    async def merge_requests_by_author(
        self, author_id: int, state: str, *, max_pages: int = 3
    ) -> list[dict[str, Any]]:
        """Merge requests the given user authored, in a specific state.

        Used to build the "Merge requests by me" view (merged / opened / closed).
        """
        return await self.paginated(
            f"/groups/{self.group_id}/merge_requests",
            {
                "author_id": author_id,
                "state": state,
                "scope": "all",
                "order_by": "updated_at",
                "sort": "desc",
            },
            max_pages=max_pages,
        )

    async def merge_requests_by_username(
        self, author_username: str, state: str, *, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        """Merge requests authored by a username, in a specific state (for reports)."""
        return await self.paginated(
            f"/groups/{self.group_id}/merge_requests",
            {
                "author_username": author_username,
                "state": state,
                "scope": "all",
                "order_by": "updated_at",
                "sort": "desc",
            },
            max_pages=max_pages,
        )

    async def group_members(self) -> list[dict[str, Any]]:
        """Every member of the group and its subgroups (name + username)."""
        return await self.paginated(
            f"/groups/{self.group_id}/members/all",
            {"per_page": 100},
            max_pages=10,
        )

    async def merge_requests_all_states(
        self, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Recent merge requests in any state, used to discover real contributors."""
        return await self.paginated(
            f"/groups/{self.group_id}/merge_requests",
            {
                "state": "all",
                "scope": "all",
                "order_by": "updated_at",
                "sort": "desc",
            },
            max_pages=max_pages,
        )

    async def search_users(self, query: str) -> list[dict[str, Any]]:
        """Find users by name or username (covers inherited, non-member access)."""
        result = await self.get("/users", {"search": query, "per_page": 20})
        return result if isinstance(result, list) else []

    async def merge_request(self, project_id: int, iid: int) -> dict[str, Any]:
        return await self.get(f"/projects/{project_id}/merge_requests/{iid}")

    async def project_detail(self, project_id: int) -> dict[str, Any]:
        return await self.get(f"/projects/{project_id}")

    async def branches(
        self, project_id: int, search: str = "", *, max_pages: int = 3
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": 100}
        if search:
            params["search"] = search
        return await self.paginated(
            f"/projects/{project_id}/repository/branches",
            params,
            max_pages=max_pages,
        )

    async def merge_requests_for_branch(
        self, project_id: int, source_branch: str
    ) -> list[dict[str, Any]]:
        """Merge requests whose source is this branch (any state), newest first."""
        result = await self.get(
            f"/projects/{project_id}/merge_requests",
            {
                "source_branch": source_branch,
                "state": "all",
                "order_by": "updated_at",
                "sort": "desc",
            },
        )
        return result if isinstance(result, list) else []

    async def compare(
        self, project_id: int, from_ref: str, to_ref: str
    ) -> dict[str, Any]:
        """Diff introduced by to_ref relative to from_ref (from=target, to=source)."""
        return await self.get(
            f"/projects/{project_id}/repository/compare",
            {"from": from_ref, "to": to_ref, "straight": "false"},
        )

    async def assigned_issues(self) -> list[dict[str, Any]]:
        return await self.paginated(
            "/issues",
            {
                "state": "opened",
                "scope": "assigned_to_me",
                "order_by": "updated_at",
                "sort": "desc",
            },
        )

    async def approvals(self, project_id: int, iid: int) -> dict[str, Any]:
        return await self.get(f"/projects/{project_id}/merge_requests/{iid}/approvals")

    async def notes(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        return await self.paginated(
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            {"sort": "desc", "order_by": "created_at"},
            max_pages=2,
        )

    async def create_note(self, project_id: int, iid: int, body: str) -> dict[str, Any]:
        return await self.post(
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            {"body": body},
        )

    async def approve(self, project_id: int, iid: int, sha: str | None = None) -> dict[str, Any]:
        params = {"sha": sha} if sha else None
        return await self.post(
            f"/projects/{project_id}/merge_requests/{iid}/approve",
            params=params,
        )
