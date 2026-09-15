"""Match GitLab merge requests to Jira issues and flag hygiene gaps.

This module is the glue between ``gitlab_client.GitLabClient`` and the Jira
report produced by ``jira_weekly_report``. It never writes to GitLab. Writing
to Jira (posting a comment) is the caller's responsibility via
``jira_weekly_report.JiraClient.add_comment`` using the text built here.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from gitlab_client import GitLabClient, GitLabError
from jira_weekly_report import extract_issue_keys, flatten_text

MIN_DESCRIPTION_LENGTH = 15
MR_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>]+/-/merge_requests/(\d+)", re.IGNORECASE
)


def _iso_updated_after(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _project_path(mr: dict[str, Any]) -> str:
    references = mr.get("references") or {}
    full = str(references.get("full", ""))
    if "!" in full:
        return full.split("!", 1)[0]
    return str(mr.get("project_id", ""))


def has_description(mr: dict[str, Any]) -> bool:
    return len((mr.get("description") or "").strip()) >= MIN_DESCRIPTION_LENGTH


def _people(raw_list: Any) -> list[str]:
    names: list[str] = []
    for person in raw_list or []:
        if isinstance(person, dict):
            names.append(str(person.get("name") or person.get("username") or "Unknown"))
    return names


def _usernames(mr: dict[str, Any]) -> set[str]:
    """All GitLab usernames touching this MR: author, assignees, reviewers."""
    people: list[dict[str, Any]] = [mr.get("author") or {}]
    people.extend(p for p in (mr.get("assignees") or []) if isinstance(p, dict))
    people.extend(p for p in (mr.get("reviewers") or []) if isinstance(p, dict))
    return {
        str(person["username"]).strip().casefold()
        for person in people
        if isinstance(person, dict) and person.get("username")
    }


def is_team_relevant(mr: dict[str, Any], team_usernames: set[str] | None) -> bool:
    """True when no team filter is configured, or the MR touches a configured
    team member as author, assignee, or reviewer."""
    if not team_usernames:
        return True
    return bool(_usernames(mr) & team_usernames)


def normalize_merge_request(mr: dict[str, Any]) -> dict[str, Any]:
    author = mr.get("author") or {}
    keys = extract_issue_keys(
        str(mr.get("source_branch", "")),
        str(mr.get("title", "")),
        str(mr.get("description", "")),
    )
    return {
        "project_id": int(mr.get("project_id", 0)),
        "iid": int(mr.get("iid", 0)),
        "title": mr.get("title", "Untitled merge request"),
        "description": mr.get("description", "") or "",
        "has_description": has_description(mr),
        "state": mr.get("state", "opened"),
        "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
        "source_branch": mr.get("source_branch", ""),
        "target_branch": mr.get("target_branch", ""),
        "project_path": _project_path(mr),
        "author": author.get("name") or author.get("username") or "Unknown",
        "assignees": _people(mr.get("assignees")),
        "reviewers": _people(mr.get("reviewers")),
        "web_url": mr.get("web_url", ""),
        "updated_at": mr.get("updated_at", ""),
        "issue_keys": keys,
        "issue_keys_source": "branch_title_description" if keys else "",
    }


def fetch_relevant_merge_requests(
    client: GitLabClient,
    merged_lookback_days: int = 30,
    team_usernames: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch open MRs plus recently merged MRs, normalized and deduplicated.

    When ``team_usernames`` is given (a set of casefolded GitLab usernames),
    only merge requests where the author, an assignee, or a reviewer is one
    of those usernames are kept. This keeps the sync scoped to the team
    instead of the whole GitLab group.
    """
    updated_after = _iso_updated_after(merged_lookback_days)
    opened = client.group_merge_requests(state="opened")
    merged = client.group_merge_requests(state="merged", updated_after=updated_after)
    seen: set[tuple[int, int]] = set()
    normalized: list[dict[str, Any]] = []
    for raw in [*opened, *merged]:
        key = (int(raw.get("project_id", 0)), int(raw.get("iid", 0)))
        if key in seen:
            continue
        seen.add(key)
        if not is_team_relevant(raw, team_usernames):
            continue
        normalized.append(normalize_merge_request(raw))
    return normalized


