from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import Settings
from .gitlab_client import GitLabClient
from .jenkins_client import JenkinsClient
from .store import JsonListStore, new_id, now_iso

ACTIVE_RUN_STATUSES = {"running", "paused_failed", "paused_for_merge"}
TERMINAL_STEP_STATUSES = {"success", "merged", "skipped"}
# Statuses that mean the Jenkins phase already finished (successfully) for this
# step, so resuming the run must not re-trigger the build - it should only
# retry (or wait on) the GitLab merge phase. "merging" is included because a
# process restart can land mid-merge-call, after Jenkins already succeeded.
JENKINS_PHASE_DONE_STATUSES = TERMINAL_STEP_STATUSES | {"waiting_merge_confirm", "merge_failed", "merging"}
MAX_PERSISTED_RUNS = 200


class OrchestratorError(RuntimeError):
    pass


def _log(step: dict[str, Any], message: str) -> None:
    """Append a human-readable line to this step's activity log.

    This is what actually answers "what did the tool do on Jenkins/GitLab" -
    the status badge alone only shows the current state, not the sequence of
    real actions (which build was reused vs. triggered, what GitLab said,
    each retry) that got there.
    """
    step.setdefault("log", []).append({"at": now_iso(), "message": message})


def _new_step_state(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "name": step["name"],
        "jenkins_job_url": step["jenkins_job_url"],
        "build_params": step.get("build_params") or {},
        "gitlab": step.get("gitlab"),
        "status": "idle",
        "attempt": 0,
        "max_retries": step.get("max_retries"),
        "retry_enabled": step.get("retry_enabled", True),
        "retry_delay_seconds": step.get("retry_delay_seconds"),
        "wait_for_existing_build": bool(step.get("wait_for_existing_build")),
        "verify_commit_before_merge": step.get("verify_commit_before_merge", True),
        "wait_for_target_branch_update": step.get("wait_for_target_branch_update", True),
        "queue_url": None,
        "build_number": None,
        "build_url": None,
        "result": None,
        "git_branch": None,
        "git_commit": None,
        "error": None,
        "merge_result": None,
        "started_at": None,
        "finished_at": None,
        "log": [],
    }


def _new_run_state(chain: dict[str, Any], auto_merge_confirmed: bool) -> dict[str, Any]:
    return {
        "id": new_id(),
        "chain_id": chain["id"],
        "chain_name": chain["name"],
        "status": "running",
        "auto_merge_confirmed": auto_merge_confirmed,
        "current_step_index": 0,
        "started_at": now_iso(),
        "finished_at": None,
        "steps": [_new_step_state(step) for step in chain["steps"]],
    }


