#!/usr/bin/env python3
"""Generate a read-only weekly Jira hygiene and sprint status report.

The script intentionally uses only the Python standard library. It supports:
* Jira Cloud (REST API v3 enhanced JQL search)
* Jira Data Center / Server (REST API v2)
* Basic authentication with an API token and bearer authentication with a PAT
"""

from __future__ import annotations

import argparse
import base64
import csv
import getpass
import html
import json
import os
import re
import ssl
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


APP_NAME = "Jira Friday Status Report"
USER_AGENT = "jira-friday-status-report/1.0"
BUILTIN_FIELD_IDS = {
    "assignee",
    "comment",
    "created",
    "description",
    "duedate",
    "fixVersions",
    "issuelinks",
    "issuetype",
    "labels",
    "parent",
    "priority",
    "status",
    "statuscategorychangedate",
    "summary",
    "timespent",
    "updated",
    "versions",
    "worklog",
}
BASE_SEARCH_FIELDS = [
    "summary",
    "issuetype",
    "status",
    "assignee",
    "priority",
    "created",
    "updated",
    "statuscategorychangedate",
    "duedate",
    "issuelinks",
    "labels",
    "parent",
    "timespent",
    "worklog",
    "comment",
]


class ReportError(RuntimeError):
    """A user-actionable report error."""


class JiraError(ReportError):
    """A Jira HTTP or API error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ReportError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ReportError("The configuration root must be a JSON object.")
    return value


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def flatten_text(value: Any) -> str:
    """Convert Jira strings, ADF documents, and option objects to plain text."""
    pieces: list[str] = []

    def visit(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            if node.strip():
                pieces.append(node.strip())
            return
        if isinstance(node, (int, float, bool)):
            pieces.append(str(node))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, dict):
            # Prefer the human-readable values common in Jira field objects.
            for key in ("displayName", "name", "value", "text", "title", "key"):
                candidate = node.get(key)
                if isinstance(candidate, (str, int, float)) and str(candidate).strip():
                    pieces.append(str(candidate).strip())
                    if key not in {"text"}:
                        return
            content = node.get("content")
            if content is not None:
                visit(content)
                return
            for key, candidate in node.items():
                if key not in {
                    "attrs",
                    "id",
                    "accountId",
                    "self",
                    "avatarUrls",
                    "iconUrl",
                    "type",
                    "version",
                }:
                    visit(candidate)

    visit(value)
    return " ".join(dict.fromkeys(pieces)).strip()


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(is_present(item) for item in value)
    if isinstance(value, dict):
        return bool(flatten_text(value))
    # Numeric zero and False can be legitimate Jira field values.
    return True


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "report"


def normalize_jira_base_url(value: str) -> str:
    """Turn a pasted Jira issue/project URL into the Jira application base URL."""
    raw = value.strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    # Preserve a possible application context path, such as /jira, while
    # removing common page routes copied from the browser.
    route_match = re.search(
        r"(?i)/(?:browse|projects|issues|secure|plugins/servlet)(?:/|$)",
        path,
    )
    if route_match:
        path = path[: route_match.start()]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def extract_issue_keys(*texts: str) -> list[str]:
    """Pull unique Jira issue keys (e.g. SD-1234) out of branch names, MR
    titles, or descriptions, preserving first-seen order."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in ISSUE_KEY_PATTERN.finditer(text.upper()):
            key = match.group(1)
            if key not in found:
                found.append(key)
    return found


def _text_to_adf(text: str) -> dict[str, Any]:
    """Convert plain multi-line text into a minimal Atlassian Document Format
    body, turning any line that is entirely a URL into a clickable link."""
    paragraphs: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if not line.strip():
            # Skip blank lines rather than emitting an empty paragraph node,
            # which some Jira Cloud instances reject as invalid ADF.
            continue
        stripped = line.strip()
        if stripped.startswith(("http://", "https://")) and " " not in stripped:
            content = [
                {
                    "type": "text",
                    "text": stripped,
                    "marks": [{"type": "link", "attrs": {"href": stripped}}],
                }
            ]
        else:
            content = [{"type": "text", "text": line}]
        paragraphs.append({"type": "paragraph", "content": content})
    if not paragraphs:
        paragraphs.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})
    return {"type": "doc", "version": 1, "content": paragraphs}


def description_mentions_section(
    description_text: str, keywords: Iterable[str], min_content_chars: int = 8
) -> bool:
    """True when one of ``keywords`` appears in the description followed by
    some real content, used as a fallback for teams that write things like
    Acceptance Criteria or Test Evidence as a section inside the Description
    field instead of a dedicated Jira custom field."""
    text = description_text or ""
    if not text.strip():
        return False
    lower = text.casefold()
    for keyword in keywords:
        keyword = keyword.strip()
        if not keyword:
            continue
        idx = lower.find(keyword.casefold())
        if idx == -1:
            continue
        after = text[idx + len(keyword) :]
        after = after.lstrip(" \t:.-–—\n")
        next_paragraph = after.split("\n\n", 1)[0]
        if len(next_paragraph.strip()) >= min_content_chars:
            return True
    return False