def enrich_orphans_with_notes(
    client: GitLabClient, merge_requests: list[dict[str, Any]]
) -> None:
    """For MRs with no detectable Jira key, check their GitLab comments too.

    Mutates ``merge_requests`` in place. Best-effort: a merge request whose
    notes can't be fetched is left as-is rather than failing the whole sync.
    Only runs for MRs that are still unmatched, so the extra API calls stay
    bounded to the ambiguous subset instead of every merge request.
    """
    for mr in merge_requests:
        if mr["issue_keys"] or mr["state"] == "closed":
            continue
        try:
            notes = client.merge_request_notes(mr["project_id"], mr["iid"])
        except GitLabError:
            continue
        note_texts = [
            str(note.get("body", ""))
            for note in notes
            if isinstance(note, dict) and not note.get("system")
        ]
        keys = extract_issue_keys(*note_texts)
        if keys:
            mr["issue_keys"] = keys
            mr["issue_keys_source"] = "gitlab_comment"


def extract_mr_references(*texts: str) -> list[tuple[str, int]]:
    """Return unique (url, iid) pairs for GitLab MR URLs found in the texts."""
    found: list[tuple[str, int]] = []
    for text in texts:
        if not text:
            continue
        for match in MR_URL_PATTERN.finditer(text):
            pair = (match.group(0), int(match.group(1)))
            if pair not in found:
                found.append(pair)
    return found


def _mr_base_url(url: str) -> str:
    return re.sub(r"/-/merge_requests/\d+.*$", "", url).rstrip("/")


def find_mr_by_url_reference(
    merge_requests: list[dict[str, Any]], reference_url: str, iid: int
) -> dict[str, Any] | None:
    target_base = _mr_base_url(reference_url).casefold()
    for mr in merge_requests:
        if mr["iid"] != iid:
            continue
        if _mr_base_url(mr["web_url"]).casefold() == target_base:
            return mr
    return None


def enrich_issues_with_jira_comment_links(
    jira_client: Any,
    issues: list[dict[str, Any]],
    merge_requests: list[dict[str, Any]],
    issue_gitlab: dict[str, dict[str, Any]],
) -> None:
    """For issues with no linked MR, scan their Jira comments for a pasted
    GitLab merge request URL and cross-reference it against the fetched MR
    list. Mutates ``issue_gitlab`` in place. Best-effort and bounded to only
    the issues that are still unlinked after branch/title/description and
    GitLab-comment matching.
    """
    candidates = [
        issue
        for issue in issues
        if issue_gitlab.get(issue["key"], {}).get("flags", {}).get("no_linked_mr")
    ]
    if not candidates:
        return
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(jira_client.get_issue_comments, issue["key"]): issue["key"]
            for issue in candidates
        }
        for future in as_completed(futures):
            issue_key = futures[future]
            try:
                comments = future.result()
            except Exception:  # noqa: BLE001 - best-effort enhancement only
                continue
            references = extract_mr_references(*comments)
            if not references:
                continue
            matched: list[dict[str, Any]] = []
            for url, iid in references:
                mr = find_mr_by_url_reference(merge_requests, url, iid)
                if mr and mr not in matched:
                    matched.append(mr)
            if matched:
                entry = issue_gitlab.setdefault(
                    issue_key, {"matched_merge_requests": [], "flags": {}}
                )
                entry["matched_merge_requests"] = matched
                entry["flags"]["no_linked_mr"] = False
                entry["flags"]["jira_missing_mr_link"] = False
                entry["link_source"] = "jira_comment"