class Orchestrator:
    """Drives chain runs and checkpoints every state change to disk.

    A run's dict is persisted (not just appended once at the end) so that if
    this process is killed or restarted mid-run - which happens a lot during
    local development, and is exactly what "wait for existing build" needs to
    survive - the run's progress isn't lost. resume_persisted_runs() reloads
    everything at startup and re-drives anything that was still "running",
    reconnecting to whatever Jenkins queue item / build it already had rather
    than triggering a duplicate.
    """

    def __init__(
        self,
        settings: Settings,
        jenkins_client: JenkinsClient,
        gitlab_client: GitLabClient,
        runs_store: JsonListStore,
    ) -> None:
        self.settings = settings
        self.jenkins = jenkins_client
        self.gitlab = gitlab_client
        self._runs_store = runs_store
        self.runs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def reconfigure(self, settings: Settings) -> None:
        self.settings = settings

    def list_runs(self) -> list[dict[str, Any]]:
        return sorted(self.runs.values(), key=lambda run: run["started_at"], reverse=True)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def resume_persisted_runs(self) -> None:
        """Reload runs.json at startup and re-drive anything left "running"."""
        for run in await self._runs_store.list():
            self.runs[run["id"]] = run
            if run.get("status") == "running" and run["current_step_index"] < len(run["steps"]):
                _log(run["steps"][run["current_step_index"]], "Dashboard restarted - resuming this run.")
                self._tasks[run["id"]] = asyncio.create_task(self._drive(run["id"]))

    async def start_run(self, chain: dict[str, Any], auto_merge_confirmed: bool) -> dict[str, Any]:
        if not chain.get("steps"):
            raise OrchestratorError("This chain has no steps to run.")
        run = _new_run_state(chain, auto_merge_confirmed)
        self.runs[run["id"]] = run
        await self._persist(run)
        self._tasks[run["id"]] = asyncio.create_task(self._drive(run["id"]))
        return run

    async def retrigger(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] != "paused_failed":
            raise OrchestratorError("This run is not paused on a failure.")
        step = run["steps"][run["current_step_index"]]
        step["attempt"] = 0
        step["error"] = None
        _log(step, "Retriggered by user.")
        run["status"] = "running"
        await self._persist(run)
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id))
        return run

    async def confirm_merge(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] != "paused_for_merge":
            raise OrchestratorError("This run is not waiting on a merge confirmation.")
        run["auto_merge_confirmed"] = True
        run["status"] = "running"
        _log(run["steps"][run["current_step_index"]], "User confirmed auto-merge for this run.")
        await self._persist(run)
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id))
        return run

    async def skip_merge(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] not in {"paused_for_merge", "paused_failed"}:
            raise OrchestratorError("This run has no pending merge step to skip.")
        step = run["steps"][run["current_step_index"]]
        step["status"] = "skipped"
        step["error"] = None
        _log(step, "Skipped by user.")
        run["status"] = "running"
        run["current_step_index"] += 1
        await self._persist(run)
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id))
        return run

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        run["status"] = "cancelled"
        run["finished_at"] = now_iso()
        if run["current_step_index"] < len(run["steps"]):
            _log(run["steps"][run["current_step_index"]], "Run cancelled by user.")
        await self._persist(run)
        return run

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            raise OrchestratorError("Unknown run.")
        return run

    async def _persist(self, run: dict[str, Any]) -> None:
        await self._runs_store.upsert(run)
        await self._prune_runs_store()

    async def _prune_runs_store(self) -> None:
        items = await self._runs_store.list()
        if len(items) <= MAX_PERSISTED_RUNS:
            return
        active = [item for item in items if item.get("status") in ACTIVE_RUN_STATUSES]
        finished = sorted(
            (item for item in items if item.get("status") not in ACTIVE_RUN_STATUSES),
            key=lambda item: item.get("started_at") or "",
        )
        keep_finished = max(0, MAX_PERSISTED_RUNS - len(active))
        await self._runs_store.replace_all(active + finished[-keep_finished:])

    async def _drive(self, run_id: str) -> None:
        run = self.runs[run_id]
        try:
            while run["current_step_index"] < len(run["steps"]):
                step = run["steps"][run["current_step_index"]]
                outcome = await self._run_step(run, step)
                if outcome == "continue":
                    run["current_step_index"] += 1
                    await self._persist(run)
                    continue
                if outcome == "merge_pending":
                    run["status"] = "paused_for_merge"
                    await self._persist(run)
                    return
                run["status"] = "paused_failed"
                await self._persist(run)
                return
            run["status"] = "completed"
            run["finished_at"] = now_iso()
            await self._persist(run)
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            run["finished_at"] = now_iso()
            await self._persist(run)
            raise

    async def _run_step(self, run: dict[str, Any], step: dict[str, Any]) -> str:
        if step["status"] not in JENKINS_PHASE_DONE_STATUSES:
            result = await self._run_jenkins_phase(run, step)
            if result == "failed":
                return "failed"

        if step.get("gitlab") and step["status"] not in {"merged", "skipped"}:
            if not run["auto_merge_confirmed"]:
                step["status"] = "waiting_merge_confirm"
                _log(step, "Jenkins succeeded - waiting for user confirmation before merging the MR.")
                await self._persist(run)
                return "merge_pending"
            merge_outcome = await self._run_merge_phase(run, step)
            if merge_outcome == "failed":
                return "failed"

        return "continue"

    async def _run_jenkins_phase(self, run: dict[str, Any], step: dict[str, Any]) -> str:
        max_retries = step["max_retries"] if step["max_retries"] is not None else self.settings.jenkins_max_retries
        if not step.get("retry_enabled", True):
            max_retries = 0
        retry_delay = (
            step["retry_delay_seconds"]
            if step.get("retry_delay_seconds") is not None
            else self.settings.jenkins_retry_delay_seconds
        )

        # Resume support: this process may have restarted while this step's
        # Jenkins queue item / build was in flight. Reconnect to whatever was
        # already captured instead of triggering a duplicate build.
        build: dict[str, Any] | None = None
        if step["status"] == "running" and step.get("build_url"):
            build = {"number": step.get("build_number"), "url": step["build_url"]}
            _log(step, f"Reconnecting to build #{build['number']} after a restart.")
        elif step["status"] == "queued" and step.get("queue_url"):
            _log(step, "Reconnecting to a queued build after a restart.")
            try:
                build = await self._await_queue(step["queue_url"])
            except OrchestratorError:
                build = None  # the queue item aged out - fall through and retrigger

        while True:
            if build is None:
                step["status"] = "queued"
                step["attempt"] += 1
                step["error"] = None
                step["started_at"] = now_iso()
                await self._persist(run)
                try:
                    if step.get("wait_for_existing_build"):
                        _log(step, "Checking whether this job already has a build running.")
                        build = await self.jenkins.running_build(step["jenkins_job_url"])
                        if build:
                            _log(step, f"Found build #{build['number']} already running - reusing it instead of triggering a new one.")
                    if build is None:
                        _log(step, "Triggering a new Jenkins build.")
                        queue_url = await self.jenkins.trigger_build(step["jenkins_job_url"], step.get("build_params"))
                        step["queue_url"] = queue_url
                        _log(step, f"Build queued: {queue_url}")
                        await self._persist(run)
                        build = await self._await_queue(queue_url)
                        if build:
                            _log(step, f"Build #{build['number']} started: {build['url']}")
                except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
                    step["error"] = str(exc)
                    _log(step, f"Error triggering/queuing the build: {exc}")

            if build is None:
                step["error"] = step["error"] or "Build was cancelled while queued in Jenkins."
                step["status"] = "failed"
                if not step["error"].startswith("Error"):
                    _log(step, step["error"])
                await self._persist(run)
                if step["attempt"] <= max_retries:
                    _log(step, f"Retrying in {retry_delay}s (attempt {step['attempt'] + 1} of {max_retries + 1}).")
                    await asyncio.sleep(retry_delay)
                    continue
                _log(step, "Giving up - no more retries.")
                return "failed"

            step["build_number"] = build["number"]
            step["build_url"] = build["url"]
            step["status"] = "running"
            await self._persist(run)
            try:
                result = await self._await_result(build["url"])
            except Exception as exc:  # noqa: BLE001
                step["error"] = str(exc)
                step["status"] = "failed"
                _log(step, f"Error while polling build #{build['number']}: {exc}")
                build = None
                await self._persist(run)
                if step["attempt"] <= max_retries:
                    _log(step, f"Retrying in {retry_delay}s (attempt {step['attempt'] + 1} of {max_retries + 1}).")
                    await asyncio.sleep(retry_delay)
                    continue
                _log(step, "Giving up - no more retries.")
                return "failed"

            step["result"] = result
            if result == "SUCCESS":
                step["status"] = "success"
                step["finished_at"] = now_iso()
                _log(step, f"Build #{build['number']} succeeded.")
                try:
                    git_info = await self.jenkins.build_git_info(build["url"], step["jenkins_job_url"])
                    if git_info:
                        step["git_branch"] = git_info["branch"]
                        step["git_commit"] = git_info["commit"]
                        _log(step, f"Built {git_info['branch']} @ {git_info['commit'][:10]}.")
                except Exception as exc:  # noqa: BLE001 - informational only, never fails the step
                    _log(step, f"Could not determine the exact branch/commit built: {exc}")
                await self._persist(run)
                return "success"

            step["error"] = f"Jenkins build result: {result}"
            step["status"] = "failed"
            _log(step, f"Build #{build['number']} finished with result {result}.")
            build = None
            await self._persist(run)
            if step["attempt"] <= max_retries:
                _log(step, f"Retrying in {retry_delay}s (attempt {step['attempt'] + 1} of {max_retries + 1}).")
                await asyncio.sleep(retry_delay)
                continue
            _log(step, "Giving up - no more retries.")
            return "failed"

    async def _await_queue(self, queue_url: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.settings.jenkins_max_wait_minutes * 60
        while time.monotonic() < deadline:
            data = await self.jenkins.queue_item(queue_url)
            if data.get("cancelled"):
                return None
            executable = data.get("executable")
            if executable:
                return executable
            await asyncio.sleep(self.settings.jenkins_poll_seconds)
        raise OrchestratorError("Timed out waiting for the build to leave the Jenkins queue.")

    async def _await_result(self, build_url: str) -> str:
        deadline = time.monotonic() + self.settings.jenkins_max_wait_minutes * 60
        while time.monotonic() < deadline:
            data = await self.jenkins.build_status(build_url)
            result = data.get("result")
            if result:
                return result
            await asyncio.sleep(self.settings.jenkins_poll_seconds)
        raise OrchestratorError("Timed out waiting for the Jenkins build to finish.")

    async def _run_merge_phase(self, run: dict[str, Any], step: dict[str, Any]) -> str:
        gitlab_cfg = step["gitlab"]
        step["status"] = "merging"
        _log(step, f"Attempting to merge MR !{gitlab_cfg['mr_iid']} ({gitlab_cfg['project_path']}).")
        await self._persist(run)

        # Fetched purely for diagnostics: GitLab's merge endpoint returns a bare
        # "405 Method Not Allowed" with no reason when the MR isn't mergeable
        # (its own pipeline hasn't passed, unresolved threads, conflicts, work
        # in progress, ...). Checking merge_status first turns that into an
        # actual answer instead of a guess - this is a *different* gate from
        # the Jenkins job that just succeeded, and merging never waits on it.
        mr_snapshot = None
        try:
            mr_snapshot = await self.gitlab.get_merge_request(
                gitlab_cfg["origin"], gitlab_cfg["project_path"], gitlab_cfg["mr_iid"]
            )
            status = mr_snapshot.get("detailed_merge_status") or mr_snapshot.get("merge_status")
            _log(
                step,
                f"MR state: {mr_snapshot.get('state')}, merge_status: {status}"
                f"{', draft' if mr_snapshot.get('draft') else ''}"
                f"{', has_conflicts' if mr_snapshot.get('has_conflicts') else ''}.",
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics only, never blocks the merge attempt
            _log(step, f"Could not fetch MR status before merging: {exc}")

        sha = step.get("git_commit") if step.get("verify_commit_before_merge", True) else None
        if sha:
            _log(step, f"Pinning the merge to the tested commit {sha[:10]} (fails safely if the branch moved since).")

        try:
            merge_info = await self.gitlab.merge_merge_request(
                gitlab_cfg["origin"], gitlab_cfg["project_path"], gitlab_cfg["mr_iid"], sha=sha
            )
            step["status"] = "merged"
            step["merge_result"] = {
                "merged_at": now_iso(),
                "sha": merge_info.get("merge_commit_sha") or merge_info.get("sha"),
            }
            _log(step, f"Merged (commit {step['merge_result']['sha']}).")
            await self._persist(run)

            if step.get("wait_for_target_branch_update", True):
                target_branch = (mr_snapshot or {}).get("target_branch") or merge_info.get("target_branch")
                await self._wait_for_target_branch(
                    step,
                    gitlab_cfg["origin"],
                    gitlab_cfg["project_path"],
                    target_branch,
                    step["merge_result"]["sha"],
                )
                await self._persist(run)

            return "merged"
        except Exception as exc:  # noqa: BLE001
            step["status"] = "merge_failed"
            hint = ""
            if mr_snapshot is not None:
                status = mr_snapshot.get("detailed_merge_status") or mr_snapshot.get("merge_status")
                if status and status not in {"can_be_merged", "mergeable"}:
                    hint = (
                        f" GitLab's own merge_status is '{status}' - this is a separate gate from "
                        "your Jenkins job's result, and is most likely why the merge was refused. "
                        "Check the MR page in GitLab for what it's waiting on."
                    )
            step["error"] = f"GitLab merge failed: {exc}.{hint}"
            _log(step, step["error"])
            await self._persist(run)
            return "failed"

    async def _wait_for_target_branch(
        self, step: dict[str, Any], origin: str, project_path: str, target_branch: str | None, merge_sha: str | None
    ) -> None:
        """Best-effort confirmation that the target branch actually advanced to
        the merge commit before the chain moves on to a job that builds off it.

        GitLab merges synchronously in the common case, so this is mostly a
        safety margin against replication lag - never a hard gate: if it can't
        confirm within the timeout, or the check itself fails, we log it and
        let the chain continue rather than getting stuck on a diagnostic step.
        """
        if not target_branch or not merge_sha:
            return
        _log(step, f"Waiting for '{target_branch}' to reflect the merge before continuing.")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                branch = await self.gitlab.get_branch(origin, project_path, target_branch)
            except Exception as exc:  # noqa: BLE001
                _log(step, f"Could not confirm '{target_branch}' was updated: {exc}")
                return
            head = (branch.get("commit") or {}).get("id", "")
            if head and (head == merge_sha or head.startswith(merge_sha) or merge_sha.startswith(head)):
                _log(step, f"'{target_branch}' now at {head[:10]} - confirmed.")
                return
            await asyncio.sleep(2)
        _log(step, f"Timed out waiting for '{target_branch}' to reflect the merge - continuing anyway.")
