"""Ad-hoc engineering-comment analysis for a pasted list of Jira issue keys.

Given free-form input (one key per line, comma-separated, or slash-grouped
like "SD-1/SD-2/SD-3" to combine linked tickets into a single row), this pulls
each ticket's most relevant engineering comment, flags any DoseWatch (DW)
version mentioned, and calls out tickets open for more than a year.

Reuses JiraClient from jira_weekly_report.py for the actual Jira connection --
same auth/base_url handling as every other report in this app. Read-only.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from jira_weekly_report import JiraClient, ReportError, flatten_text, safe_filename

ONE_YEAR_DAYS = 365
MAX_TICKETS = 200

ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
DW_RE = re.compile(r"DW[\s\-_]*v?\.?\s*(\d+(?:\.\d+){1,4})", re.IGNORECASE)
# DoseWatch (DW) versions are also often written bare, e.g. "reproducible in
# 3.4.2" -- catch those as a fallback when "DW" isn't spelled out.
BARE_VERSION_RE = re.compile(
    r"\b(?:version\s+|in\s+|on\s+|to\s+)?(\d+\.\d+(?:\.\d+){1,3})\b", re.IGNORECASE
)


def parse_ticket_groups(raw_input: str) -> list[list[str]]:
    """Split free-form pasted text into issue-key groups.

    Each line/comma-separated token becomes one output row; a "/"-joined
    token (e.g. "SD-1/SD-2") is kept together as one combined row.
    """
    groups: list[list[str]] = []
    for line in raw_input.splitlines():
        for token in re.split(r"[,\s]+", line.strip()):
            token = token.strip()
            if not token:
                continue
            keys = [part.strip().upper() for part in token.split("/") if part.strip()]
            keys = [key for key in keys if ISSUE_KEY_RE.match(key)]
            if keys:
                groups.append(keys)
    return groups


def find_dw(text: str) -> str | None:
    match = DW_RE.search(text)
    if match:
        return f"DW {match.group(1)}"
    match = BARE_VERSION_RE.search(text)
    if match:
        return f"DW {match.group(1)} (implied, not spelled out as 'DW' in comment)"
    return None


def get_raw_comments(client: JiraClient, issue_key: str) -> list[dict[str, str]]:
    query = urlencode({"maxResults": 50, "orderBy": "-created"})
    data = client.request(
        "GET",
        f"/rest/api/{client.api_version}/issue/{quote(issue_key)}/comment?{query}",
    )
    comments = data.get("comments", []) if isinstance(data, dict) else []
    out = []
    for item in comments:
        if not isinstance(item, dict) or not item.get("body"):
            continue
        out.append(
            {
                "created": str(item.get("created", ""))[:10],
                "author": (item.get("author") or {}).get("displayName", "Unknown"),
                "text": flatten_text(item.get("body")),
            }
        )
    return out


def pick_engineering_comment(comments: list[dict[str, str]]) -> dict[str, str] | None:
    if not comments:
        return None
    # Prefer the most recent comment that spells out "DW x.y.z" explicitly,
    # then the most recent one with any dotted version number, else latest overall.
    explicit_dw = [c for c in comments if DW_RE.search(c["text"])]
    if explicit_dw:
        return max(explicit_dw, key=lambda c: c["created"])
    bare_version = [c for c in comments if BARE_VERSION_RE.search(c["text"])]
    if bare_version:
        return max(bare_version, key=lambda c: c["created"])
    return max(comments, key=lambda c: c["created"])


def compute_age(created_raw: str, today: date) -> tuple[int | None, str, bool]:
    """Return (age_days, human label, is_older_than_1_year)."""
    if not created_raw:
        return None, "", False
    try:
        created_date = date.fromisoformat(created_raw)
    except ValueError:
        return None, "", False
    age_days = (today - created_date).days
    if age_days >= ONE_YEAR_DAYS:
        years = age_days / 365.25
        return age_days, f"{years:.1f} yrs old", True
    return age_days, f"{age_days} days old", False


def build_note(key: str, fields: dict[str, Any], comments: list[dict[str, str]], today: date) -> str:
    created_raw = str(fields.get("created") or "")[:10]
    _, age_label, is_old = compute_age(created_raw, today)
    affected_versions = ", ".join(
        v.get("name") for v in fields.get("versions") or [] if v.get("name")
    )
    summary = (fields.get("summary") or "").strip()

    chosen = pick_engineering_comment(comments)
    dw = find_dw(chosen["text"]) if chosen else None

    header_bits = [f"created {created_raw or 'unknown'}"]
    if age_label:
        header_bits.append(age_label)
    if chosen:
        header_bits.append(f"commented {chosen['created']}")
        if dw:
            header_bits.append(dw)
    header = f"{key} [{', '.join(header_bits)}]"

    if chosen:
        snippet = re.sub(r"[\r\n\xa0]+", " ", chosen["text"]).strip()
        snippet = re.sub(r"\s{2,}", " ", snippet)
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        body = f"({chosen['author']}): {snippet}"
    else:
        body = "No engineering comment found."

    if is_old:
        version_bit = f"DW {affected_versions}" if affected_versions else "no affected version set on the ticket"
        aging_note = (
            f"OPEN {age_label.replace(' old', '')} since {created_raw} "
            f"against {version_bit}. Summary: {summary}."
        )
        return f"{header}: {aging_note} {body}"

    return f"{header}: {body}"


FIELDNAMES = [
    "SD",
    "Summary",
    "Status",
    "Created",
    "Age",
    "Older than 1 year",
    "Affected Version(s)",
    "Fix Version(s)",
    "Engineering Comment",
]


def _analyze(client: JiraClient, groups: list[list[str]]) -> list[dict[str, Any]]:
    all_keys = [key for group in groups for key in group]
    fields = ["summary", "status", "fixVersions", "versions", "created"]
    jql = "key in (" + ",".join(all_keys) + ")"
    issues = client.search(jql, fields)
    issue_by_key = {item["key"]: item for item in issues}

    today = date.today()
    rows: list[dict[str, Any]] = []
    for group in groups:
        row_label = "/".join(group)
        summaries: list[str] = []
        statuses: list[str] = []
        fix_versions: set[str] = set()
        affected_versions_all: set[str] = set()
        created_dates: list[str] = []
        engineering_notes: list[str] = []

        for key in group:
            issue = issue_by_key.get(key)
            if not issue:
                engineering_notes.append(f"{key}: NOT FOUND in Jira")
                continue
            issue_fields = issue.get("fields", {})
            summaries.append(issue_fields.get("summary", ""))
            statuses.append((issue_fields.get("status") or {}).get("name", ""))
            for fv in issue_fields.get("fixVersions") or []:
                name = fv.get("name")
                if name:
                    fix_versions.add(name)
            for v in issue_fields.get("versions") or []:
                name = v.get("name")
                if name:
                    affected_versions_all.add(name)
            created_raw = str(issue_fields.get("created") or "")[:10]
            if created_raw:
                created_dates.append(created_raw)

            comments = get_raw_comments(client, key)
            engineering_notes.append(build_note(key, issue_fields, comments, today))

        oldest_created = min(created_dates) if created_dates else ""
        _, age_label, is_old = compute_age(oldest_created, today)

        rows.append(
            {
                "SD": row_label,
                "Summary": " | ".join(s for s in summaries if s),
                "Status": " | ".join(s for s in statuses if s),
                "Created": oldest_created or "-",
                "Age": age_label or "-",
                "Older than 1 year": "Yes" if is_old else "No",
                "Affected Version(s)": ", ".join(sorted(affected_versions_all)) or "-",
                "Fix Version(s)": ", ".join(sorted(fix_versions)) or "-",
                "Engineering Comment": "\n".join(engineering_notes) or "No comments found",
            }
        )
    return rows


def write_sd_analysis_reports(
    output_dir: Path, generated: datetime, rows: list[dict[str, Any]]
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(
        f"sd_ticket_analysis_{generated.date().isoformat()}_{generated.strftime('%H%M%S')}"
    )
    csv_path = output_dir / f"{stem}.csv"
    json_path = output_dir / f"{stem}.json"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(
        json.dumps(
            {"generated_at": generated.isoformat(timespec="seconds"), "rows": rows},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "sd_analysis_csv": csv_path.resolve(),
        "sd_analysis_json": json_path.resolve(),
    }


def run_sd_ticket_analysis(
    config: dict[str, Any],
    *,
    raw_input: str,
    output_dir: Path,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    groups = parse_ticket_groups(raw_input)
    if not groups:
        raise ReportError(
            "Enter at least one Jira issue key (e.g. SD-1234), one per line."
        )
    if len(groups) > MAX_TICKETS:
        raise ReportError(f"Limit a single scan to {MAX_TICKETS} rows or fewer.")

    client = JiraClient(config, credentials=credentials)
    rows = _analyze(client, groups)

    generated = datetime.now().astimezone()
    paths = write_sd_analysis_reports(output_dir, generated, rows)
    found_count = sum(
        1 for row in rows if "NOT FOUND" not in row["Engineering Comment"]
    )

    return {
        "generated_at": generated.isoformat(timespec="seconds"),
        "report_date": generated.date().isoformat(),
        "jira_base_url": client.base_url,
        "api_mode": client.mode,
        "requested_count": len(groups),
        "found_count": found_count,
        "rows": rows,
        "paths": paths,
    }