def build_issue_index(merge_requests: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for mr in merge_requests:
        for key in mr["issue_keys"]:
            index.setdefault(key, []).append(mr)
    return index


def sync_report(
    issues: list[dict[str, Any]], merge_requests: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute per-issue GitLab hygiene info plus a group-wide orphan list.

    ``issues`` is the list produced by ``jira_weekly_report.analyze_issues``
    (each item has at least ``key``, ``missing_keys`` and ``status_category``).
    """
    index = build_issue_index(merge_requests)
    issue_gitlab: dict[str, dict[str, Any]] = {}

    for issue in issues:
        key = issue["key"]
        matches = index.get(key, [])
        status_category_cf = str(issue.get("status_category", "")).casefold()
        is_done = status_category_cf == "done"
        # Only flag a missing MR link once work has actually started — a
        # "To Do" issue naturally has no merge request yet.
        is_active = status_category_cf in {"in progress", "done"}
        no_linked_mr = is_active and not matches
        mr_missing_description = any(
            not mr["has_description"] for mr in matches if mr["state"] != "closed"
        )
        mr_merged_issue_open = any(
            mr["state"] == "merged" for mr in matches
        ) and not is_done
        jira_missing_mr_link = no_linked_mr and (
            "commit_ids" in (issue.get("missing_keys") or [])
        )
        issue_gitlab[key] = {
            "matched_merge_requests": matches,
            "flags": {
                "no_linked_mr": no_linked_mr,
                "mr_missing_description": mr_missing_description,
                "mr_merged_issue_open": mr_merged_issue_open,
                "jira_missing_mr_link": jira_missing_mr_link,
            },
        }

    # An MR is "orphan" when it carries no detectable Jira key at all.
    orphan_merge_requests = [
        mr for mr in merge_requests if mr["state"] == "opened" and not mr["issue_keys"]
    ]

    return {
        "issue_gitlab": issue_gitlab,
        "orphan_merge_requests": orphan_merge_requests,
        "summary": summarize_sync(issue_gitlab, orphan_merge_requests),
    }


def summarize_sync(
    issue_gitlab: dict[str, dict[str, Any]],
    orphan_merge_requests: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "issues_with_no_linked_mr": sum(
            1 for item in issue_gitlab.values() if item["flags"]["no_linked_mr"]
        ),
        "issues_with_mr_missing_description": sum(
            1 for item in issue_gitlab.values() if item["flags"]["mr_missing_description"]
        ),
        "issues_with_merged_mr_still_open": sum(
            1 for item in issue_gitlab.values() if item["flags"]["mr_merged_issue_open"]
        ),
        "issues_missing_mr_link_in_jira": sum(
            1 for item in issue_gitlab.values() if item["flags"]["jira_missing_mr_link"]
        ),
        "orphan_merge_request_count": len(orphan_merge_requests),
    }


def build_mr_comment(issue_key: str, mr: dict[str, Any]) -> str:
    """Build the Jira comment body describing a GitLab merge request."""
    state_label = {"opened": "Open", "merged": "Merged", "closed": "Closed"}.get(
        mr.get("state", ""), mr.get("state", "Unknown")
    )
    description = flatten_text(mr.get("description")) or "(No description provided in the merge request.)"
    lines = [
        f"GitLab merge request: {mr.get('title', 'Untitled merge request')}",
        f"Project: {mr.get('project_path', '')}",
        f"Status: {state_label}" + (" (draft)" if mr.get("draft") else ""),
        f"Branch: {mr.get('source_branch', '')} -> {mr.get('target_branch', '')}",
        f"Author: {mr.get('author', 'Unknown')}",
        f"Link: {mr.get('web_url', '')}",
        "",
        "Description:",
        description,
    ]
    return "\n".join(lines)


def find_merge_request(
    merge_requests: list[dict[str, Any]], project_id: int, iid: int
) -> dict[str, Any] | None:
    for mr in merge_requests:
        if mr["project_id"] == project_id and mr["iid"] == iid:
            return mr
    return None
