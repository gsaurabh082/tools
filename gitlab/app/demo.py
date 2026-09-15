from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _iso(days: int = 0, hours: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


def demo_payload(group_url: str) -> dict[str, Any]:
    projects = [
        {
            "id": 101,
            "name": "Acquisition Portal",
            "path_with_namespace": "pia_restricted/platform/acquisition-portal",
            "namespace": "platform",
            "web_url": f"{group_url}/platform/acquisition-portal",
            "open_mr_count": 3,
            "attention_count": 2,
            "last_activity_at": _iso(hours=2),
        },
        {
            "id": 102,
            "name": "Workflow Services",
            "path_with_namespace": "pia_restricted/services/workflow-services",
            "namespace": "services",
            "web_url": f"{group_url}/services/workflow-services",
            "open_mr_count": 2,
            "attention_count": 1,
            "last_activity_at": _iso(hours=6),
        },
        {
            "id": 103,
            "name": "Quality Toolkit",
            "path_with_namespace": "pia_restricted/tooling/quality-toolkit",
            "namespace": "tooling",
            "web_url": f"{group_url}/tooling/quality-toolkit",
            "open_mr_count": 2,
            "attention_count": 1,
            "last_activity_at": _iso(days=1),
        },
        {
            "id": 104,
            "name": "Analytics Console",
            "path_with_namespace": "pia_restricted/apps/analytics-console",
            "namespace": "apps",
            "web_url": f"{group_url}/apps/analytics-console",
            "open_mr_count": 1,
            "attention_count": 1,
            "last_activity_at": _iso(days=8),
        },
    ]

    merge_requests = [
        _mr(
            101,
            248,
            "Add exam-level acquisition progress",
            "pia_restricted/platform/acquisition-portal",
            "Maya S.",
            "MS",
            "needs_approval",
            "passed",
            hours=2,
            labels=["feature", "frontend"],
            reviewers=["You", "Arun K."],
            web_url=f"{group_url}/platform/acquisition-portal/-/merge_requests/248",
            description="""## Summary
Adds visible acquisition progress for the exam workflow.

Work item: SD-248

## Testing evidence
- Unit tests pass locally.
- Verified the loading and completed states in the browser.""",
        ),
        _mr(
            102,
            87,
            "Handle retry state for export jobs",
            "pia_restricted/services/workflow-services",
            "You",
            "YO",
            "waiting_reply",
            "passed",
            hours=7,
            labels=["backend", "resilience"],
            reviewers=["Neha P."],
            web_url=f"{group_url}/services/workflow-services/-/merge_requests/87",
            description="""SD-87

## Summary
Retries are now recorded before an export job is re-queued.

## Testing
- Added coverage for the retry limit and timeout cases.""",
        ),
        _mr(
            101,
            251,
            "Refactor study-card focus states",
            "pia_restricted/platform/acquisition-portal",
            "Jon Bell",
            "JB",
            "assigned",
            "running",
            hours=10,
            labels=["accessibility"],
            reviewers=["You"],
            web_url=f"{group_url}/platform/acquisition-portal/-/merge_requests/251",
        ),
        _mr(
            103,
            44,
            "Upgrade static analysis rules",
            "pia_restricted/tooling/quality-toolkit",
            "Priya R.",
            "PR",
            "needs_approval",
            "failed",
            days=1,
            labels=["quality", "dependencies"],
            reviewers=["You"],
            web_url=f"{group_url}/tooling/quality-toolkit/-/merge_requests/44",
            description="""## Summary
Updates static-analysis configuration for SD-44.

## Testing
- Ran the updated checks locally.""",
        ),
        _mr(
            101,
            245,
            "Draft: simplify protocol validation",
            "pia_restricted/platform/acquisition-portal",
            "You",
            "YO",
            "draft",
            "passed",
            days=2,
            labels=["draft", "cleanup"],
            reviewers=[],
            web_url=f"{group_url}/platform/acquisition-portal/-/merge_requests/245",
            draft=True,
        ),
        _mr(
            102,
            81,
            "Expose workflow audit events",
            "pia_restricted/services/workflow-services",
            "Sofia D.",
            "SD",
            "healthy",
            "passed",
            days=3,
            labels=["api"],
            reviewers=["Arun K."],
            web_url=f"{group_url}/services/workflow-services/-/merge_requests/81",
            description="""## Summary
Exposes audit events for downstream consumers. Tracks SD-81.

## Test evidence
- Unit and integration suites pass.
- QA verified the event payload against the acceptance criteria.""",
        ),
        _mr(
            104,
            19,
            "Improve large-series chart rendering",
            "pia_restricted/apps/analytics-console",
            "You",
            "YO",
            "stale",
            "passed",
            days=8,
            labels=["performance"],
            reviewers=["Maya S."],
            web_url=f"{group_url}/apps/analytics-console/-/merge_requests/19",
        ),
        _mr(
            103,
            47,
            "Document local integration test flow",
            "pia_restricted/tooling/quality-toolkit",
            "Lee W.",
            "LW",
            "healthy",
            "none",
            days=4,
            labels=["documentation"],
            reviewers=[],
            web_url=f"{group_url}/tooling/quality-toolkit/-/merge_requests/47",
            description="""## Summary
Documents the local integration-test flow for SD-47.

## Testing
- Followed the documented flow on a clean workstation.""",
        ),
    ]

    tasks = [
        {
            "id": 601,
            "iid": 72,
            "title": "Validate export timeout with production-sized data",
            "project_path": "pia_restricted/services/workflow-services",
            "labels": ["validation", "P1"],
            "due_date": (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
            "updated_at": _iso(hours=4),
            "web_url": f"{group_url}/services/workflow-services/-/issues/72",
        },
        {
            "id": 602,
            "iid": 113,
            "title": "Review accessibility acceptance criteria",
            "project_path": "pia_restricted/platform/acquisition-portal",
            "labels": ["accessibility"],
            "due_date": None,
            "updated_at": _iso(days=1),
            "web_url": f"{group_url}/platform/acquisition-portal/-/issues/113",
        },
    ]

    in_review = [
        {**mr, "state": "opened", "merged_at": None, "closed_at": None}
        for mr in merge_requests
        if mr["author"]["name"] == "You"
    ]
    my_merge_requests = {
        "merged": [
            _history_mr(
                102,
                79,
                "Cache workflow permission lookups",
                "pia_restricted/services/workflow-services",
                "merged",
                "passed",
                group_url,
                merged_days=2,
                created_days=6,
                labels=["backend", "performance"],
            ),
            _history_mr(
                101,
                240,
                "Add keyboard navigation to study cards",
                "pia_restricted/platform/acquisition-portal",
                "merged",
                "passed",
                group_url,
                merged_days=9,
                created_days=13,
                labels=["accessibility", "frontend"],
            ),
        ],
        "in_review": in_review,
        "closed": [
            _history_mr(
                104,
                12,
                "Prototype alternate chart engine",
                "pia_restricted/apps/analytics-console",
                "closed",
                "failed",
                group_url,
                closed_days=15,
                created_days=21,
                labels=["spike"],
            ),
        ],
    }

    return {
        "mode": "demo",
        "group": {"name": "PIA Restricted", "path": "pia_restricted", "web_url": group_url},
        "current_user": {"id": 1, "name": "You", "username": "you"},
        "summary": {
            "open_merge_requests": len(merge_requests),
            "needs_my_approval": 2,
            "waiting_for_reply": 1,
            "assigned_to_me": 2,
            "my_open_issues": len(tasks),
            "stale": 1,
            "failed_pipelines": 1,
        },
        "projects": projects,
        "merge_requests": merge_requests,
        "my_merge_requests": my_merge_requests,
        "tasks": tasks,
        "refreshed_at": datetime.now(UTC).isoformat(),
        "warnings": ["Demo data is shown until GITLAB_TOKEN is added to .env."],
    }


def _mr(
    project_id: int,
    iid: int,
    title: str,
    project_path: str,
    author: str,
    initials: str,
    attention: str,
    pipeline: str,
    *,
    days: int = 0,
    hours: int = 0,
    labels: list[str],
    reviewers: list[str],
    web_url: str,
    draft: bool = False,
    description: str = "",
) -> dict[str, Any]:
    return {
        "id": project_id * 1000 + iid,
        "iid": iid,
        "project_id": project_id,
        "title": title,
        "project_path": project_path,
        "source_branch": f"feature/{'SD-' + str(iid) if description else 'mr-' + str(iid)}",
        "target_branch": "main",
        "author": {"name": author, "initials": initials, "username": author.lower().replace(" ", ".")},
        "reviewers": reviewers,
        "assignees": ["You"] if attention == "assigned" else [],
        "labels": labels,
        "draft": draft,
        "attention": attention,
        "attention_label": {
            "needs_approval": "Needs your approval",
            "waiting_reply": "Waiting for reply",
            "assigned": "Assigned to you",
            "draft": "Draft",
            "stale": "Stale",
            "healthy": "On track",
        }[attention],
        "pipeline_status": pipeline,
        "approved_by_me": False,
        "approvals_left": 1 if attention == "needs_approval" else 0,
        "updated_at": _iso(days=days, hours=hours),
        "created_at": _iso(days=days + 2, hours=hours),
        "web_url": web_url,
        "user_notes_count": 3 if attention == "waiting_reply" else 1,
        "has_conflicts": False,
        "description": description,
        "blocking_discussions_resolved": iid != 44,
        "detailed_merge_status": "mergeable" if pipeline == "passed" and not draft else "ci_must_pass",
        "changes_count": {248: "14", 81: "9", 47: "3"}.get(iid, "6"),
        "commits_count": {248: 3, 81: 2, 47: 1}.get(iid, 1),
    }


def _history_mr(
    project_id: int,
    iid: int,
    title: str,
    project_path: str,
    state: str,
    pipeline: str,
    group_url: str,
    *,
    merged_days: int = 0,
    closed_days: int = 0,
    created_days: int = 0,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """A lightweight authored merge request for the 'Merge requests by me' demo view."""
    slug = project_path.split("/", 1)[-1]
    return {
        "id": project_id * 1000 + iid,
        "iid": iid,
        "project_id": project_id,
        "title": title,
        "project_path": project_path,
        "source_branch": f"feature/mr-{iid}",
        "target_branch": "main",
        "state": state,
        "author": {"id": 1, "name": "You", "username": "you", "initials": "YO"},
        "labels": labels or [],
        "draft": False,
        "pipeline_status": pipeline,
        "created_at": _iso(days=created_days),
        "updated_at": _iso(days=merged_days or closed_days),
        "merged_at": _iso(days=merged_days) if state == "merged" else None,
        "closed_at": _iso(days=closed_days) if state == "closed" else None,
        "web_url": f"{group_url}/{slug}/-/merge_requests/{iid}",
        "user_notes_count": 2,
    }
