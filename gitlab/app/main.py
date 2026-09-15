from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import (
    Settings,
    save_code_review_root,
    save_gitlab_token,
    save_write_actions_setting,
)
from .gitlab import GitLabError
from .service import DashboardService

BASE_DIR = Path(__file__).resolve().parent.parent


class TokenConnectionRequest(BaseModel):
    token: str = Field(min_length=8, max_length=512)

    @field_validator("token")
    @classmethod
    def token_must_be_single_value(cls, value: str) -> str:
        token = value.strip()
        if not token or any(character.isspace() for character in token):
            raise ValueError("Token must not contain spaces.")
        return token


class WriteActionsRequest(BaseModel):
    enabled: bool


class CodeReviewRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    project_id: int | None = None
    project_path: str = Field(default="", max_length=400)
    slug: str = Field(default="", max_length=200)


class CodeReviewRootRequest(BaseModel):
    root: str = Field(min_length=1, max_length=500)


class ProjectCheckoutRequest(BaseModel):
    folder: str = Field(default="", max_length=500)


class MergeRequestNoteRequest(BaseModel):
    body: str = Field(min_length=3, max_length=3000)

    @field_validator("body")
    @classmethod
    def note_must_have_content(cls, value: str) -> str:
        body = value.strip()
        if not body:
            raise ValueError("Comment must not be blank.")
        return body


