"""Local FastAPI UI for the Jira Friday status and hygiene report."""

from __future__ import annotations

import copy
import csv
import html
import io
import json
import logging
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from credential_store import (
    credential_metadata,
    delete_credentials,
    delete_gitlab_credentials,
    gitlab_credential_metadata,
    load_credentials,
    load_gitlab_credentials,
    save_credentials,
    save_gitlab_credentials,
)
from gitlab_client import GitLabClient, GitLabError, normalize_group_url
from gitlab_sync import (
    build_mr_comment,
    enrich_issues_with_jira_comment_links,
    enrich_orphans_with_notes,
    fetch_relevant_merge_requests,
    normalize_merge_request,
    summarize_sync,
    sync_report,
)
from jira_weekly_report import (
    FieldResolver,
    JiraClient,
    JiraError,
    ReportError,
    build_jql,
    flatten_text,
    jira_name,
    load_json,
    normalize_jira_base_url,
    run_assignment_report,
    run_report,
    sprint_jql_value,
)
from jira_weekly_report import _text_to_adf as text_to_adf
from sd_ticket_analysis import run_sd_ticket_analysis


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "jira_weekly_config.json"
STATIC_DIR = ROOT / "static"
CONFIG = load_json(CONFIG_PATH)
configured_output = Path(str(CONFIG.get("output_dir", "reports")))
REPORTS_DIR = (
    configured_output
    if configured_output.is_absolute()
    else (ROOT / configured_output)
).resolve()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
STANDUPS_DIR = (ROOT / "standups").resolve()
STANDUPS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("jira-report-ui")

app = FastAPI(
    title="Jira Weekly Status Report",
    description="Read-only sprint status and Jira hygiene reporting.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")


class ReportRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None
    sprint: str = Field(default="", max_length=200)
    jql: str = Field(default="", max_length=5000)
    report_type: Literal["combined", "sprint", "hygiene"] = "combined"
    save_token: bool = True
    worklog_start: date | None = None
    worklog_end: date | None = None


class SprintsRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None


class StandupRosterRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None
    sprint: str = Field(default="", max_length=200)
    team_members: list[str] = Field(default_factory=list)


class StandupTicketUpdate(BaseModel):
    key: str = Field(default="", max_length=50)
    summary: str = Field(default="", max_length=500)
    status: str = Field(default="", max_length=100)
    update: str = Field(default="", max_length=2000)


class StandupEntry(BaseModel):
    person_id: str = Field(default="", max_length=200)
    person: str = Field(default="", max_length=200)
    tickets: list[StandupTicketUpdate] = Field(default_factory=list)
    blockers: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=4000)


class StandupRequest(BaseModel):
    standup_date: date
    sprint: str = Field(default="", max_length=200)
    entries: list[StandupEntry] = Field(default_factory=list)


class GitLabCredentialsRequest(BaseModel):
    group_url: str = Field(min_length=8, max_length=500)
    token: SecretStr = Field(...)
    save_token: bool = True


class GitLabWriteActionsRequest(BaseModel):
    enabled: bool