def sprint_jql_value(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_jql(config: dict[str, Any], sprint: str | None, explicit_jql: str | None) -> str:
    if explicit_jql:
        return explicit_jql.strip()
    template = str(config.get("jql_template", "")).strip()
    if template:
        sprint_value = sprint or str(config.get("sprint", "")).strip()
        if not sprint_value:
            raise ReportError("Set 'sprint' in the configuration or pass --sprint.")
        try:
            return template.format(sprint=sprint_jql_value(sprint_value))
        except (KeyError, ValueError) as exc:
            raise ReportError(f"Invalid jql_template: {exc}") from exc
    jql = str(config.get("jql", "")).strip()
    if not jql:
        raise ReportError("Set 'jql_template' or 'jql' in the configuration.")
    if sprint:
        replacement = rf"\g<1>{sprint_jql_value(sprint)}"
        jql, count = re.subn(
            r"(?i)(\bSprint\s*=\s*)(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s)]+)",
            replacement,
            jql,
            count=1,
        )
        if count == 0:
            raise ReportError("Could not replace Sprint in the configured JQL.")
    return jql


class JiraClient:
    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, str] | None = None,
    ):
        env_base_url = os.getenv("JIRA_BASE_URL", "").strip()
        self.base_url = normalize_jira_base_url(
            env_base_url or str(config.get("base_url", ""))
        )
        if not self.base_url or "your-" in self.base_url or "example." in self.base_url:
            raise ReportError(
                "Set your Jira URL in jira_weekly_config.json or the JIRA_BASE_URL environment variable."
            )
        if not self.base_url.startswith(("https://", "http://")):
            raise ReportError("Jira base_url must start with https:// or http://.")

        self.config = config
        self.credentials = credentials or {}
        self.allow_prompt = credentials is None
        self.auth_header = self._authentication_header(config.get("auth", {}))
        self.ssl_context = None
        if not bool(config.get("verify_ssl", True)):
            self.ssl_context = ssl._create_unverified_context()  # noqa: SLF001
        self.mode = self._detect_mode()
        self.api_version = 3 if self.mode == "cloud" else 2

    def _authentication_header(self, auth_config: Any) -> str:
        if not isinstance(auth_config, dict):
            auth_config = {}
        auth_type = str(auth_config.get("type", "auto")).lower()
        pat_env = str(auth_config.get("pat_env", "JIRA_PAT"))
        user_env = str(auth_config.get("username_env", "JIRA_USER"))
        token_env = str(auth_config.get("token_env", "JIRA_API_TOKEN"))
        supplied_token = str(self.credentials.get("token", "")).strip()
        pat = (
            str(self.credentials.get("pat", "")).strip()
            or os.getenv(pat_env, "").strip()
        )
        username = (
            str(self.credentials.get("username", "")).strip()
            or
            os.getenv(user_env, "").strip()
            or str(auth_config.get("username", "")).strip()
        )
        token = supplied_token or os.getenv(token_env, "").strip()

        if auth_type == "auto":
            # Atlassian Cloud uses email + API token. Data Center commonly uses
            # a bearer PAT; the environment variable always takes precedence.
            configured_mode = str(self.config.get("api_mode", "auto")).lower()
            if configured_mode == "cloud":
                auth_type = "basic"
            elif configured_mode == "server":
                auth_type = "bearer"
            elif pat:
                auth_type = "bearer"
            elif username or ".atlassian.net" in self.base_url.lower():
                auth_type = "basic"
            else:
                auth_type = "bearer"

        if auth_type == "bearer":
            pat = pat or supplied_token
            if not pat and self.allow_prompt and sys.stdin.isatty():
                pat = getpass.getpass(f"Jira personal access token ({pat_env}): ").strip()
            if not pat:
                raise ReportError(
                    f"Bearer authentication selected, but {pat_env} is not set."
                )
            return f"Bearer {pat}"

        if auth_type == "basic":
            if not username and self.allow_prompt and sys.stdin.isatty():
                username = input(f"Jira username/email ({user_env}): ").strip()
            if not token and self.allow_prompt and sys.stdin.isatty():
                token = getpass.getpass(f"Jira API token ({token_env}): ").strip()
            if not username or not token:
                raise ReportError(
                    f"Basic authentication requires {user_env} and {token_env}."
                )
            encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
            return f"Basic {encoded}"

        raise ReportError("auth.type must be 'auto', 'basic', or 'bearer'.")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": self.auth_header,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=60, context=self.ssl_context) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                final_url = response.geturl()
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            detail = raw_error
            try:
                parsed = json.loads(raw_error)
                messages = parsed.get("errorMessages") or parsed.get("errors") or parsed
                detail = flatten_text(messages) or raw_error
            except json.JSONDecodeError:
                pass
            raise JiraError(
                f"Jira returned HTTP {exc.code} for {path}: {detail[:600]}",
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise JiraError(f"Could not connect to {self.base_url}: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            decoded = raw.decode("utf-8", errors="replace")
            title_match = re.search(
                r"(?is)<title[^>]*>(.*?)</title>",
                decoded,
            )
            page_title = (
                re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()
                if title_match
                else ""
            )
            redirected = final_url.rstrip("/") != (self.base_url + path).rstrip("/")
            hints = [
                "Jira returned a web page instead of REST API data.",
                "Check that the Jira URL is the application root, not a copied issue or dashboard URL.",
                "For Jira Cloud select 'Jira Cloud' and use email + API token; "
                "for Data Center select 'Data Center / Server' and use a PAT.",
            ]
            if redirected or "html" in content_type.lower():
                hints.append("The request may have been redirected to your company sign-in/SSO page.")
            if page_title:
                hints.append(f"Returned page title: {page_title[:120]}.")
            raise JiraError(" ".join(hints)) from exc

    def _detect_mode(self) -> str:
        configured = str(self.config.get("api_mode", "auto")).lower()
        if configured in {"cloud", "server"}:
            return configured
        if configured != "auto":
            raise ReportError("api_mode must be 'auto', 'cloud', or 'server'.")
        try:
            info = self.request("GET", "/rest/api/2/serverInfo")
            deployment = str(info.get("deploymentType", "")).lower()
            if deployment == "cloud":
                return "cloud"
            if deployment:
                return "server"
        except JiraError:
            pass
        return "cloud" if ".atlassian.net" in self.base_url.lower() else "server"

    def get_fields(self) -> list[dict[str, Any]]:
        result = self.request("GET", f"/rest/api/{self.api_version}/field")
        if not isinstance(result, list):
            raise JiraError("Unexpected response from Jira field metadata endpoint.")
        return [item for item in result if isinstance(item, dict)]

    def search(self, jql: str, fields: list[str]) -> list[dict[str, Any]]:
        if self.mode == "cloud":
            try:
                return self._enhanced_cloud_search(jql, fields)
            except JiraError as exc:
                if exc.status not in {404, 405, 410}:
                    raise
        return self._legacy_search(jql, fields)

    def _enhanced_cloud_search(
        self, jql: str, fields: list[str]
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        next_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            payload: dict[str, Any] = {
                "jql": jql,
                "fields": fields,
                "fieldsByKeys": False,
                "maxResults": 100,
            }
            if next_token:
                payload["nextPageToken"] = next_token
            page = self.request("POST", "/rest/api/3/search/jql", payload)
            page_issues = page.get("issues", [])
            if not isinstance(page_issues, list):
                raise JiraError("Unexpected response from Jira enhanced search.")
            issues.extend(item for item in page_issues if isinstance(item, dict))
            next_token = page.get("nextPageToken")
            if not next_token or page.get("isLast") is True:
                break
            if next_token in seen_tokens:
                raise JiraError("Jira returned a repeated pagination token.")
            seen_tokens.add(next_token)
        return issues

    def get_issue_worklogs(
        self,
        issue_key: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        local_tz = datetime.now().astimezone().tzinfo
        start_ms = int(
            datetime.combine(start_date, time.min, tzinfo=local_tz).timestamp()
            * 1000
        )
        end_exclusive = end_date + timedelta(days=1)
        end_ms = int(
            datetime.combine(end_exclusive, time.min, tzinfo=local_tz).timestamp()
            * 1000
        )

        def fetch(use_date_parameters: bool) -> list[dict[str, Any]]:
            worklogs: list[dict[str, Any]] = []
            start_at = 0
            while True:
                params: dict[str, Any] = {
                    "startAt": start_at,
                    "maxResults": 100,
                }
                if use_date_parameters:
                    params.update(
                        {
                            "startedAfter": start_ms,
                            "startedBefore": end_ms,
                        }
                    )
                path = (
                    f"/rest/api/{self.api_version}/issue/{quote(issue_key)}/worklog?"
                    + urlencode(params)
                )
                page = self.request("GET", path)
                page_worklogs = page.get("worklogs", [])
                if not isinstance(page_worklogs, list):
                    raise JiraError(
                        f"Unexpected worklog response for {issue_key}."
                    )
                worklogs.extend(
                    item for item in page_worklogs if isinstance(item, dict)
                )
                start_at += len(page_worklogs)
                total = int(page.get("total", start_at))
                if not page_worklogs or start_at >= total:
                    break
            return worklogs

        try:
            worklogs = fetch(use_date_parameters=True)
        except JiraError as exc:
            # Older Jira Server releases may not support the date parameters.
            if exc.status != 400:
                raise
            worklogs = fetch(use_date_parameters=False)

        filtered: list[dict[str, Any]] = []
        for item in worklogs:
            started = parse_datetime(item.get("started"))
            if not started:
                continue
            local_date = started.astimezone().date()
            if start_date <= local_date <= end_date:
                filtered.append(item)
        return filtered

    def get_user_display_name(self, identifier: str) -> str:
        parameter = "accountId" if self.mode == "cloud" else "username"
        query = urlencode({parameter: identifier})
        user = self.request(
            "GET",
            f"/rest/api/{self.api_version}/user?{query}",
        )
        return jira_name(user, fallback=identifier)

    def get_issue_comments(self, issue_key: str, max_results: int = 50) -> list[str]:
        """Plain-text bodies of the most recent comments on an issue."""
        query = urlencode({"maxResults": max_results})
        data = self.request(
            "GET",
            f"/rest/api/{self.api_version}/issue/{quote(issue_key)}/comment?{query}",
        )
        comments = data.get("comments", []) if isinstance(data, dict) else []
        return [
            flatten_text(item.get("body"))
            for item in comments
            if isinstance(item, dict) and item.get("body")
        ]

    def add_comment(self, issue_key: str, text: str) -> dict[str, Any]:
        """Post a plain-text comment to a Jira issue.

        Builds an Atlassian Document Format body for Cloud (API v3) and a
        plain string body for Data Center / Server (API v2), since the two
        deployment types expect different comment payload shapes.
        """
        if self.mode == "cloud":
            payload = {"body": _text_to_adf(text)}
        else:
            payload = {"body": text}
        return self.request(
            "POST",
            f"/rest/api/{self.api_version}/issue/{quote(issue_key)}/comment",
            payload,
        )

    def _legacy_search(self, jql: str, fields: list[str]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start_at = 0
        while True:
            payload = {
                "jql": jql,
                "fields": fields,
                "maxResults": 100,
                "startAt": start_at,
            }
            page = self.request(
                "POST", f"/rest/api/{self.api_version}/search", payload
            )
            page_issues = page.get("issues", [])
            if not isinstance(page_issues, list):
                raise JiraError("Unexpected response from Jira issue search.")
            issues.extend(item for item in page_issues if isinstance(item, dict))
            start_at += len(page_issues)
            total = int(page.get("total", start_at))
            if not page_issues or start_at >= total:
                break
        return issues


@dataclass
class ResolvedCheck:
    key: str
    label: str
    field_ids: list[str]
    required: bool
    issue_types: set[str]
    exclude_issue_types: set[str]
    status_categories: set[str]
    exclude_status_categories: set[str]
    allow_zero: bool
    description_keywords: list[str]

    def applies(self, issue_type: str, status_category: str) -> bool:
        issue_type_cf = issue_type.casefold()
        status_cf = status_category.casefold()
        if self.issue_types and issue_type_cf not in self.issue_types:
            return False
        if issue_type_cf in self.exclude_issue_types:
            return False
        if self.status_categories and status_cf not in self.status_categories:
            return False
        if status_cf in self.exclude_status_categories:
            return False
        return True


class FieldResolver:
    def __init__(self, fields: list[dict[str, Any]]):
        self.fields = fields
        self.index: dict[str, list[str]] = defaultdict(list)
        for field in fields:
            field_id = str(field.get("id", "")).strip()
            if not field_id:
                continue
            candidates: list[Any] = [
                field_id,
                field.get("key"),
                field.get("name"),
                *(field.get("clauseNames") or []),
            ]
            for candidate in candidates:
                if candidate is None:
                    continue
                normalized = str(candidate).strip().casefold()
                if normalized and field_id not in self.index[normalized]:
                    self.index[normalized].append(field_id)

    def resolve(self, candidates: Iterable[Any]) -> list[str]:
        resolved: list[str] = []
        for candidate in candidates:
            name = str(candidate).strip()
            if not name:
                continue
            matches = self.index.get(name.casefold(), [])
            if not matches and (
                name in BUILTIN_FIELD_IDS
                or re.fullmatch(r"customfield_\d+", name, flags=re.IGNORECASE)
            ):
                matches = [name]
            for field_id in matches:
                if field_id not in resolved:
                    resolved.append(field_id)
        return resolved


def resolve_checks(
    config: dict[str, Any], resolver: FieldResolver
) -> tuple[list[ResolvedCheck], list[dict[str, str]]]:
    configured_checks = config.get("checks", [])
    if not isinstance(configured_checks, list) or not configured_checks:
        raise ReportError("Configuration must contain a non-empty 'checks' list.")
    checks: list[ResolvedCheck] = []
    unresolved: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for raw in configured_checks:
        if not isinstance(raw, dict):
            raise ReportError("Every checks entry must be a JSON object.")
        key = str(raw.get("key", "")).strip()
        label = str(raw.get("label", key)).strip()
        if not key or key in seen_keys:
            raise ReportError(f"Check keys must be unique and non-empty: {key!r}")
        seen_keys.add(key)
        candidates = list(raw.get("field_ids", [])) + list(raw.get("field_names", []))
        field_ids = resolver.resolve(candidates)
        required = bool(raw.get("required", True))
        description_keywords = [
            str(item).strip()
            for item in raw.get("description_keywords", [])
            if str(item).strip()
        ]
        if not field_ids and not description_keywords:
            unresolved.append(
                {
                    "key": key,
                    "label": label,
                    "candidates": ", ".join(str(item) for item in candidates),
                    "required": "yes" if required else "no",
                }
            )
        checks.append(
            ResolvedCheck(
                key=key,
                label=label,
                field_ids=field_ids,
                required=required,
                issue_types={
                    str(item).casefold() for item in raw.get("issue_types", [])
                },
                exclude_issue_types={
                    str(item).casefold()
                    for item in raw.get("exclude_issue_types", [])
                },
                status_categories={
                    str(item).casefold()
                    for item in raw.get("status_categories", [])
                },
                exclude_status_categories={
                    str(item).casefold()
                    for item in raw.get("exclude_status_categories", [])
                },
                allow_zero=bool(raw.get("allow_zero", True)),
                description_keywords=description_keywords,
            )
        )
    return checks, unresolved


def jira_name(value: Any, fallback: str = "Unknown") -> str:
    if not isinstance(value, dict):
        return flatten_text(value) or fallback
    for key in ("displayName", "name", "value", "key", "accountId"):
        candidate = value.get(key)
        if candidate:
            return str(candidate)
    return fallback


def value_for_fields(fields: dict[str, Any], field_ids: Iterable[str]) -> list[Any]:
    return [fields.get(field_id) for field_id in field_ids]


def check_value_present(value: Any, allow_zero: bool) -> bool:
    if not is_present(value):
        return False
    if allow_zero:
        return True
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, list):
        return any(check_value_present(item, allow_zero=False) for item in value)
    return True


def worklog_details(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    worklogs = value.get("worklogs", [])
    if not isinstance(worklogs, list):
        return []
    details: list[dict[str, Any]] = []
    for item in worklogs:
        if not isinstance(item, dict):
            continue
        seconds = item.get("timeSpentSeconds")
        if not isinstance(seconds, (int, float)):
            seconds = 0
        started = str(item.get("started", "")).strip()
        started_dt = parse_datetime(started)
        details.append(
            {
                "author": jira_name(item.get("author"), fallback="Unknown"),
                "time_spent": str(item.get("timeSpent", "")).strip(),
                "seconds": seconds,
                "hours": round(seconds / 3600, 2),
                "started": started,
                "date": started_dt.astimezone().date().isoformat()
                if started_dt
                else "",
                "comment": flatten_text(item.get("comment")),
            }
        )
    return details


def attach_period_worklogs(
    client: JiraClient,
    raw_issues: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    workers: int = 4,
) -> list[dict[str, str]]:
    """Fetch complete worklogs for the period and attach them to search issues."""
    issue_by_key = {
        str(issue.get("key", "")): issue
        for issue in raw_issues
        if issue.get("key")
    }
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = {
            executor.submit(
                client.get_issue_worklogs,
                issue_key,
                start_date,
                end_date,
            ): issue_key
            for issue_key in issue_by_key
        }
        for future in as_completed(futures):
            issue_key = futures[future]
            issue = issue_by_key[issue_key]
            fields = issue.setdefault("fields", {})
            try:
                worklogs = future.result()
                fields["_period_worklog"] = {"worklogs": worklogs}
                fields["_period_worklog_available"] = True
            except JiraError as exc:
                fields["_period_worklog"] = {"worklogs": []}
                fields["_period_worklog_available"] = False
                errors.append({"issue": issue_key, "message": str(exc)})
    return sorted(errors, key=lambda item: item["issue"])


def resolve_team_member_names(
    client: JiraClient,
    config: dict[str, Any],
) -> list[str]:
    identifiers = [
        str(item).strip()
        for item in config.get("team_members", [])
        if str(item).strip()
    ]
    names: list[str] = []
    for identifier in identifiers:
        try:
            name = client.get_user_display_name(identifier)
        except JiraError:
            continue
        if name not in names:
            names.append(name)
    return names


def analyze_issues(
    raw_issues: list[dict[str, Any]],
    checks: list[ResolvedCheck],
    helper_fields: dict[str, list[str]],
    config: dict[str, Any],
    base_url: str,
) -> list[dict[str, Any]]:
    thresholds = config.get("thresholds", {})
    if not isinstance(thresholds, dict):
        thresholds = {}
    stale_limit = int(thresholds.get("no_activity_days", 7))
    in_progress_limit = int(thresholds.get("in_progress_days", 14))
    due_soon_limit = int(thresholds.get("due_soon_days", 7))
    now = datetime.now().astimezone()
    today = now.date()
    results: list[dict[str, Any]] = []

    for raw_issue in raw_issues:
        fields = raw_issue.get("fields") or {}
        key = str(raw_issue.get("key", "UNKNOWN"))
        issue_type = jira_name(fields.get("issuetype"))
        status = jira_name(fields.get("status"))
        status_object = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        status_category = jira_name(status_object.get("statusCategory"), fallback="")
        owner = jira_name(fields.get("assignee"), fallback="Unassigned")
        updated = parse_datetime(fields.get("updated"))
        created = parse_datetime(fields.get("created"))
        status_changed = parse_datetime(fields.get("statuscategorychangedate"))
        due_raw = fields.get("duedate")
        due_dt = parse_datetime(due_raw)
        due_date = due_dt.date() if due_dt else None
        updated_days = (now - updated.astimezone(now.tzinfo)).days if updated else None
        status_age_source = status_changed or updated or created
        status_days = (
            (now - status_age_source.astimezone(now.tzinfo)).days
            if status_age_source
            else None
        )

        missing: list[str] = []
        missing_keys: list[str] = []
        advisory: list[str] = []
        passed_checks = 0
        total_checks = 0
        check_values: dict[str, str] = {}
        description_text = flatten_text(fields.get("description"))
        raw_comments = fields.get("comment")
        comment_available = "comment" in fields
        comment_count = 0
        if isinstance(raw_comments, dict):
            raw_count = raw_comments.get("total")
            if isinstance(raw_count, (int, float)):
                comment_count = max(int(raw_count), 0)
            elif isinstance(raw_comments.get("comments"), list):
                comment_count = len(raw_comments["comments"])
        elif isinstance(raw_comments, list):
            comment_count = len(raw_comments)
        description_sections: list[dict[str, Any]] = []

        for check in checks:
            if not check.applies(issue_type, status_category):
                continue
            if not check.field_ids and not check.description_keywords:
                continue

            field_present = False
            if check.field_ids:
                values = value_for_fields(fields, check.field_ids)
                field_present = any(
                    check_value_present(value, check.allow_zero) for value in values
                )
                check_values[check.key] = " ".join(
                    value for value in (flatten_text(item) for item in values) if value
                )

            present = field_present
            source = "field" if field_present else "missing"
            if check.description_keywords and not field_present:
                present = description_mentions_section(
                    description_text, check.description_keywords
                )
                source = "description" if present else "missing"
                if present:
                    check_values[check.key] = "Found in description text"

            if check.description_keywords:
                description_sections.append(
                    {
                        "key": check.key,
                        "label": check.label,
                        "present": present,
                        "source": source,
                    }
                )

            if check.required:
                total_checks += 1
                if present:
                    passed_checks += 1
                else:
                    missing.append(check.label)
                    missing_keys.append(check.key)
            elif not present:
                advisory.append(f"{check.label} missing (advisory)")

        hygiene_warnings: list[str] = []
        is_done = status_category.casefold() == "done"
        is_in_progress = status_category.casefold() == "in progress"
        labels = [
            str(item).casefold()
            for item in (fields.get("labels") or [])
            if item is not None
        ]
        flagged_values = value_for_fields(fields, helper_fields.get("flagged", []))
        flagged_text = " ".join(flatten_text(item).casefold() for item in flagged_values)
        is_blocked = (
            "block" in status.casefold()
            or any("block" in label for label in labels)
            or "impediment" in flagged_text
            or "flagged" in flagged_text
        )

        if not is_done:
            total_checks += 1
            if updated_days is not None and updated_days <= stale_limit:
                passed_checks += 1
            else:
                missing_keys.append("status_freshness")
                if updated_days is None:
                    missing.append("Status freshness unavailable")
                else:
                    missing.append(f"No activity for {updated_days} days")

        if is_in_progress:
            total_checks += 1
            if status_days is not None and status_days <= in_progress_limit:
                passed_checks += 1
            else:
                missing_keys.append("in_progress_age")
                if status_days is None:
                    missing.append("In Progress age unavailable")
                else:
                    missing.append(f"In Progress for {status_days} days")

        issue_links = fields.get("issuelinks") or []
        if is_blocked:
            total_checks += 1
            if is_present(issue_links):
                passed_checks += 1
            else:
                missing_keys.append("linked_dependencies")
                missing.append("Linked dependency/blocker")

        overdue = bool(due_date and due_date < today and not is_done)
        due_soon = bool(
            due_date
            and not is_done
            and today <= due_date <= date.fromordinal(today.toordinal() + due_soon_limit)
        )
        if overdue:
            hygiene_warnings.append(f"Overdue since {due_date.isoformat()}")
        elif due_soon:
            hygiene_warnings.append(f"Due soon: {due_date.isoformat()}")

        score = round((passed_checks / total_checks * 100), 1) if total_checks else 100.0
        time_spent_seconds = fields.get("timespent")
        if not isinstance(time_spent_seconds, (int, float)):
            time_spent_seconds = 0
        logged_hours = round(time_spent_seconds / 3600, 2)
        period_worklogs = worklog_details(
            fields.get("_period_worklog") or fields.get("worklog")
        )
        worked_by_totals: dict[str, float] = defaultdict(float)
        for entry in period_worklogs:
            worked_by_totals[entry["author"]] += float(entry.get("seconds", 0))
        worked_by = [
            {
                "person": person,
                "hours": round(seconds / 3600, 2),
            }
            for person, seconds in sorted(
                worked_by_totals.items(), key=lambda item: item[0].casefold()
            )
        ]
        period_logged_hours = round(
            sum(float(item.get("seconds", 0)) for item in period_worklogs) / 3600,
            2,
        )
        results.append(
            {
                "key": key,
                "url": f"{base_url}/browse/{quote(key)}",
                "summary": flatten_text(fields.get("summary")) or "(No summary)",
                "issue_type": issue_type,
                "status": status,
                "status_category": status_category,
                "owner": owner,
                "priority": jira_name(fields.get("priority"), fallback=""),
                "created": created.isoformat() if created else "",
                "updated": updated.isoformat() if updated else "",
                "updated_days": updated_days,
                "status_days": status_days,
                "due_date": due_date.isoformat() if due_date else "",
                "epic_parent": check_values.get("epic_parent", ""),
                "commit_details": check_values.get("commit_ids", ""),
                # This reflects the actual Jira field, rather than whether a
                # description-related hygiene check happens to be configured.
                "description_present": bool(description_text.strip()),
                "description_sections": description_sections,
                "comment_count": comment_count,
                "comment_available": comment_available,
                "logged_hours": logged_hours,
                "period_logged_hours": period_logged_hours,
                "worked_by": worked_by,
                "worklogs": period_worklogs,
                "worklog_available": bool(
                    fields.get("_period_worklog_available", True)
                ),
                "missing": missing,
                "missing_keys": missing_keys,
                "advisory": advisory,
                "warnings": hygiene_warnings,
                "passed_checks": passed_checks,
                "total_checks": total_checks,
                "score": score,
                "compliant": not missing,
                "blocked": is_blocked,
                "overdue": overdue,
                "due_soon": due_soon,
                "stale": (
                    not is_done
                    and updated_days is not None
                    and updated_days > stale_limit
                ),
                "aging": (
                    is_in_progress
                    and status_days is not None
                    and status_days > in_progress_limit
                ),
                "open_bug": "bug" in issue_type.casefold() and not is_done,
            }
        )
    return results


def summarize(
    issues: list[dict[str, Any]], checks: list[ResolvedCheck]
) -> dict[str, Any]:
    total_passed = sum(item["passed_checks"] for item in issues)
    total_checks = sum(item["total_checks"] for item in issues)
    score = round(total_passed / total_checks * 100, 1) if total_checks else 100.0
    owners: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        grouped[issue["owner"]].append(issue)
    all_missing_keys = [check.key for check in checks]
    all_missing_keys.extend(
        ["status_freshness", "in_progress_age", "linked_dependencies"]
    )
    for owner, owner_issues in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        missing_counts = Counter(
            key
            for issue in owner_issues
            for key in issue.get("missing_keys", [])
        )
        owner_passed = sum(item["passed_checks"] for item in owner_issues)
        owner_checks = sum(item["total_checks"] for item in owner_issues)
        owners[owner] = {
            "owner": owner,
            "total": len(owner_issues),
            "compliant": sum(1 for item in owner_issues if item["compliant"]),
            "done": sum(
                1
                for item in owner_issues
                if item["status_category"].casefold() == "done"
            ),
            "in_progress": sum(
                1
                for item in owner_issues
                if item["status_category"].casefold() == "in progress"
            ),
            "to_do": sum(
                1
                for item in owner_issues
                if item["status_category"].casefold() == "to do"
            ),
            "blocked": sum(1 for item in owner_issues if item["blocked"]),
            "overdue": sum(1 for item in owner_issues if item["overdue"]),
            "score": round(owner_passed / owner_checks * 100, 1)
            if owner_checks
            else 100.0,
            "missing": {key: missing_counts.get(key, 0) for key in all_missing_keys},
        }
    status_counts = Counter(item["status"] for item in issues)
    missing_by_key = Counter(
        key for issue in issues for key in issue.get("missing_keys", [])
    )
    return {
        "total": len(issues),
        "compliant": sum(1 for item in issues if item["compliant"]),
        "score": score,
        "done": sum(
            1 for item in issues if item["status_category"].casefold() == "done"
        ),
        "in_progress": sum(
            1
            for item in issues
            if item["status_category"].casefold() == "in progress"
        ),
        "to_do": sum(
            1 for item in issues if item["status_category"].casefold() == "to do"
        ),
        "open_bugs": sum(1 for item in issues if item["open_bug"]),
        "unassigned": sum(1 for item in issues if item["owner"] == "Unassigned"),
        "blocked": sum(1 for item in issues if item["blocked"]),
        "stale": sum(1 for item in issues if item["stale"]),
        "aging": sum(1 for item in issues if item["aging"]),
        "overdue": sum(1 for item in issues if item["overdue"]),
        "due_soon": sum(1 for item in issues if item["due_soon"]),
        "missing_test_evidence": sum(
            1 for item in issues if "test_evidence" in item["missing_keys"]
        ),
        "missing_documentation": sum(
            1 for item in issues if "documentation" in item["missing_keys"]
        ),
        "missing_by_key": dict(missing_by_key),
        "owners": list(owners.values()),
        "statuses": [
            {"status": status, "count": count}
            for status, count in status_counts.most_common()
        ],
    }


def build_timesheet(
    issues: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    expected_hours_per_day: float,
    team_people: Iterable[str] = (),
) -> dict[str, Any]:
    dates: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        dates.append(cursor)
        cursor += timedelta(days=1)
    date_keys = [item.isoformat() for item in dates]
    working_days = sum(1 for item in dates if item.weekday() < 5)
    expected_hours = round(working_days * expected_hours_per_day, 2)

    seconds_by_person_date: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    entries: list[dict[str, Any]] = []
    people: set[str] = {str(item) for item in team_people if str(item).strip()}
    owner_has_available_issue: dict[str, bool] = defaultdict(bool)
    owner_has_unavailable_issue: dict[str, bool] = defaultdict(bool)

    for issue in issues:
        assigned_owner = issue.get("owner", "Unassigned")
        if assigned_owner != "Unassigned":
            people.add(assigned_owner)
            if issue.get("worklog_available", True):
                owner_has_available_issue[assigned_owner] = True
            else:
                owner_has_unavailable_issue[assigned_owner] = True
        for item in issue.get("worklogs", []):
            person = str(item.get("author", "Unknown"))
            work_date = str(item.get("date", ""))
            seconds = float(item.get("seconds", 0))
            if not work_date or work_date not in date_keys:
                continue
            people.add(person)
            seconds_by_person_date[person][work_date] += seconds
            entries.append(
                {
                    "date": work_date,
                    "person": person,
                    "issue": issue["key"],
                    "issue_url": issue["url"],
                    "summary": issue["summary"],
                    "hours": round(seconds / 3600, 2),
                    "time_spent": item.get("time_spent", ""),
                    "comment": item.get("comment", ""),
                }
            )

    rows: list[dict[str, Any]] = []
    for person in sorted(people, key=str.casefold):
        daily_hours = {
            day: round(seconds_by_person_date[person].get(day, 0) / 3600, 2)
            for day in date_keys
        }
        total_hours = round(sum(daily_hours.values()), 2)
        if (
            total_hours == 0
            and owner_has_unavailable_issue[person]
            and not owner_has_available_issue[person]
        ):
            status = "Unavailable"
        elif expected_hours > 0 and total_hours >= expected_hours:
            status = "Complete"
        elif total_hours > 0:
            status = "Partial"
        else:
            status = "Not filled"
        completion = (
            round(min(total_hours / expected_hours * 100, 100), 1)
            if expected_hours
            else 100.0
        )
        rows.append(
            {
                "person": person,
                "daily_hours": daily_hours,
                "total_hours": total_hours,
                "expected_hours": expected_hours,
                "completion": completion,
                "status": status,
            }
        )

    entries.sort(
        key=lambda item: (
            item["date"],
            item["person"].casefold(),
            item["issue"],
        )
    )
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "dates": date_keys,
        "expected_hours_per_day": expected_hours_per_day,
        "expected_hours": expected_hours,
        "total_hours": round(sum(row["total_hours"] for row in rows), 2),
        "complete": sum(1 for row in rows if row["status"] == "Complete"),
        "partial": sum(1 for row in rows if row["status"] == "Partial"),
        "not_filled": sum(1 for row in rows if row["status"] == "Not filled"),
        "unavailable": sum(1 for row in rows if row["status"] == "Unavailable"),
        "rows": rows,
        "entries": entries,
    }


def badge(value: str, tone: str = "neutral") -> str:
    return f'<span class="badge {tone}">{html.escape(value)}</span>'


def generate_html(
    report_date: str,
    generated_at: str,
    jql: str,
    base_url: str,
    issues: list[dict[str, Any]],
    summary: dict[str, Any],
    checks: list[ResolvedCheck],
    unresolved: list[dict[str, str]],
    timesheet: dict[str, Any],
) -> str:
    check_labels = {check.key: check.label for check in checks}
    check_labels.update(
        {
            "status_freshness": "Stale status",
            "in_progress_age": "In Progress > limit",
            "linked_dependencies": "Missing blocker link",
        }
    )
    owner_focus = [
        "epic_parent",
        "story_points",
        "due_date",
        "fix_version",
        "test_evidence",
    ]
    owner_headers = "".join(
        f"<th>{html.escape(check_labels.get(key, key))}</th>" for key in owner_focus
    )
    owner_rows = []
    for owner in summary["owners"]:
        owner_score_tone = (
            "good"
            if owner["score"] >= 90
            else "warn"
            if owner["score"] >= 70
            else "bad"
        )
        owner_score_badge = badge(f"{owner['score']}%", owner_score_tone)
        owner_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(owner['owner'])}</strong></td>"
            f"<td>{owner['total']}</td>"
            f"<td>{owner['compliant']}</td>"
            f"<td>{owner_score_badge}</td>"
            + "".join(f"<td>{owner['missing'].get(key, 0)}</td>" for key in owner_focus)
            + "</tr>"
        )
    if not owner_rows:
        owner_rows.append('<tr><td colspan="8" class="empty">No issues matched the JQL.</td></tr>')

    status_rows = "".join(
        f"<tr><td>{html.escape(item['status'])}</td><td>{item['count']}</td></tr>"
        for item in summary["statuses"]
    ) or '<tr><td colspan="2" class="empty">No status data</td></tr>'

    issue_rows = []
    for issue in sorted(
        issues,
        key=lambda item: (
            item["compliant"],
            item["owner"].casefold(),
            item["key"],
        ),
    ):
        problems = issue["missing"] + issue["advisory"] + issue["warnings"]
        problem_html = (
            "<br>".join(html.escape(item) for item in problems)
            if problems
            else badge("All checked items complete", "good")
        )
        score_tone = "good" if issue["score"] >= 90 else "warn" if issue["score"] >= 70 else "bad"
        issue_score_badge = badge(f"{issue['score']}%", score_tone)
        worklog_summary = "; ".join(
            " — ".join(
                value
                for value in (
                    item.get("author", ""),
                    item.get("time_spent", ""),
                    item.get("started", ""),
                )
                if value
            )
            for item in issue.get("worklogs", [])
        )
        logged_text = f"{issue['logged_hours']:g}h" if issue["logged_hours"] else "—"
        worked_by_text = ", ".join(
            f"{item['person']} — {item['hours']:g}h"
            for item in issue.get("worked_by", [])
        ) or "—"
        issue_rows.append(
            f'<tr class="{"row-good" if issue["compliant"] else "row-bad"}">'
            f'<td><a href="{html.escape(issue["url"])}">{html.escape(issue["key"])}</a></td>'
            f"<td>{html.escape(issue['summary'])}</td>"
            f"<td>{html.escape(issue['owner'])}</td>"
            f"<td>{html.escape(issue['issue_type'])}</td>"
            f"<td>{badge(issue['status'])}</td>"
            f"<td>{html.escape(issue['due_date'] or '—')}</td>"
            f'<td title="{html.escape(worklog_summary, quote=True)}">{html.escape(logged_text)}</td>'
            f"<td>{html.escape(worked_by_text)}</td>"
            f"<td>{html.escape(str(issue['updated_days']) + 'd ago' if issue['updated_days'] is not None else '—')}</td>"
            f"<td>{issue_score_badge}</td>"
            f"<td>{problem_html}</td>"
            "</tr>"
        )
    if not issue_rows:
        issue_rows.append('<tr><td colspan="11" class="empty">No issues matched the JQL.</td></tr>')

    timesheet_date_headers = "".join(
        f"<th>{html.escape(day)}</th>" for day in timesheet["dates"]
    )
    timesheet_rows = []
    for row in timesheet["rows"]:
        tone = (
            "good"
            if row["status"] == "Complete"
            else "warn"
            if row["status"] == "Partial"
            else "bad"
        )
        timesheet_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['person'])}</strong></td>"
            + "".join(
                f"<td>{row['daily_hours'].get(day, 0):g}h</td>"
                for day in timesheet["dates"]
            )
            + f"<td><strong>{row['total_hours']:g}h</strong></td>"
            + f"<td>{row['expected_hours']:g}h</td>"
            + f"<td>{badge(row['status'], tone)}</td>"
            + "</tr>"
        )
    if not timesheet_rows:
        timesheet_rows.append(
            f'<tr><td colspan="{len(timesheet["dates"]) + 4}" class="empty">'
            "No people or worklogs found for this period.</td></tr>"
        )

    worklog_rows = []
    for entry in timesheet["entries"]:
        worklog_rows.append(
            "<tr>"
            f"<td>{html.escape(entry['date'])}</td>"
            f"<td>{html.escape(entry['person'])}</td>"
            f'<td><a href="{html.escape(entry["issue_url"])}">{html.escape(entry["issue"])}</a></td>'
            f"<td>{html.escape(entry['summary'])}</td>"
            f"<td>{entry['hours']:g}h</td>"
            f"<td>{html.escape(entry['comment'] or '—')}</td>"
            "</tr>"
        )
    if not worklog_rows:
        worklog_rows.append(
            '<tr><td colspan="6" class="empty">No worklogs found for this period.</td></tr>'
        )

    unresolved_html = ""
    if unresolved:
        entries = "".join(
            f"<li><strong>{html.escape(item['label'])}</strong>: tried "
            f"{html.escape(item['candidates'] or '(no field names configured)')}"
            f"{' — required check excluded from scoring' if item['required'] == 'yes' else ' — advisory check unavailable'}</li>"
            for item in unresolved
        )
        unresolved_html = (
            '<section class="notice"><h2>Configuration attention</h2>'
            "<p>These Jira fields could not be matched by name. Add the exact field name "
            "or customfield ID in <code>jira_weekly_config.json</code>.</p>"
            f"<ul>{entries}</ul></section>"
        )

    kpis = [
        ("Sprint items", summary["total"], ""),
        ("Hygiene score", f"{summary['score']}%", "accent"),
        ("Fully compliant", summary["compliant"], "good-card"),
        ("Open bugs", summary["open_bugs"], ""),
        ("Blocked", summary["blocked"], "warn-card"),
        ("No activity", summary["stale"], "warn-card"),
        ("In Progress > limit", summary["aging"], "warn-card"),
        ("Overdue", summary["overdue"], "bad-card"),
    ]
    kpi_html = "".join(
        f'<div class="kpi {tone}"><span>{html.escape(str(label))}</span>'
        f"<strong>{html.escape(str(value))}</strong></div>"
        for label, value, tone in kpis
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{APP_NAME} — {html.escape(report_date)}</title>
<style>
:root {{ --ink:#172b4d; --muted:#5e6c84; --line:#dfe1e6; --bg:#f4f5f7;
  --blue:#0052cc; --blue-soft:#deebff; --green:#006644; --green-soft:#e3fcef;
  --amber:#974f0c; --amber-soft:#fff0b3; --red:#bf2600; --red-soft:#ffebe6; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 "Segoe UI",Arial,sans-serif; }}
.page {{ max-width:1500px; margin:auto; padding:28px; }}
header {{ background:linear-gradient(130deg,#0747a6,#0052cc); color:white; padding:28px 32px;
  border-radius:14px; box-shadow:0 8px 22px rgba(9,30,66,.16); }}
header h1 {{ margin:0 0 5px; font-size:28px; }} header p {{ margin:3px 0; opacity:.9; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(155px,1fr)); gap:12px; margin:18px 0; }}
.kpi {{ background:white; border:1px solid var(--line); border-radius:10px; padding:15px; }}
.kpi span {{ display:block; color:var(--muted); font-size:12px; }} .kpi strong {{ font-size:26px; }}
.kpi.accent {{ border-top:4px solid var(--blue); }} .kpi.good-card {{ border-top:4px solid var(--green); }}
.kpi.warn-card {{ border-top:4px solid #ffab00; }} .kpi.bad-card {{ border-top:4px solid var(--red); }}
section {{ background:white; border:1px solid var(--line); border-radius:10px; padding:18px;
  margin:16px 0; overflow:auto; }}
h2 {{ margin:0 0 12px; font-size:18px; }} .grid {{ display:grid; grid-template-columns:3fr 1fr; gap:16px; }}
table {{ border-collapse:collapse; width:100%; min-width:680px; }} th {{ background:#f4f5f7; color:var(--muted);
  text-align:left; font-size:12px; position:sticky; top:0; }} th,td {{ border-bottom:1px solid var(--line);
  padding:10px; vertical-align:top; }} tr:hover td {{ background:#fafbfc; }}
a {{ color:var(--blue); text-decoration:none; font-weight:600; }} a:hover {{ text-decoration:underline; }}
.badge {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#ebecf0; white-space:nowrap; }}
.badge.good {{ color:var(--green); background:var(--green-soft); }} .badge.warn {{ color:var(--amber); background:var(--amber-soft); }}
.badge.bad {{ color:var(--red); background:var(--red-soft); }} .row-bad td:first-child {{ border-left:3px solid #ff5630; }}
.row-good td:first-child {{ border-left:3px solid #36b37e; }} .notice {{ border-left:5px solid #ffab00; background:#fffae6; }}
.notice code {{ background:#fff0b3; padding:1px 4px; }} .empty {{ color:var(--muted); text-align:center; }}
.query {{ margin-top:14px; padding:10px 12px; background:rgba(255,255,255,.12); border-radius:7px;
  overflow-wrap:anywhere; font-family:Consolas,monospace; font-size:12px; }}
footer {{ color:var(--muted); text-align:center; padding:16px; }}
@media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} .page {{ padding:12px; }} }}
@media print {{ body {{ background:white; }} .page {{ max-width:none; padding:0; }} section,.kpi,header {{ box-shadow:none; }} }}
</style>
</head>
<body><main class="page">
<header>
  <h1>{APP_NAME}</h1>
  <p>Report date: {html.escape(report_date)} · Generated: {html.escape(generated_at)}</p>
  <p>Jira: {html.escape(base_url)}</p>
  <div class="query">{html.escape(jql)}</div>
</header>
<div class="kpis">{kpi_html}</div>
{unresolved_html}
<div class="grid">
  <section><h2>Owner-wise hygiene</h2>
    <table><thead><tr><th>Owner</th><th>Total</th><th>Compliant</th><th>Score</th>{owner_headers}</tr></thead>
    <tbody>{''.join(owner_rows)}</tbody></table>
  </section>
  <section><h2>Status distribution</h2>
    <table><thead><tr><th>Status</th><th>Items</th></tr></thead><tbody>{status_rows}</tbody></table>
  </section>
</div>
<section><h2>Issue details</h2>
  <table><thead><tr><th>Key</th><th>Summary</th><th>Owner</th><th>Type</th><th>Status</th>
  <th>Due</th><th>Total logged</th><th>Worked this period by</th><th>Updated</th><th>Score</th><th>Missing / attention</th></tr></thead>
  <tbody>{''.join(issue_rows)}</tbody></table>
</section>
<section><h2>Timesheet filled up · {html.escape(timesheet['start_date'])} to {html.escape(timesheet['end_date'])}</h2>
  <table><thead><tr><th>Person</th>{timesheet_date_headers}<th>Total</th><th>Expected</th><th>Status</th></tr></thead>
  <tbody>{''.join(timesheet_rows)}</tbody></table>
</section>
<section><h2>Date-wise worklog details</h2>
  <table><thead><tr><th>Date</th><th>Person</th><th>Issue</th><th>Summary</th><th>Hours</th><th>Comment</th></tr></thead>
  <tbody>{''.join(worklog_rows)}</tbody></table>
</section>
<footer>Read-only report. No Jira issues were modified.</footer>
</main></body></html>"""


def write_reports(
    output_dir: Path,
    report_date: str,
    generated_at: str,
    jql: str,
    base_url: str,
    issues: list[dict[str, Any]],
    summary: dict[str, Any],
    checks: list[ResolvedCheck],
    unresolved: list[dict[str, str]],
    timesheet: dict[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(f"jira_weekly_report_{report_date}")
    html_path = output_dir / f"{stem}.html"
    issue_csv_path = output_dir / f"{stem}_issues.csv"
    owner_csv_path = output_dir / f"{stem}_owners.csv"
    timesheet_csv_path = output_dir / f"{stem}_timesheet.csv"
    worklogs_csv_path = output_dir / f"{stem}_worklogs.csv"
    json_path = output_dir / f"{stem}.json"

    html_path.write_text(
        generate_html(
            report_date,
            generated_at,
            jql,
            base_url,
            issues,
            summary,
            checks,
            unresolved,
            timesheet,
        ),
        encoding="utf-8",
    )

    with issue_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = [
            "key",
            "summary",
            "owner",
            "issue_type",
            "status",
            "priority",
            "due_date",
            "epic_parent",
            "commit_details",
            "description_present",
            "logged_hours",
            "period_logged_hours",
            "worked_by",
            "worklog_available",
            "worklogs",
            "updated_days",
            "status_days",
            "score",
            "compliant",
            "blocked",
            "overdue",
            "missing",
            "advisory",
            "warnings",
            "url",
        ]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for issue in issues:
            row = {key: issue.get(key, "") for key in columns}
            for key in ("missing", "advisory", "warnings"):
                row[key] = "; ".join(issue.get(key, []))
            row["worklogs"] = "; ".join(
                " | ".join(
                    value
                    for value in (
                        item.get("author", ""),
                        item.get("time_spent", ""),
                        item.get("started", ""),
                        item.get("comment", ""),
                    )
                    if value
                )
                for item in issue.get("worklogs", [])
            )
            row["worked_by"] = "; ".join(
                f"{item['person']} | {item['hours']:g}h"
                for item in issue.get("worked_by", [])
            )
            writer.writerow(row)

    check_keys = [check.key for check in checks]
    check_keys.extend(["status_freshness", "in_progress_age", "linked_dependencies"])
    with owner_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["owner", "total", "compliant", "score"] + [
            f"missing_{key}" for key in check_keys
        ]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for owner in summary["owners"]:
            row = {key: owner.get(key, "") for key in ("owner", "total", "compliant", "score")}
            row.update(
                {
                    f"missing_{key}": owner["missing"].get(key, 0)
                    for key in check_keys
                }
            )
            writer.writerow(row)

    with timesheet_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = [
            "person",
            *timesheet["dates"],
            "total_hours",
            "expected_hours",
            "completion_percent",
            "status",
        ]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in timesheet["rows"]:
            row = {
                "person": item["person"],
                **item["daily_hours"],
                "total_hours": item["total_hours"],
                "expected_hours": item["expected_hours"],
                "completion_percent": item["completion"],
                "status": item["status"],
            }
            writer.writerow(row)

    with worklogs_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["date", "person", "issue", "summary", "hours", "time_spent", "comment", "issue_url"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(timesheet["entries"])

    payload = {
        "report_date": report_date,
        "generated_at": generated_at,
        "jira_base_url": base_url,
        "jql": jql,
        "summary": summary,
        "timesheet": timesheet,
        "unresolved_fields": unresolved,
        "issues": issues,
    }
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "html": html_path.resolve(),
        "issues_csv": issue_csv_path.resolve(),
        "owners_csv": owner_csv_path.resolve(),
        "timesheet_csv": timesheet_csv_path.resolve(),
        "worklogs_csv": worklogs_csv_path.resolve(),
        "json": json_path.resolve(),
    }


def sprint_details(value: Any) -> list[dict[str, str]]:
    """Normalize Jira Cloud/Data Center Sprint field values."""
    values = value if isinstance(value, list) else [value]
    details: list[dict[str, str]] = []
    for item in values:
        if not item:
            continue
        if isinstance(item, dict):
            details.append(
                {
                    "id": str(item.get("id", "")).strip(),
                    "name": str(item.get("name", "")).strip(),
                    "state": str(item.get("state", "")).strip().upper(),
                }
            )
            continue
        text_value = str(item)
        detail: dict[str, str] = {"id": "", "name": "", "state": ""}
        for key in ("id", "name", "state"):
            match = re.search(
                rf"(?i)(?:^|[,\[]){key}=([^,\]]+)",
                text_value,
            )
            if match:
                detail[key] = match.group(1).strip()
        detail["state"] = detail["state"].upper()
        if not detail["name"]:
            detail["name"] = text_value.strip()
        details.append(detail)
    return details


def assignment_placement(
    sprints: list[dict[str, str]],
    current_sprint: str,
    status_category: str,
) -> str:
    if status_category.casefold() == "done":
        return "Done"
    if not sprints:
        return "Backlog"
    current_cf = current_sprint.strip().casefold()
    for sprint in sprints:
        if sprint["state"] == "ACTIVE":
            return "Current Sprint"
        if current_cf and (
            sprint["id"].casefold() == current_cf
            or current_cf in sprint["name"].casefold()
        ):
            return "Current Sprint"
    if any(sprint["state"] == "FUTURE" for sprint in sprints):
        return "Future Sprint"
    return "Other Sprint"


def analyze_assignments(
    raw_issues: list[dict[str, Any]],
    sprint_field_ids: list[str],
    current_sprint: str,
    base_url: str,
    team_people: Iterable[str] = (),
) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    issues: list[dict[str, Any]] = []
    for raw in raw_issues:
        fields = raw.get("fields") or {}
        sprint_values = value_for_fields(fields, sprint_field_ids)
        normalized_sprints: list[dict[str, str]] = []
        for value in sprint_values:
            normalized_sprints.extend(sprint_details(value))
        status_object = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        status_category = jira_name(status_object.get("statusCategory"), fallback="")
        due_dt = parse_datetime(fields.get("duedate"))
        due_date = due_dt.date() if due_dt else None
        placement = assignment_placement(
            normalized_sprints,
            current_sprint,
            status_category,
        )
        key = str(raw.get("key", "UNKNOWN"))
        issues.append(
            {
                "key": key,
                "url": f"{base_url}/browse/{quote(key)}",
                "summary": flatten_text(fields.get("summary")) or "(No summary)",
                "owner": jira_name(fields.get("assignee"), fallback="Unassigned"),
                "issue_type": jira_name(fields.get("issuetype")),
                "status": jira_name(fields.get("status")),
                "status_category": status_category,
                "priority": jira_name(fields.get("priority"), fallback=""),
                "placement": placement,
                "sprints": [
                    sprint["name"] or sprint["id"]
                    for sprint in normalized_sprints
                    if sprint["name"] or sprint["id"]
                ],
                "due_date": due_date.isoformat() if due_date else "",
                "overdue": bool(
                    due_date
                    and due_date < today
                    and status_category.casefold() != "done"
                ),
                "updated": str(fields.get("updated", "")),
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        grouped[issue["owner"]].append(issue)
    for person in team_people:
        grouped.setdefault(str(person), [])

    owners = []
    for owner, owner_issues in sorted(
        grouped.items(), key=lambda item: item[0].casefold()
    ):
        owners.append(
            {
                "owner": owner,
                "total": len(owner_issues),
                "current_sprint": sum(
                    1 for item in owner_issues if item["placement"] == "Current Sprint"
                ),
                "future_sprint": sum(
                    1 for item in owner_issues if item["placement"] == "Future Sprint"
                ),
                "backlog": sum(
                    1 for item in owner_issues if item["placement"] == "Backlog"
                ),
                "other_sprint": sum(
                    1 for item in owner_issues if item["placement"] == "Other Sprint"
                ),
                "overdue": sum(1 for item in owner_issues if item["overdue"]),
                "no_due_date": sum(
                    1 for item in owner_issues if not item["due_date"]
                ),
            }
        )

    placement_counts = Counter(item["placement"] for item in issues)
    return {
        "summary": {
            "total": len(issues),
            "current_sprint": placement_counts.get("Current Sprint", 0),
            "future_sprint": placement_counts.get("Future Sprint", 0),
            "backlog": placement_counts.get("Backlog", 0),
            "other_sprint": placement_counts.get("Other Sprint", 0),
            "done": placement_counts.get("Done", 0),
            "overdue": sum(1 for item in issues if item["overdue"]),
            "unassigned": sum(
                1 for item in issues if item["owner"] == "Unassigned"
            ),
            "owners": owners,
        },
        "issues": sorted(
            issues,
            key=lambda item: (
                item["owner"].casefold(),
                item["placement"],
                item["key"],
            ),
        ),
    }


def write_assignment_reports(
    output_dir: Path,
    report_date: str,
    current_sprint: str,
    jql: str,
    data: dict[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(f"jira_assignment_report_{report_date}")
    issue_csv = output_dir / f"{stem}_issues.csv"
    owner_csv = output_dir / f"{stem}_owners.csv"
    json_path = output_dir / f"{stem}.json"

    issue_columns = [
        "key",
        "summary",
        "owner",
        "issue_type",
        "status",
        "priority",
        "placement",
        "sprints",
        "due_date",
        "overdue",
        "updated",
        "url",
    ]
    with issue_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=issue_columns)
        writer.writeheader()
        for issue in data["issues"]:
            row = {key: issue.get(key, "") for key in issue_columns}
            row["sprints"] = "; ".join(issue["sprints"])
            writer.writerow(row)

    owner_columns = [
        "owner",
        "total",
        "current_sprint",
        "future_sprint",
        "backlog",
        "other_sprint",
        "overdue",
        "no_due_date",
    ]
    with owner_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=owner_columns)
        writer.writeheader()
        writer.writerows(data["summary"]["owners"])

    json_path.write_text(
        json.dumps(
            {
                "report_date": report_date,
                "current_sprint": current_sprint,
                "jql": jql,
                **data,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "assignment_issues_csv": issue_csv.resolve(),
        "assignment_owners_csv": owner_csv.resolve(),
        "assignment_json": json_path.resolve(),
    }


def run_assignment_report(
    config: dict[str, Any],
    *,
    config_base_dir: Path,
    current_sprint: str,
    explicit_jql: str | None = None,
    output_dir: Path | None = None,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    jql = (
        explicit_jql.strip()
        if explicit_jql and explicit_jql.strip()
        else str(config.get("assignment_jql", "")).strip()
    )
    if not jql:
        raise ReportError("Set assignment_jql in the configuration.")
    client = JiraClient(config, credentials=credentials)
    fields = client.get_fields()
    resolver = FieldResolver(fields)
    sprint_field_ids = resolver.resolve(["Sprint"])
    if not sprint_field_ids:
        raise ReportError(
            "The Jira Sprint field could not be found for the assignment report."
        )
    requested_fields = list(
        dict.fromkeys(
            [
                "summary",
                "issuetype",
                "status",
                "assignee",
                "priority",
                "created",
                "updated",
                "duedate",
                *sprint_field_ids,
            ]
        )
    )
    raw_issues = client.search(jql, requested_fields)
    data = analyze_assignments(
        raw_issues,
        sprint_field_ids,
        current_sprint,
        client.base_url,
    )
    generated = datetime.now().astimezone()
    selected_output = output_dir or Path(str(config.get("output_dir", "reports")))
    if not selected_output.is_absolute():
        selected_output = config_base_dir / selected_output
    paths = write_assignment_reports(
        selected_output,
        generated.date().isoformat(),
        current_sprint,
        jql,
        data,
    )
    return {
        "report_date": generated.date().isoformat(),
        "generated_at": generated.isoformat(timespec="seconds"),
        "jira_base_url": client.base_url,
        "api_mode": client.mode,
        "current_sprint": current_sprint,
        "jql": jql,
        "summary": data["summary"],
        "issues": data["issues"],
        "paths": paths,
    }


def run_report(
    config: dict[str, Any],
    *,
    config_base_dir: Path,
    sprint: str | None = None,
    explicit_jql: str | None = None,
    output_dir: Path | None = None,
    credentials: dict[str, str] | None = None,
    worklog_start: date | None = None,
    worklog_end: date | None = None,
) -> dict[str, Any]:
    """Run one Jira scan and return both dashboard data and exported paths."""
    jql = build_jql(config, sprint, explicit_jql)
    client = JiraClient(config, credentials=credentials)
    jira_fields = client.get_fields()
    resolver = FieldResolver(jira_fields)
    checks, unresolved = resolve_checks(config, resolver)
    helper_fields = {"flagged": resolver.resolve(["Flagged", "Impediment"])}

    requested_fields = list(BASE_SEARCH_FIELDS)
    for check in checks:
        requested_fields.extend(check.field_ids)
    for field_ids in helper_fields.values():
        requested_fields.extend(field_ids)
    requested_fields = list(dict.fromkeys(requested_fields))

    raw_issues = client.search(jql, requested_fields)
    today = datetime.now().astimezone().date()
    period_start = worklog_start or (today - timedelta(days=today.weekday()))
    period_end = worklog_end or (period_start + timedelta(days=6))
    if period_end < period_start:
        raise ReportError("The worklog end date must be on or after the start date.")
    if (period_end - period_start).days > 62:
        raise ReportError("The worklog date range cannot exceed 63 days.")
    thresholds = config.get("thresholds", {})
    if not isinstance(thresholds, dict):
        thresholds = {}
    worklog_errors = attach_period_worklogs(
        client,
        raw_issues,
        period_start,
        period_end,
        workers=int(thresholds.get("worklog_workers", 4)),
    )
    issues = analyze_issues(
        raw_issues,
        checks,
        helper_fields,
        config,
        client.base_url,
    )
    summary = summarize(issues, checks)
    timesheet = build_timesheet(
        issues,
        period_start,
        period_end,
        expected_hours_per_day=float(
            thresholds.get("expected_hours_per_day", 8)
        ),
        team_people=resolve_team_member_names(client, config),
    )
    generated = datetime.now().astimezone()
    report_date = generated.date().isoformat()
    selected_output = output_dir or Path(str(config.get("output_dir", "reports")))
    if not selected_output.is_absolute():
        selected_output = config_base_dir / selected_output
    paths = write_reports(
        selected_output,
        report_date,
        generated.isoformat(timespec="seconds"),
        jql,
        client.base_url,
        issues,
        summary,
        checks,
        unresolved,
        timesheet,
    )
    return {
        "report_date": report_date,
        "generated_at": generated.isoformat(timespec="seconds"),
        "jira_base_url": client.base_url,
        "api_mode": client.mode,
        "jql": jql,
        "issues": issues,
        "summary": summary,
        "timesheet": timesheet,
        "worklog_errors": worklog_errors,
        "unresolved": unresolved,
        "paths": paths,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("jira_weekly_config.json"),
        help="Path to the JSON configuration file.",
    )
    parser.add_argument("--sprint", help="Override the sprint ID/name in jql_template.")
    parser.add_argument("--jql", help="Use this complete JQL instead of the configured query.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the configured report output directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config_path = args.config.expanduser().resolve()
        config = load_json(config_path)
        preview_jql = build_jql(config, args.sprint, args.jql)
        print(f"Running JQL: {preview_jql}")
        result = run_report(
            config,
            config_base_dir=config_path.parent,
            sprint=args.sprint,
            explicit_jql=args.jql,
            output_dir=args.output_dir,
        )
        summary = result["summary"]
        print(
            f"Connected to {result['jira_base_url']} ({result['api_mode']}); "
            f"found {summary['total']} issue(s)."
        )
        print(
            f"Complete: {summary['score']}% hygiene, "
            f"{summary['compliant']}/{summary['total']} fully compliant."
        )
        if result["unresolved"]:
            print(
                f"Attention: {len(result['unresolved'])} configured field(s) could not be resolved. "
                "See the HTML report."
            )
        for name, path in result["paths"].items():
            print(f"{name.upper()}_PATH={path}")
        return 0
    except ReportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