def create_app(
    settings: Settings | None = None,
    *,
    env_path: Path | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_env_path = env_path or BASE_DIR / ".env"
    service = DashboardService(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await service.close()

    app = FastAPI(
        title="GitLab Focus",
        version="1.2.0",
        description="A local action dashboard for GitLab group hygiene.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.dashboard_service = service
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.exception_handler(GitLabError)
    async def gitlab_error_handler(_: Request, exc: GitLabError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(BASE_DIR / "templates" / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        current_settings = service.settings
        return {
            "status": "ok",
            "mode": "demo" if current_settings.demo_mode else "live",
            "group_path": current_settings.group_path,
        }

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        current_settings = service.settings
        return {
            "group_url": current_settings.group_url,
            "group_path": current_settings.group_path,
            "demo_mode": current_settings.demo_mode,
            "token_configured": not current_settings.demo_mode,
            "write_actions_enabled": current_settings.enable_write_actions,
            "cache_seconds": current_settings.cache_seconds,
            "stale_days": current_settings.stale_days,
        }

    @app.post("/api/connection/token")
    async def connect_token(payload: TokenConnectionRequest, request: Request) -> dict[str, Any]:
        if request.headers.get("X-GitLab-Focus-Action") != "connect":
            raise HTTPException(status_code=400, detail="Connection confirmation header is missing.")

        candidate, user, group = await service.validate_token(payload.token)
        try:
            await asyncio.to_thread(save_gitlab_token, resolved_env_path, payload.token)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="The token was valid, but the local .env file could not be saved.",
            ) from exc

        service.reconfigure(candidate)
        app.state.settings = candidate
        return {
            "status": "saved",
            "refresh_required": True,
            "user": {
                "name": user.get("name") or user.get("username"),
                "username": user.get("username"),
            },
            "group": {
                "name": group.get("name") or candidate.group_path,
                "path": group.get("full_path") or candidate.group_path,
            },
        }

    @app.put("/api/config/write-actions")
    async def update_write_actions(
        payload: WriteActionsRequest,
        request: Request,
    ) -> dict[str, Any]:
        if request.headers.get("X-GitLab-Focus-Action") != "write-actions":
            raise HTTPException(status_code=400, detail="Write-action confirmation header is missing.")
        if payload.enabled and service.settings.demo_mode:
            raise HTTPException(
                status_code=409,
                detail="Connect and validate a GitLab token before enabling write actions.",
            )

        try:
            await asyncio.to_thread(
                save_write_actions_setting,
                resolved_env_path,
                payload.enabled,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="The local .env write-action setting could not be saved.",
            ) from exc

        updated_settings = replace(
            service.settings,
            enable_write_actions=payload.enabled,
        )
        service.reconfigure(updated_settings)
        app.state.settings = updated_settings
        return {
            "status": "enabled" if payload.enabled else "disabled",
            "write_actions_enabled": payload.enabled,
        }

    @app.get("/api/dashboard")
    async def dashboard(force: bool = Query(False)) -> dict[str, Any]:
        return await service.dashboard(force=force)

    @app.get("/api/merge-requests")
    async def merge_requests(
        q: str = Query("", max_length=200),
        project: str = Query("", max_length=300),
        attention: str = Query("all", max_length=40),
    ) -> dict[str, Any]:
        payload = await service.dashboard()
        items = payload["merge_requests"]
        if q:
            needle = q.casefold()
            items = [
                item
                for item in items
                if needle in item["title"].casefold()
                or needle in item["project_path"].casefold()
                or needle in item["author"]["name"].casefold()
                or needle in str(item["iid"])
            ]
        if project:
            items = [item for item in items if item["project_path"] == project]
        if attention != "all":
            items = [item for item in items if item["attention"] == attention]
        return {"items": items, "count": len(items), "refreshed_at": payload["refreshed_at"]}

    @app.get("/api/merge-requests/{project_id}/{iid}/reviewer-readiness")
    async def reviewer_readiness(project_id: int, iid: int) -> dict[str, Any]:
        if project_id <= 0 or iid <= 0:
            raise HTTPException(status_code=400, detail="Invalid merge request identifier.")
        return await service.reviewer_readiness(project_id, iid)

    @app.get("/api/authors")
    async def authors() -> dict[str, Any]:
        items = await service.authors()
        return {"items": items, "count": len(items)}

    @app.get("/api/reports/merge-requests")
    async def merge_request_report(
        author: str = Query(..., min_length=1, max_length=200),
        states: str = Query("opened,merged", max_length=60),
    ) -> dict[str, Any]:
        requested = [part.strip() for part in states.split(",") if part.strip()]
        return await service.merge_request_report(author, requested)

    @app.get("/api/projects/{project_id}/branches")
    async def project_branches(
        project_id: int,
        q: str = Query("", max_length=200),
    ) -> dict[str, Any]:
        if project_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid project identifier.")
        return await service.project_branches(project_id, q)

    @app.get("/api/projects/{project_id}/branch-review")
    async def branch_review(
        project_id: int,
        source: str = Query(..., min_length=1, max_length=300),
        target: str = Query("", max_length=300),
    ) -> dict[str, Any]:
        if project_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid project identifier.")
        return await service.branch_review(project_id, source, target)

    @app.post("/api/code-review/run")
    async def run_code_review(payload: CodeReviewRunRequest) -> dict[str, Any]:
        return await service.run_code_review(
            payload.prompt, payload.project_id, payload.project_path, payload.slug
        )

    @app.get("/api/code-review/config")
    async def get_code_review_config() -> dict[str, Any]:
        return service.code_review_config()

    @app.put("/api/code-review/config")
    async def update_code_review_config(payload: CodeReviewRootRequest) -> dict[str, Any]:
        root = payload.root.strip()
        try:
            await asyncio.to_thread(save_code_review_root, resolved_env_path, root)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="The local projects folder could not be saved to .env.",
            ) from exc
        updated_settings = replace(service.settings, code_review_root=root)
        service.reconfigure(updated_settings)
        app.state.settings = updated_settings
        return {"root": root}

    @app.put("/api/projects/{project_id}/code-review-path")
    async def set_project_checkout(
        project_id: int, payload: ProjectCheckoutRequest
    ) -> dict[str, Any]:
        if project_id < 0:
            raise HTTPException(status_code=400, detail="Invalid project identifier.")
        return await asyncio.to_thread(
            service.set_project_checkout, project_id, payload.folder
        )

    @app.get("/api/projects")
    async def projects(q: str = Query("", max_length=200)) -> dict[str, Any]:
        payload = await service.dashboard()
        items = payload["projects"]
        if q:
            needle = q.casefold()
            items = [
                item
                for item in items
                if needle in item["name"].casefold()
                or needle in item["path_with_namespace"].casefold()
            ]
        return {"items": items, "count": len(items)}

    @app.post("/api/merge-requests/{project_id}/{iid}/notes")
    async def add_merge_request_note(
        project_id: int,
        iid: int,
        payload: MergeRequestNoteRequest,
        request: Request,
    ) -> dict[str, Any]:
        if project_id <= 0 or iid <= 0:
            raise HTTPException(status_code=400, detail="Invalid merge request identifier.")
        if request.headers.get("X-GitLab-Focus-Action") != "create-note":
            raise HTTPException(status_code=400, detail="Comment confirmation header is missing.")
        result = await service.add_note(project_id, iid, payload.body)
        return {"status": "created", "note_id": result.get("id")}

    @app.post("/api/refresh")
    async def refresh() -> dict[str, Any]:
        return await service.dashboard(force=True)

    @app.post("/api/merge-requests/{project_id}/{iid}/approve")
    async def approve(project_id: int, iid: int, request: Request) -> dict[str, Any]:
        if project_id <= 0 or iid <= 0:
            raise HTTPException(status_code=400, detail="Invalid merge request identifier.")
        if request.headers.get("X-GitLab-Focus-Action") != "approve":
            raise HTTPException(status_code=400, detail="Approval confirmation header is missing.")
        await service.approve(project_id, iid)
        return {"status": "approved", "project_id": project_id, "iid": iid}

    return app


app = create_app()