class GitLabSyncIssue(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    missing_keys: list[str] = Field(default_factory=list)
    status_category: str = ""


class GitLabSyncRequest(BaseModel):
    issues: list[GitLabSyncIssue] = Field(default_factory=list)
    group_url: str = Field(default="", max_length=500)
    token: SecretStr | None = None
    save_token: bool = True
    jira_base_url: str = Field(default="", max_length=500)
    jira_api_mode: Literal["auto", "cloud", "server"] = "auto"
    jira_auth_type: Literal["auto", "bearer", "basic"] = "auto"
    jira_username: str = Field(default="", max_length=300)


class GitLabSyncCommentRequest(BaseModel):
    issue_key: str = Field(min_length=1, max_length=50)
    project_id: int
    iid: int
    jira_base_url: str = Field(min_length=8, max_length=500)
    jira_api_mode: Literal["auto", "cloud", "server"] = "auto"
    jira_auth_type: Literal["auto", "bearer", "basic"] = "auto"
    jira_username: str = Field(default="", max_length=300)
    jira_token: SecretStr | None = None
    group_url: str = Field(default="", max_length=500)
    gitlab_token: SecretStr | None = None


class BulkStoryRow(BaseModel):
    person: str = Field(min_length=1, max_length=200)
    heading: str = Field(min_length=1, max_length=500)
    epic_link: str = Field(default="", max_length=100)
    story_points: int = Field(default=3, ge=1, le=200)
    description: str = Field(default="", max_length=8000)
    acceptance_criteria: str = Field(default="", max_length=8000)


class BulkCreateRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None
    save_token: bool = True
    project_key: str = Field(min_length=1, max_length=30)
    issue_type: str = Field(default="Story", max_length=60)
    max_points_per_story: int = Field(default=5, ge=1, le=20)
    stories: list[BulkStoryRow] = Field(default_factory=list, max_length=300)


class AssignmentRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None
    current_sprint: str = Field(default="", max_length=200)
    jql: str = Field(default="", max_length=5000)
    save_token: bool = True


class SdAnalysisRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_mode: Literal["auto", "cloud", "server"] = "auto"
    auth_type: Literal["auto", "bearer", "basic"] = "auto"
    username: str = Field(default="", max_length=300)
    token: SecretStr | None = None
    save_token: bool = True
    tickets: str = Field(default="", max_length=20000)


def configured_base_url() -> str:
    value = str(CONFIG.get("base_url", "")).strip()
    if "your-" in value or "example." in value:
        return ""
    return value


def configured_project_key() -> str:
    for key in ("jql_template", "assignment_jql", "jql"):
        template = str(CONFIG.get(key, ""))
        match = re.search(r"project\s*=\s*\"?([A-Za-z][A-Za-z0-9_]*)\"?", template)
        if match:
            return match.group(1)
    return ""


def _resolve_report_credentials(
    base_url: str,
    api_mode: str,
    auth_type: str,
    username: str,
    provided_token: str,
) -> tuple[dict, dict]:
    """Build a local config + credentials dict the same way /api/report does."""
    requested_base_url = normalize_jira_base_url(base_url)
    saved = load_credentials()
    saved_matches = bool(
        saved
        and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = provided_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        message = "Enter a Jira API token or PAT."
        if saved and not saved_matches:
            message = "The saved token belongs to another Jira URL. Enter the token for this Jira site."
        raise HTTPException(status_code=422, detail=message)

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = auth_type
    resolved_username = username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )
    credentials = {"username": resolved_username, "token": token}
    return local_config, credentials


def _boards_for_project(client: JiraClient, project_key: str) -> list[dict]:
    # Prefer Scrum boards, since Kanban boards don't support the sprint endpoint
    # and would otherwise fail every lookup with "The board doesn't support sprints".
    scrum_response = client.request(
        "GET",
        f"/rest/agile/1.0/board?projectKeyOrId={quote(project_key)}&type=scrum",
    )
    boards = [b for b in scrum_response.get("values", []) if isinstance(b, dict)]
    if boards:
        return boards
    all_response = client.request(
        "GET", f"/rest/agile/1.0/board?projectKeyOrId={quote(project_key)}"
    )
    return [b for b in all_response.get("values", []) if isinstance(b, dict)]


def _fetch_active_and_future_sprints(local_config: dict, credentials: dict) -> list[dict]:
    client = JiraClient(local_config, credentials=credentials)
    project_key = configured_project_key()
    boards: list[dict] = []
    if project_key:
        boards = _boards_for_project(client, project_key)
    if not boards:
        raise JiraError(
            "No Jira board was found for the configured project. "
            "Check the project key in jql_template."
        )
    sprints_by_id: dict[int, dict] = {}
    last_error: JiraError | None = None
    boards_checked = 0
    for board in boards:
        board_id = board.get("id")
        if board_id is None:
            continue
        boards_checked += 1
        start_at = 0
        try:
            while True:
                page = client.request(
                    "GET",
                    f"/rest/agile/1.0/board/{board_id}/sprint?"
                    f"state=active,future&startAt={start_at}&maxResults=50",
                )
                for sprint in page.get("values", []):
                    if not isinstance(sprint, dict) or sprint.get("id") is None:
                        continue
                    sprints_by_id[sprint["id"]] = {
                        "id": sprint["id"],
                        "name": str(sprint.get("name", "")),
                        "state": str(sprint.get("state", "")).capitalize(),
                    }
                if page.get("isLast", True):
                    break
                start_at += len(page.get("values", []) or [])
                if not page.get("values"):
                    break
        except JiraError as exc:
            # This board (often a Kanban board) doesn't support sprints. Skip it
            # and keep checking the other boards for the project instead of failing.
            last_error = exc
            continue
    if not sprints_by_id and last_error and boards_checked:
        raise last_error
    return sorted(sprints_by_id.values(), key=lambda s: s["id"])


def gitlab_section() -> dict:
    section = CONFIG.get("gitlab", {})
    return section if isinstance(section, dict) else {}


def configured_gitlab_group_url() -> str:
    value = str(gitlab_section().get("group_url", "")).strip()
    if "your-" in value or "example." in value:
        return ""
    return value


def gitlab_comments_allowed() -> bool:
    return bool(gitlab_section().get("allow_comments", False))


def gitlab_team_usernames() -> set[str]:
    """Casefolded GitLab usernames to scope MR matching to, or empty for no filter."""
    if not bool(gitlab_section().get("filter_to_team_members", True)):
        return set()
    return {
        str(item).strip().casefold()
        for item in CONFIG.get("team_members", [])
        if str(item).strip()
    }


def save_gitlab_allow_comments(enabled: bool) -> None:
    fresh = load_json(CONFIG_PATH)
    section = fresh.setdefault("gitlab", {})
    section["allow_comments"] = enabled
    CONFIG_PATH.write_text(
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    CONFIG["gitlab"] = section


def _check_candidates(config: dict, key: str) -> list[str]:
    """Field name/ID candidates already configured for a given hygiene check
    (e.g. 'epic_parent', 'story_points'), reused so bulk creation maps to the
    same custom fields the weekly report already knows about."""
    for raw in config.get("checks", []):
        if isinstance(raw, dict) and raw.get("key") == key:
            return list(raw.get("field_ids", [])) + list(raw.get("field_names", []))
    return []


def _split_story_points(total: int, max_points: int) -> list[int]:
    """Split a story's points into chunks of at most max_points each,
    distributed as evenly as possible."""
    if total <= max_points:
        return [total]
    parts = math.ceil(total / max_points)
    base, remainder = divmod(total, parts)
    return [base + 1] * remainder + [base] * (parts - remainder)


def _expand_bulk_rows(stories: list[BulkStoryRow], max_points: int) -> list[dict[str, Any]]:
    """Turn each submitted row into one or more issue rows, splitting any
    story above max_points_per_story and suffixing the title with its part."""
    expanded: list[dict[str, Any]] = []
    for row in stories:
        chunks = _split_story_points(row.story_points, max_points)
        total_parts = len(chunks)
        heading = row.heading.strip()
        for index, points in enumerate(chunks, start=1):
            title = f"{heading} (Part {index} of {total_parts})" if total_parts > 1 else heading
            expanded.append(
                {
                    "person": row.person.strip(),
                    "heading": title,
                    "original_heading": heading,
                    "epic_link": row.epic_link.strip(),
                    "story_points": points,
                    "description": row.description.strip(),
                    "acceptance_criteria": row.acceptance_criteria.strip(),
                    "part": index if total_parts > 1 else 0,
                    "total_parts": total_parts,
                }
            )
    return expanded


def _pick_user_match(
    users: list[dict[str, Any]], person: str, id_key: str
) -> dict[str, str] | None:
    """A confident match only: an exact (case-insensitive) display-name hit,
    or a single unambiguous search result. Never guess between candidates —
    that risks silently assigning the wrong person."""
    exact = [
        user for user in users
        if str(user.get("displayName", "")).strip().casefold() == person.casefold()
    ]
    if len(exact) == 1:
        return {id_key: exact[0].get(id_key)}
    if len(users) == 1:
        return {id_key: users[0].get(id_key)}
    return None


def _resolve_assignee_field(client: JiraClient, person: str) -> tuple[dict[str, str] | None, str]:
    """Look up the real Jira user behind a display name (e.g. 'Amir'), since
    Jira's assignee field needs an accountId (Cloud) or the actual username/
    SSO ID (Data Center/Server) — not the display name someone types.
    Returns (field, note); note is set when we couldn't confirm a unique
    match, in which case the issue is left unassigned rather than guessed."""
    person = person.strip()
    if not person:
        return None, ""

    if client.mode == "cloud":
        try:
            query = urlencode({"query": person})
            matches = client.request("GET", f"/rest/api/3/user/search?{query}")
        except JiraError:
            return None, f"Could not look up Jira user '{person}'; created unassigned."
        users = matches if isinstance(matches, list) else []
        field = _pick_user_match(users, person, "accountId")
        if field:
            return field, ""
        return None, f"No confident Jira user match for '{person}'; created unassigned."

    # Data Center / Server: the assignee picker matches on display name,
    # username, and email, and returns the real username ('name') to assign.
    users: list[dict[str, Any]] = []
    try:
        query = urlencode({"query": person})
        picker = client.request("GET", f"/rest/api/2/user/picker?{query}")
        users = picker.get("users", []) if isinstance(picker, dict) else []
    except JiraError:
        users = []
    if not users:
        try:
            query = urlencode({"username": person})
            found = client.request("GET", f"/rest/api/2/user/search?{query}")
            users = found if isinstance(found, list) else []
        except JiraError:
            users = []
    field = _pick_user_match(users, person, "name")
    if field:
        return field, ""
    return None, f"No confident Jira user match for '{person}'; created unassigned."


def _resolve_epic_field_id(resolver: FieldResolver, epic_candidates: list[str]) -> str | None:
    """Prefer a dedicated custom field ('Epic Link', 'Parent Link', ...) —
    that's what classic/company-managed projects use to link a story to an
    epic. Jira's built-in 'parent' field always resolves (it's a global
    field id), but on those same projects it's reserved for sub-tasks and
    Jira rejects it for any other issue type ("Issue type X is not a
    sub-task but a parent is specified"). Only fall back to 'parent' when
    no dedicated field is configured, which is the case on team-managed
    (next-gen) projects that really do use 'parent' for epic links too."""
    named_candidates = [
        candidate for candidate in epic_candidates
        if str(candidate).strip().casefold() != "parent"
    ]
    resolved = [
        field_id for field_id in resolver.resolve(named_candidates) if field_id != "parent"
    ]
    if resolved:
        return resolved[0]
    if any(str(candidate).strip().casefold() == "parent" for candidate in epic_candidates):
        return "parent"
    return None


def _build_issue_payload(
    client: JiraClient,
    resolver: FieldResolver,
    epic_candidates: list[str],
    story_point_candidates: list[str],
    project_key: str,
    issue_type: str,
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": row["heading"],
        "issuetype": {"name": issue_type},
    }

    description_text = row["description"]
    if row["acceptance_criteria"]:
        ac_lines = "\n".join(
            line for line in row["acceptance_criteria"].splitlines() if line.strip()
        )
        description_text = (
            f"{description_text}\n\nAcceptance Criteria:\n{ac_lines}".strip()
            if description_text
            else f"Acceptance Criteria:\n{ac_lines}"
        )
    if description_text:
        fields["description"] = (
            text_to_adf(description_text) if client.api_version == 3 else description_text
        )

    assignee_field, assignee_note = _resolve_assignee_field(client, row["person"])
    if assignee_field:
        fields["assignee"] = assignee_field
    elif assignee_note:
        notes.append(assignee_note)

    if row["epic_link"]:
        field_id = _resolve_epic_field_id(resolver, epic_candidates)
        if field_id:
            fields[field_id] = (
                {"key": row["epic_link"]} if field_id == "parent" else row["epic_link"]
            )

    story_point_field_ids = resolver.resolve(story_point_candidates)
    if story_point_field_ids:
        fields[story_point_field_ids[0]] = row["story_points"]

    return {"fields": fields}, notes


_INVALID_FIELD_PATTERN = re.compile(r"[Ff]ield '([\w.]+)' (?:cannot be set|is not on|is unknown)")
_PARENT_NOT_SUBTASK_PATTERN = re.compile(
    r"is not a sub-task but a parent is specified", re.IGNORECASE
)
_USER_NOT_FOUND_PATTERN = re.compile(r"User '[^']+' does not exist", re.IGNORECASE)
_UNDROPPABLE_FIELDS = {"project", "summary", "issuetype"}


def _extract_invalid_field_ids(message: str, fields: dict[str, Any]) -> list[str]:
    """Field IDs Jira rejected as not present on the create screen (or
    otherwise unknown/misused for this project/issue type), limited to
    fields we actually set and are safe to drop."""
    found = list(_INVALID_FIELD_PATTERN.findall(message))
    if _PARENT_NOT_SUBTASK_PATTERN.search(message) and "parent" in fields:
        # Falls back on this when a project has no dedicated Epic Link
        # field and 'parent' turns out to still be wrong for this issue type.
        found.append("parent")
    if _USER_NOT_FOUND_PATTERN.search(message) and "assignee" in fields:
        # Our own user lookup missed (or the account has since changed);
        # drop the assignee rather than fail the whole issue.
        found.append("assignee")
    return [
        field_id
        for field_id in dict.fromkeys(found)
        if field_id in fields and field_id not in _UNDROPPABLE_FIELDS
    ]


def _create_bulk_issues(
    client: JiraClient,
    config: dict[str, Any],
    project_key: str,
    issue_type: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolver = FieldResolver(client.get_fields())
    epic_candidates = _check_candidates(config, "epic_parent")
    story_point_candidates = _check_candidates(config, "story_points")

    results: list[dict[str, Any]] = []
    for row in rows:
        payload, notes = _build_issue_payload(
            client, resolver, epic_candidates, story_point_candidates,
            project_key, issue_type, row,
        )
        dropped_fields: list[str] = []
        last_error: JiraError | None = None
        created_key = ""

        # Some projects' create screens don't expose every custom field
        # (commonly Story Points or Epic Link), and an assignee lookup can
        # occasionally still be rejected by Jira even after our own check.
        # Rather than fail the whole row, drop whichever field Jira names
        # and retry, up to a handful of times in case more than one is off.
        for _ in range(6):
            try:
                response = client.request(
                    "POST", f"/rest/api/{client.api_version}/issue", payload
                )
                created_key = str(response.get("key", "")) if isinstance(response, dict) else ""
                last_error = None
                break
            except JiraError as exc:
                last_error = exc
                invalid_fields = (
                    _extract_invalid_field_ids(str(exc), payload["fields"])
                    if exc.status == 400
                    else []
                )
                if not invalid_fields:
                    break
                for field_id in invalid_fields:
                    payload["fields"].pop(field_id, None)
                    dropped_fields.append(field_id)
                    if field_id == "assignee":
                        notes.append("Jira rejected the assignee; created unassigned.")
                    elif field_id == "parent":
                        notes.append("Could not link the epic on this project; set it manually.")
                    else:
                        notes.append(
                            f"Created, but this project's screen doesn't show '{field_id}'. Set manually if needed."
                        )

        if created_key:
            results.append(
                {
                    **row,
                    "status": "created",
                    "issue_key": created_key,
                    "issue_url": f"{client.base_url}/browse/{created_key}",
                    "error": " ".join(dict.fromkeys(notes)),
                }
            )
        else:
            results.append(
                {
                    **row,
                    "status": "error",
                    "issue_key": "",
                    "issue_url": "",
                    "error": str(last_error) if last_error else "Jira did not return an issue key.",
                }
            )
    return results


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/assignments", include_in_schema=False)
async def assignments_page() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "assignments.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/bulk-create", include_in_schema=False)
async def bulk_create_page() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "bulk_create.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/standup", include_in_schema=False)
async def standup_page() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "standup.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/sd-analysis", include_in_schema=False)
async def sd_analysis_page() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sd_analysis.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def ui_config() -> dict:
    sprint = str(CONFIG.get("sprint", "")).strip()
    try:
        default_jql = build_jql(CONFIG, sprint or None, None)
    except ReportError:
        default_jql = str(CONFIG.get("jql", "")).strip()
    return {
        "base_url": configured_base_url(),
        "api_mode": str(CONFIG.get("api_mode", "auto")),
        "sprint": sprint,
        "jql": default_jql,
        "assignment_jql": str(CONFIG.get("assignment_jql", "")).strip(),
        "team_members": [str(member) for member in CONFIG.get("team_members", [])],
        "saved_credentials": credential_metadata(),
    }


