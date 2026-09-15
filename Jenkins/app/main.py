from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import (
    Settings,
    save_gitlab_token,
    save_jenkins_credentials,
    save_poll_settings,
)
from .gitlab_client import GitLabClient, GitLabError, parse_mr_url
from .jenkins_client import JenkinsClient, JenkinsError
from .orchestrator import Orchestrator, OrchestratorError
from .store import JsonListStore, new_id, now_iso

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIRM_HEADER = "X-Jenkins-Chain-Action"


class StepInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jenkins_job_url: str = Field(min_length=8, max_length=2000)
    build_params: dict[str, str] = Field(default_factory=dict)
    gitlab_mr_url: str = Field(default="", max_length=500)
    retry_enabled: bool = True
    max_retries: int | None = Field(default=None, ge=0, le=20)
    retry_delay_seconds: int | None = Field(default=None, ge=5, le=3600)
    wait_for_existing_build: bool = False
    verify_commit_before_merge: bool = True
    wait_for_target_branch_update: bool = True

    @field_validator("jenkins_job_url")
    @classmethod
    def must_be_http(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("http://") and not value.startswith("https://"):
            raise ValueError("Jenkins job URL must start with http:// or https://")
        return value


class ChainInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    steps: list[StepInput] = Field(default_factory=list)


class RunRequest(BaseModel):
    auto_merge_confirmed: bool = False


class JobCheckRequest(BaseModel):
    job_url: str = Field(min_length=8, max_length=2000)


class MrCheckRequest(BaseModel):
    mr_url: str = Field(min_length=8, max_length=500)


class JobSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    origin: str = Field(default="", max_length=300)


class MrSearchRequest(BaseModel):
    project_path: str = Field(min_length=1, max_length=400)
    query: str = Field(default="", max_length=200)
    origin: str = Field(default="", max_length=300)


class ConfigUpdateRequest(BaseModel):
    jenkins_user: str = Field(default="", max_length=200)
    jenkins_token: str = Field(default="", max_length=500)
    gitlab_token: str = Field(default="", max_length=500)
    poll_seconds: int = Field(default=15, ge=5, le=600)
    max_retries: int = Field(default=2, ge=0, le=20)
    retry_delay_seconds: int = Field(default=30, ge=5, le=3600)


def _build_step_record(step: StepInput) -> dict[str, Any]:
    gitlab_cfg: dict[str, Any] | None = None
    if step.gitlab_mr_url.strip():
        origin, project_path, iid = parse_mr_url(step.gitlab_mr_url)
        gitlab_cfg = {
            "mr_url": step.gitlab_mr_url.strip(),
            "origin": origin,
            "project_path": project_path,
            "mr_iid": iid,
        }
    return {
        "id": new_id(),
        "name": step.name.strip(),
        "jenkins_job_url": step.jenkins_job_url.strip(),
        "build_params": step.build_params,
        "gitlab": gitlab_cfg,
        "retry_enabled": step.retry_enabled,
        "max_retries": step.max_retries,
        "retry_delay_seconds": step.retry_delay_seconds,
        "wait_for_existing_build": step.wait_for_existing_build,
        "verify_commit_before_merge": step.verify_commit_before_merge,
        "wait_for_target_branch_update": step.wait_for_target_branch_update,
    }


def create_app(settings: Settings | None = None, *, env_path: Path | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_env_path = env_path or BASE_DIR / ".env"

    jenkins_client = JenkinsClient(resolved_settings)
    gitlab_client = GitLabClient(resolved_settings)
    chains_store = JsonListStore(BASE_DIR / "data" / "chains.json")
    runs_store = JsonListStore(BASE_DIR / "data" / "runs.json")
    orchestrator = Orchestrator(resolved_settings, jenkins_client, gitlab_client, runs_store)

    state: dict[str, Any] = {"settings": resolved_settings}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Reconnect to whatever was still "running" when this process last
        # exited, instead of silently losing it - see Orchestrator's docstring.
        await orchestrator.resume_persisted_runs()
        yield
        await jenkins_client.close()
        await gitlab_client.close()

    app = FastAPI(
        title="Jenkins Pipeline Chain",
        version="1.0.0",
        description="Local dashboard to chain Jenkins jobs, poll their status, retry failures, and auto-merge GitLab MRs between steps.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.exception_handler(JenkinsError)
    async def jenkins_error_handler(_: Request, exc: JenkinsError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(GitLabError)
    async def gitlab_error_handler(_: Request, exc: GitLabError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(OrchestratorError)
    async def orchestrator_error_handler(_: Request, exc: OrchestratorError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(BASE_DIR / "templates" / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        settings = state["settings"]
        return {
            "status": "ok",
            "jenkins_configured": settings.jenkins_configured,
            "gitlab_configured": settings.gitlab_configured,
        }

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        settings = state["settings"]
        return {
            "jenkins_user": settings.jenkins_user,
            "jenkins_configured": settings.jenkins_configured,
            "gitlab_configured": settings.gitlab_configured,
            "gitlab_default_origin": settings.gitlab_default_origin,
            "jenkins_default_origin": settings.jenkins_default_origin,
            "poll_seconds": settings.jenkins_poll_seconds,
            "max_retries": settings.jenkins_max_retries,
            "retry_delay_seconds": settings.jenkins_retry_delay_seconds,
        }

    @app.put("/api/config")
    async def update_config(payload: ConfigUpdateRequest, request: Request) -> dict[str, Any]:
        if request.headers.get(CONFIRM_HEADER) != "save-config":
            raise HTTPException(status_code=400, detail="Confirmation header is missing.")

        settings = state["settings"]
        updates: dict[str, Any] = {
            "jenkins_poll_seconds": payload.poll_seconds,
            "jenkins_max_retries": payload.max_retries,
            "jenkins_retry_delay_seconds": payload.retry_delay_seconds,
        }
        save_poll_settings(
            resolved_env_path,
            poll_seconds=payload.poll_seconds,
            max_retries=payload.max_retries,
            retry_delay_seconds=payload.retry_delay_seconds,
        )

        if payload.jenkins_user.strip() and payload.jenkins_token.strip():
            save_jenkins_credentials(resolved_env_path, payload.jenkins_user.strip(), payload.jenkins_token.strip())
            updates["jenkins_user"] = payload.jenkins_user.strip()
            updates["jenkins_token"] = payload.jenkins_token.strip()

        if payload.gitlab_token.strip():
            save_gitlab_token(resolved_env_path, payload.gitlab_token.strip())
            updates["gitlab_token"] = payload.gitlab_token.strip()

        new_settings = replace(settings, **updates)
        state["settings"] = new_settings
        jenkins_client.settings = new_settings
        gitlab_client.settings = new_settings
        orchestrator.reconfigure(new_settings)
        return {"status": "saved"}

    @app.post("/api/jenkins/check")
    async def check_job(payload: JobCheckRequest) -> dict[str, Any]:
        return await jenkins_client.check_job(payload.job_url.strip())

    @app.post("/api/gitlab/check-mr")
    async def check_mr(payload: MrCheckRequest) -> dict[str, Any]:
        origin, project_path, iid = parse_mr_url(payload.mr_url)
        mr = await gitlab_client.get_merge_request(origin, project_path, iid)
        return {
            "title": mr.get("title"),
            "state": mr.get("state"),
            "merge_status": mr.get("detailed_merge_status") or mr.get("merge_status"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "web_url": mr.get("web_url"),
        }

    @app.post("/api/jenkins/search-jobs")
    async def search_jobs(payload: JobSearchRequest) -> dict[str, Any]:
        origin = payload.origin.strip() or state["settings"].jenkins_default_origin
        items = await jenkins_client.search_jobs(origin, payload.query)
        return {"items": items}

    @app.post("/api/gitlab/search-mrs")
    async def search_mrs(payload: MrSearchRequest) -> dict[str, Any]:
        origin = payload.origin.strip() or state["settings"].gitlab_default_origin
        mrs = await gitlab_client.search_merge_requests(origin, payload.project_path.strip(), payload.query)
        return {
            "items": [
                {
                    "iid": mr.get("iid"),
                    "title": mr.get("title"),
                    "source_branch": mr.get("source_branch"),
                    "target_branch": mr.get("target_branch"),
                    "web_url": mr.get("web_url"),
                }
                for mr in mrs
            ]
        }

    @app.get("/api/chains")
    async def list_chains() -> dict[str, Any]:
        return {"items": await chains_store.list()}

    @app.get("/api/chains/{chain_id}")
    async def get_chain(chain_id: str) -> dict[str, Any]:
        chain = await chains_store.get(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail="Chain not found.")
        return chain

    @app.post("/api/chains")
    async def create_chain(payload: ChainInput) -> dict[str, Any]:
        chain = {
            "id": new_id(),
            "name": payload.name.strip(),
            "steps": [_build_step_record(step) for step in payload.steps],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        await chains_store.upsert(chain)
        return chain

    @app.put("/api/chains/{chain_id}")
    async def update_chain(chain_id: str, payload: ChainInput) -> dict[str, Any]:
        existing = await chains_store.get(chain_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Chain not found.")
        chain = {
            "id": chain_id,
            "name": payload.name.strip(),
            "steps": [_build_step_record(step) for step in payload.steps],
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
        }
        await chains_store.upsert(chain)
        return chain

    @app.delete("/api/chains/{chain_id}")
    async def delete_chain(chain_id: str) -> dict[str, Any]:
        deleted = await chains_store.delete(chain_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chain not found.")
        return {"status": "deleted"}

    @app.post("/api/chains/{chain_id}/run")
    async def run_chain(chain_id: str, payload: RunRequest) -> dict[str, Any]:
        chain = await chains_store.get(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail="Chain not found.")
        # Jenkins credentials are optional - some instances allow anonymous
        # trigger/poll (network-level access control instead of per-user auth).
        # JenkinsClient already sends unauthenticated requests when none are set.
        has_gitlab_step = any(step.get("gitlab") for step in chain["steps"])
        if has_gitlab_step and payload.auto_merge_confirmed and not state["settings"].gitlab_configured:
            raise HTTPException(status_code=409, detail="Configure a GitLab token first (Settings).")
        return await orchestrator.start_run(chain, payload.auto_merge_confirmed)

    @app.get("/api/runs")
    async def list_runs() -> dict[str, Any]:
        # resume_persisted_runs() loads every past run into memory at startup,
        # so the in-memory list already reflects everything on disk.
        return {"items": orchestrator.list_runs()}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = orchestrator.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @app.post("/api/runs/{run_id}/retrigger")
    async def retrigger_run(run_id: str) -> dict[str, Any]:
        return await orchestrator.retrigger(run_id)

    @app.post("/api/runs/{run_id}/confirm-merge")
    async def confirm_merge(run_id: str, request: Request) -> dict[str, Any]:
        if request.headers.get(CONFIRM_HEADER) != "confirm-merge":
            raise HTTPException(status_code=400, detail="Merge confirmation header is missing.")
        if not state["settings"].gitlab_configured:
            raise HTTPException(status_code=409, detail="Configure a GitLab token first (Settings).")
        return await orchestrator.confirm_merge(run_id)

    @app.post("/api/runs/{run_id}/skip-merge")
    async def skip_merge(run_id: str) -> dict[str, Any]:
        return await orchestrator.skip_merge(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        return await orchestrator.cancel(run_id)

    return app


app = create_app()