@app.delete("/api/credentials")
async def forget_credentials() -> dict[str, bool]:
    return {"deleted": delete_credentials()}


@app.post("/api/sprints")
async def list_sprints(request: SprintsRequest) -> dict:
    provided_token = (
        request.token.get_secret_value().strip() if request.token else ""
    )
    local_config, credentials = _resolve_report_credentials(
        request.base_url,
        request.api_mode,
        request.auth_type,
        request.username,
        provided_token,
    )
    try:
        sprints = await run_in_threadpool(
            _fetch_active_and_future_sprints, local_config, credentials
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # Keep credentials and internal traces out of the UI.
        logger.exception("Unexpected sprint lookup failure")
        raise HTTPException(
            status_code=500,
            detail="Sprints could not be loaded. Check the server window for details.",
        ) from exc
    return {"sprints": sprints}


def _standup_jql(project_key: str, sprint_value: str, member_ids: list[str]) -> str:
    clauses = [f'project = "{project_key}"', f"Sprint = {sprint_jql_value(sprint_value)}"]
    if member_ids:
        quoted = ", ".join('"' + m.replace('"', '\\"') + '"' for m in member_ids)
        clauses.append(f"assignee in ({quoted})")
    return " AND ".join(clauses) + " ORDER BY assignee ASC, updated DESC"


def _load_standup_roster(
    local_config: dict, credentials: dict, sprint_value: str, member_ids: list[str]
) -> list[dict]:
    client = JiraClient(local_config, credentials=credentials)
    project_key = configured_project_key()
    if not project_key:
        raise ReportError("Could not determine the Jira project key from jql_template.")
    jql = _standup_jql(project_key, sprint_value, member_ids)
    issues = client.search(jql, ["summary", "assignee", "status"])
    id_field = "accountId" if client.mode == "cloud" else "name"
    grouped: dict[str, dict] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        fields = issue.get("fields", {}) or {}
        assignee = fields.get("assignee")
        identifier = ""
        display_name = "Unassigned"
        if isinstance(assignee, dict):
            identifier = str(
                assignee.get(id_field) or assignee.get("accountId") or assignee.get("name") or ""
            )
            display_name = jira_name(assignee, fallback=identifier or "Unassigned")
        group_key = identifier or display_name
        entry = grouped.setdefault(
            group_key, {"id": identifier, "display_name": display_name, "issues": []}
        )
        entry["issues"].append(
            {
                "key": str(issue.get("key", "")),
                "summary": flatten_text(fields.get("summary")) or "(No summary)",
                "status": jira_name(fields.get("status"), fallback=""),
            }
        )
    for member_id in member_ids:
        if member_id not in grouped:
            grouped[member_id] = {"id": member_id, "display_name": member_id, "issues": []}
    return sorted(grouped.values(), key=lambda item: item["display_name"].casefold())


@app.post("/api/standup-roster")
async def standup_roster(request: StandupRosterRequest) -> dict:
    provided_token = (
        request.token.get_secret_value().strip() if request.token else ""
    )
    local_config, credentials = _resolve_report_credentials(
        request.base_url,
        request.api_mode,
        request.auth_type,
        request.username,
        provided_token,
    )
    sprint_value = request.sprint.strip() or str(CONFIG.get("sprint", "")).strip()
    if not sprint_value:
        raise HTTPException(status_code=422, detail="Select a sprint first.")
    member_ids = [m.strip() for m in request.team_members if m.strip()]
    try:
        roster = await run_in_threadpool(
            _load_standup_roster, local_config, credentials, sprint_value, member_ids
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unexpected standup roster failure")
        raise HTTPException(
            status_code=500,
            detail="The sprint roster could not be loaded. Check the server window for details.",
        )
    return {"sprint": sprint_value, "roster": roster}


def _standup_path(standup_date: str) -> Path:
    safe_date = re.sub(r"[^0-9-]", "", standup_date) or "unknown"
    return STANDUPS_DIR / f"{safe_date}.json"


@app.get("/api/standup")
async def get_standup(standup_date: str) -> dict:
    path = _standup_path(standup_date)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"standup_date": standup_date, "entries": []}


def _jira_issue_link(base_url: str, key: str) -> str:
    escaped_key = html.escape(key)
    if not base_url or not key:
        return escaped_key
    return (
        f'<a href="{html.escape(base_url)}/browse/{quote(key)}" '
        f'target="_blank" rel="noopener">{escaped_key}</a>'
    )


def _render_standup_html(payload: dict) -> str:
    base_url = configured_base_url()
    standup_date = html.escape(str(payload.get("standup_date", "")))
    generated_at = html.escape(str(payload.get("generated_at", "")))
    sprint = html.escape(str(payload.get("sprint", "")))
    blocks = []
    for entry in payload.get("entries", []):
        person = html.escape(str(entry.get("person", "")).strip() or "Unassigned")
        tickets = entry.get("tickets", []) or []
        ticket_rows = []
        for ticket in tickets:
            key = str(ticket.get("key", "")).strip()
            summary = html.escape(str(ticket.get("summary", "")).strip() or "—")
            status = html.escape(str(ticket.get("status", "")).strip() or "—")
            update = html.escape(str(ticket.get("update", "")).strip() or "—").replace("\n", "<br>")
            ticket_rows.append(
                "<tr>"
                f"<td>{_jira_issue_link(base_url, key) if key else '—'}</td>"
                f"<td>{summary}</td>"
                f"<td>{status}</td>"
                f"<td>{update}</td>"
                "</tr>"
            )
        ticket_table = (
            "<table class=\"tickets\"><thead><tr><th>Jira ID</th><th>Title</th>"
            "<th>Status</th><th>Update</th></tr></thead><tbody>"
            + ("\n".join(ticket_rows) or "<tr><td colspan=\"4\">No tickets in this sprint.</td></tr>")
            + "</tbody></table>"
        )
        blockers_raw = str(entry.get("blockers", "")).strip()
        blockers = html.escape(blockers_raw or "None").replace("\n", "<br>")
        notes = html.escape(str(entry.get("notes", "")).strip() or "—").replace("\n", "<br>")
        blocker_class = " has-blocker" if blockers_raw else ""
        blocks.append(
            f'<section class="person-block{blocker_class}">'
            f"<h2>{person}</h2>"
            f"{ticket_table}"
            f'<p><strong>Blockers:</strong> {blockers}</p>'
            f'<p><strong>Additional details:</strong> {notes}</p>'
            "</section>"
        )
    blocks_html = "\n".join(blocks) or "<p>No entries recorded.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Daily Standup — {standup_date}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 32px; color: #1a1f2b; }}
  h1 {{ margin-bottom: 4px; }}
  h2 {{ margin: 0 0 8px; color: #102a43; }}
  p.meta {{ color: #667; margin-top: 0; }}
  section.person-block {{ border: 1px solid #d8dce3; border-radius: 10px; padding: 16px; margin-top: 16px; }}
  section.person-block.has-blocker {{ border-color: #b42318; }}
  table.tickets {{ border-collapse: collapse; width: 100%; margin-top: 6px; }}
  table.tickets th, table.tickets td {{ border: 1px solid #d8dce3; padding: 8px 10px; text-align: left; vertical-align: top; font-size: 13px; }}
  table.tickets th {{ background: #f3f5f9; }}
  @media print {{ body {{ margin: 12px; }} }}
</style>
</head>
<body>
  <h1>Daily Standup — {standup_date}</h1>
  <p class="meta">Sprint {sprint} · Generated {generated_at}</p>
  {blocks_html}
</body>
</html>
"""


def _render_standup_csv(payload: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Person", "Jira ID", "Title", "Status", "Update", "Blockers", "Additional details"]
    )
    for entry in payload.get("entries", []):
        person = str(entry.get("person", "")).strip() or "Unassigned"
        blockers = str(entry.get("blockers", "")).strip()
        notes = str(entry.get("notes", "")).strip()
        tickets = entry.get("tickets", []) or []
        if not tickets:
            writer.writerow([person, "", "", "", "", blockers, notes])
            continue
        for index, ticket in enumerate(tickets):
            writer.writerow(
                [
                    person,
                    str(ticket.get("key", "")).strip(),
                    str(ticket.get("summary", "")).strip(),
                    str(ticket.get("status", "")).strip(),
                    str(ticket.get("update", "")).strip(),
                    blockers if index == 0 else "",
                    notes if index == 0 else "",
                ]
            )
    return buffer.getvalue()


@app.post("/api/standup-report")
async def create_standup_report(request: StandupRequest) -> dict:
    standup_date = request.standup_date.isoformat()
    payload = {
        "standup_date": standup_date,
        "sprint": request.sprint.strip(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [entry.model_dump() for entry in request.entries],
    }
    try:
        _standup_path(standup_date).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        html_content = _render_standup_html(payload)
        html_path = REPORTS_DIR / f"standup_report_{standup_date}.html"
        html_path.write_text(html_content, encoding="utf-8")
        csv_content = _render_standup_csv(payload)
        csv_path = REPORTS_DIR / f"standup_report_{standup_date}.csv"
        csv_path.write_text(csv_content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save the standup report: {exc}"
        ) from exc
    return {
        "standup_date": standup_date,
        "sprint": payload["sprint"],
        "generated_at": payload["generated_at"],
        "entries": payload["entries"],
        "download": f"/reports/{html_path.name}",
        "download_csv": f"/reports/{csv_path.name}",
    }


@app.get("/api/gitlab/config")
async def gitlab_config() -> dict:
    return {
        "group_url": configured_gitlab_group_url(),
        "allow_comments": gitlab_comments_allowed(),
        "saved_credentials": gitlab_credential_metadata(),
    }


@app.post("/api/gitlab/credentials")
async def connect_gitlab(request: GitLabCredentialsRequest) -> dict:
    group_url = normalize_group_url(request.group_url)
    token = request.token.get_secret_value().strip()
    try:
        user = await run_in_threadpool(
            lambda: GitLabClient(
                group_url, token, verify_ssl=bool(gitlab_section().get("verify_ssl", True))
            ).current_user()
        )
    except GitLabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    credential_saved = False
    if request.save_token:
        try:
            save_gitlab_credentials({"group_url": group_url, "token": token})
            credential_saved = True
        except (OSError, RuntimeError) as exc:
            logger.warning("GitLab credential saving failed: %s", exc)

    return {
        "status": "connected",
        "group_url": group_url,
        "user": {
            "name": user.get("name") or user.get("username"),
            "username": user.get("username"),
        },
        "credential_saved": credential_saved,
    }


@app.delete("/api/gitlab/credentials")
async def forget_gitlab_credentials() -> dict[str, bool]:
    return {"deleted": delete_gitlab_credentials()}


@app.put("/api/gitlab/write-actions")
async def update_gitlab_write_actions(
    payload: GitLabWriteActionsRequest, request: Request
) -> dict:
    if request.headers.get("X-Jira-Friday-Action") != "gitlab-write-actions":
        raise HTTPException(status_code=400, detail="Write-action confirmation header is missing.")
    try:
        await run_in_threadpool(save_gitlab_allow_comments, payload.enabled)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="The configuration file could not be updated.",
        ) from exc
    return {"allow_comments": payload.enabled}


def _resolve_gitlab_client(group_url: str, token: str) -> tuple[GitLabClient, str]:
    requested_group_url = normalize_group_url(group_url or configured_gitlab_group_url())
    saved = load_gitlab_credentials()
    saved_matches = bool(
        saved and normalize_group_url(saved.get("group_url", "")) == requested_group_url
    )
    resolved_token = token or (saved.get("token", "") if saved_matches else "")
    if not requested_group_url:
        raise HTTPException(status_code=422, detail="Set the GitLab group URL.")
    if not resolved_token:
        message = "Enter a GitLab access token."
        if saved and not saved_matches:
            message = "The saved GitLab token belongs to another group URL. Enter the token for this group."
        raise HTTPException(status_code=422, detail=message)
    try:
        client = GitLabClient(
            requested_group_url,
            resolved_token,
            verify_ssl=bool(gitlab_section().get("verify_ssl", True)),
        )
    except GitLabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return client, requested_group_url


def _build_optional_jira_client(request: "GitLabSyncRequest") -> JiraClient | None:
    """Best-effort: build a JiraClient from saved credentials so the sync can
    also scan Jira comments for pasted GitLab links. Returns None (rather
    than raising) if there's no matching saved token, since this is an
    enhancement on top of branch/title/description matching, not required
    for the sync to work."""
    if not request.jira_base_url:
        return None
    requested_base_url = normalize_jira_base_url(request.jira_base_url)
    saved = load_credentials()
    if not saved or normalize_jira_base_url(saved.get("base_url", "")) != requested_base_url:
        return None
    token = saved.get("token", "")
    if not token:
        return None
    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.jira_api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.jira_auth_type
    username = request.jira_username.strip() or saved.get("username", "")
    try:
        return JiraClient(local_config, {"username": username, "token": token})
    except (ReportError, JiraError) as exc:
        logger.warning("Optional Jira comment-link scan skipped: %s", exc)
        return None


def _run_gitlab_sync(
    client: GitLabClient,
    lookback: int,
    team_usernames: set[str],
    issues: list[dict],
    jira_client: JiraClient | None,
) -> dict:
    merge_requests = fetch_relevant_merge_requests(client, lookback, team_usernames)
    enrich_orphans_with_notes(client, merge_requests)
    result = sync_report(issues, merge_requests)
    if jira_client is not None:
        try:
            enrich_issues_with_jira_comment_links(
                jira_client, issues, merge_requests, result["issue_gitlab"]
            )
            result["summary"] = summarize_sync(
                result["issue_gitlab"], result["orphan_merge_requests"]
            )
        except Exception as exc:  # noqa: BLE001 - never fail sync over this extra
            logger.warning("Jira comment-link scan failed: %s", exc)
    return result


@app.post("/api/gitlab-sync")
async def gitlab_sync(request: GitLabSyncRequest) -> dict:
    token = request.token.get_secret_value().strip() if request.token else ""
    client, group_url = _resolve_gitlab_client(request.group_url, token)

    credential_saved = False
    if request.save_token and token:
        try:
            save_gitlab_credentials({"group_url": group_url, "token": token})
            credential_saved = True
        except (OSError, RuntimeError) as exc:
            logger.warning("GitLab credential saving failed: %s", exc)

    lookback = int(gitlab_section().get("merged_lookback_days", 30))
    team_usernames = gitlab_team_usernames()
    issues = [item.model_dump() for item in request.issues]
    jira_client = await run_in_threadpool(_build_optional_jira_client, request)

    try:
        result = await run_in_threadpool(
            _run_gitlab_sync, client, lookback, team_usernames, issues, jira_client
        )
    except GitLabError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        **result,
        "group_url": group_url,
        "credential_saved": credential_saved,
        "allow_comments": gitlab_comments_allowed(),
        "filtered_to_team": bool(team_usernames),
        "jira_comment_scan_used": jira_client is not None,
    }


@app.post("/api/gitlab-sync/comment")
async def gitlab_sync_comment(request: GitLabSyncCommentRequest, http_request: Request) -> dict:
    if http_request.headers.get("X-Jira-Friday-Action") != "post-comment":
        raise HTTPException(status_code=400, detail="Comment confirmation header is missing.")
    if not gitlab_comments_allowed():
        raise HTTPException(
            status_code=403,
            detail="Posting comments to Jira is disabled. Enable it in GitLab sync settings first.",
        )

    gitlab_token = request.gitlab_token.get_secret_value().strip() if request.gitlab_token else ""
    gitlab_client, _ = _resolve_gitlab_client(request.group_url, gitlab_token)
    try:
        raw_mr = await run_in_threadpool(
            gitlab_client.merge_request, request.project_id, request.iid
        )
    except GitLabError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mr = normalize_merge_request(raw_mr)
    comment_text = build_mr_comment(request.issue_key, mr)

    requested_base_url = normalize_jira_base_url(request.jira_base_url)
    jira_token = request.jira_token.get_secret_value().strip() if request.jira_token else ""
    saved = load_credentials()
    saved_matches = bool(
        saved and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = jira_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        raise HTTPException(status_code=422, detail="Enter a Jira API token or PAT.")
    username = request.jira_username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.jira_api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.jira_auth_type
    credentials = {"username": username, "token": token}

    try:
        client = await run_in_threadpool(JiraClient, local_config, credentials)
        result = await run_in_threadpool(client.add_comment, request.issue_key, comment_text)
    except (ReportError, JiraError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "posted",
        "issue_key": request.issue_key,
        "issue_url": f"{client.base_url}/browse/{request.issue_key}",
        "comment_id": result.get("id") if isinstance(result, dict) else None,
    }


@app.post("/api/report")
async def create_report(request: ReportRequest) -> dict:
    requested_base_url = normalize_jira_base_url(request.base_url)
    provided_token = (
        request.token.get_secret_value().strip() if request.token else ""
    )
    saved = load_credentials()
    saved_matches = bool(
        saved
        and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = provided_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        message = "Enter a Jira API token or PAT."
        if saved and not saved_matches:
            message = "The saved token belongs to another Jira URL. Enter the token for this Jira site."
        raise HTTPException(status_code=422, detail=message)

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.auth_type
    username = request.username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )
    credentials = {
        "username": username,
        "token": token,
    }
    explicit_jql = request.jql.strip() or None
    sprint = request.sprint.strip() or None

    credential_save_succeeded = False
    if request.save_token and provided_token:
        try:
            save_credentials(
                {
                    "base_url": requested_base_url,
                    "username": username,
                    "auth_type": request.auth_type,
                    "token": provided_token,
                }
            )
            credential_save_succeeded = True
        except (OSError, RuntimeError) as exc:
            logger.warning("Credential saving failed: %s", exc)

    try:
        result = await run_in_threadpool(
            run_report,
            local_config,
            config_base_dir=ROOT,
            sprint=sprint,
            explicit_jql=explicit_jql,
            output_dir=REPORTS_DIR,
            credentials=credentials,
            worklog_start=request.worklog_start,
            worklog_end=request.worklog_end,
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # Keep credentials and internal traces out of the UI.
        logger.exception("Unexpected Jira report failure")
        raise HTTPException(
            status_code=500,
            detail="The report could not be completed. Check the server window for details.",
        ) from exc

    downloads = {
        name: f"/reports/{path.name}"
        for name, path in result["paths"].items()
    }
    return {
        "report_date": result["report_date"],
        "generated_at": result["generated_at"],
        "jira_base_url": result["jira_base_url"],
        "api_mode": result["api_mode"],
        "jql": result["jql"],
        "report_type": request.report_type,
        "summary": result["summary"],
        "timesheet": result["timesheet"],
        "worklog_errors": result["worklog_errors"],
        "issues": result["issues"],
        "unresolved": result["unresolved"],
        "downloads": downloads,
        "credential_saved": bool(
            credential_save_succeeded or saved_matches
        ),
    }


@app.post("/api/assignment-report")
async def create_assignment_report(request: AssignmentRequest) -> dict:
    requested_base_url = normalize_jira_base_url(request.base_url)
    provided_token = (
        request.token.get_secret_value().strip() if request.token else ""
    )
    saved = load_credentials()
    saved_matches = bool(
        saved
        and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = provided_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        message = "Enter a Jira API token or PAT."
        if saved and not saved_matches:
            message = "The saved token belongs to another Jira URL. Enter the token for this Jira site."
        raise HTTPException(status_code=422, detail=message)

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.auth_type
    username = request.username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )
    credentials = {"username": username, "token": token}

    credential_save_succeeded = False
    if request.save_token and provided_token:
        try:
            save_credentials(
                {
                    "base_url": requested_base_url,
                    "username": username,
                    "auth_type": request.auth_type,
                    "token": provided_token,
                }
            )
            credential_save_succeeded = True
        except (OSError, RuntimeError) as exc:
            logger.warning("Credential saving failed: %s", exc)

    try:
        result = await run_in_threadpool(
            run_assignment_report,
            local_config,
            config_base_dir=ROOT,
            current_sprint=request.current_sprint.strip(),
            explicit_jql=request.jql.strip() or None,
            output_dir=REPORTS_DIR,
            credentials=credentials,
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected Jira assignment report failure")
        raise HTTPException(
            status_code=500,
            detail="The assignment report could not be completed. Check the server window for details.",
        ) from exc

    return {
        **{key: value for key, value in result.items() if key != "paths"},
        "downloads": {
            name: f"/reports/{path.name}"
            for name, path in result["paths"].items()
        },
        "credential_saved": bool(
            credential_save_succeeded or saved_matches
        ),
    }


@app.post("/api/sd-analysis")
async def create_sd_analysis(request: SdAnalysisRequest) -> dict:
    requested_base_url = normalize_jira_base_url(request.base_url)
    provided_token = (
        request.token.get_secret_value().strip() if request.token else ""
    )
    saved = load_credentials()
    saved_matches = bool(
        saved
        and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = provided_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        message = "Enter a Jira API token or PAT."
        if saved and not saved_matches:
            message = "The saved token belongs to another Jira URL. Enter the token for this Jira site."
        raise HTTPException(status_code=422, detail=message)

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.auth_type
    username = request.username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )
    credentials = {"username": username, "token": token}

    credential_save_succeeded = False
    if request.save_token and provided_token:
        try:
            save_credentials(
                {
                    "base_url": requested_base_url,
                    "username": username,
                    "auth_type": request.auth_type,
                    "token": provided_token,
                }
            )
            credential_save_succeeded = True
        except (OSError, RuntimeError) as exc:
            logger.warning("Credential saving failed: %s", exc)

    try:
        result = await run_in_threadpool(
            run_sd_ticket_analysis,
            local_config,
            raw_input=request.tickets,
            output_dir=REPORTS_DIR,
            credentials=credentials,
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected SD ticket analysis failure")
        raise HTTPException(
            status_code=500,
            detail="The SD ticket analysis could not be completed. Check the server window for details.",
        ) from exc

    return {
        **{key: value for key, value in result.items() if key != "paths"},
        "downloads": {
            name: f"/reports/{path.name}"
            for name, path in result["paths"].items()
        },
        "credential_saved": bool(
            credential_save_succeeded or saved_matches
        ),
    }


@app.post("/api/bulk-create")
async def bulk_create_issues(request: BulkCreateRequest, http_request: Request) -> dict:
    """Create Jira issues in bulk. Unlike the read-only reports, this writes
    to Jira, so it requires the same explicit confirmation header used for
    the optional GitLab comment posting."""
    if http_request.headers.get("X-Jira-Friday-Action") != "bulk-create-issues":
        raise HTTPException(status_code=400, detail="Creation confirmation header is missing.")
    if not request.stories:
        raise HTTPException(status_code=422, detail="Add at least one story before creating tickets.")

    requested_base_url = normalize_jira_base_url(request.base_url)
    provided_token = request.token.get_secret_value().strip() if request.token else ""
    saved = load_credentials()
    saved_matches = bool(
        saved and normalize_jira_base_url(saved.get("base_url", "")) == requested_base_url
    )
    token = provided_token or (saved.get("token", "") if saved_matches else "")
    if not token:
        message = "Enter a Jira API token or PAT."
        if saved and not saved_matches:
            message = "The saved token belongs to another Jira URL. Enter the token for this Jira site."
        raise HTTPException(status_code=422, detail=message)

    local_config = copy.deepcopy(CONFIG)
    local_config["base_url"] = requested_base_url
    local_config["api_mode"] = request.api_mode
    auth = local_config.setdefault("auth", {})
    auth["type"] = request.auth_type
    username = request.username.strip() or (
        saved.get("username", "") if saved_matches and saved else ""
    )
    credentials = {"username": username, "token": token}

    credential_save_succeeded = False
    if request.save_token and provided_token:
        try:
            save_credentials(
                {
                    "base_url": requested_base_url,
                    "username": username,
                    "auth_type": request.auth_type,
                    "token": provided_token,
                }
            )
            credential_save_succeeded = True
        except (OSError, RuntimeError) as exc:
            logger.warning("Credential saving failed: %s", exc)

    try:
        client = await run_in_threadpool(JiraClient, local_config, credentials)
    except (ReportError, JiraError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project_key = request.project_key.strip().upper()
    issue_type = request.issue_type.strip() or "Story"
    expanded_rows = _expand_bulk_rows(request.stories, request.max_points_per_story)

    try:
        results = await run_in_threadpool(
            _create_bulk_issues, client, local_config, project_key, issue_type, expanded_rows
        )
    except JiraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - keep internal traces out of the UI
        logger.exception("Unexpected bulk create failure")
        raise HTTPException(
            status_code=500,
            detail="Bulk creation failed. Check the server window for details.",
        ) from exc

    created_count = sum(1 for item in results if item["status"] == "created")
    return {
        "jira_base_url": client.base_url,
        "api_mode": client.mode,
        "project_key": project_key,
        "results": results,
        "created_count": created_count,
        "failed_count": len(results) - created_count,
        "credential_saved": bool(credential_save_succeeded or saved_matches),
    }
