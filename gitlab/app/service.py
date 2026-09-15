from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .claude_cli import ClaudeCliError, run_claude
from .config import Settings
from .demo import demo_payload
from .gitlab import GitLabClient, GitLabError
from .review_paths import get_override, set_override

_BASE_DIR = Path(__file__).resolve().parent.parent
_REVIEW_STORE = _BASE_DIR / "review_paths.json"


def _checkout_candidates(root: str, project_path: str, slug: str) -> list[str]:
    """Likely local folder paths for a project, under the configured root."""
    root = (root or "").strip()
    if not root:
        return []
    parts = [part for part in (project_path or "").split("/") if part]
    names = [slug, parts[-1] if parts else "", os.sep.join(parts) if parts else ""]
    seen: set[str] = set()
    candidates: list[str] = []
    for name in names:
        clean = (name or "").replace("..", "").strip("/\\")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        candidates.append(os.path.join(root, clean))
    return candidates


def _resolve_checkout(
    settings: Settings, project_id: int, project_path: str, slug: str
) -> dict[str, Any]:
    """Find the local checkout folder for a project (saved override, then auto)."""
    root = settings.code_review_root
    override = get_override(_REVIEW_STORE, project_id)
    if override and os.path.isdir(override):
        return {"root": root, "path": override, "found": True, "source": "saved"}
    for candidate in _checkout_candidates(root, project_path, slug):
        if os.path.isdir(candidate):
            return {"root": root, "path": candidate, "found": True, "source": "auto"}
    return {"root": root, "path": None, "found": False, "source": None}


SD_TICKET_PATTERN = re.compile(r"\bSD-\d+\b", re.IGNORECASE)
TEST_EVIDENCE_PATTERN = re.compile(
    r"\b(?:tests?|testing|qa|validation|validated|verification|verified)\b",
    re.IGNORECASE,
)
NO_TEST_EVIDENCE_PATTERN = re.compile(
    r"\b(?:no tests?(?: were)? run|not tested|tests? (?:not|required)?\s*(?:n/?a|none)|n/?a)\b",
    re.IGNORECASE,
)
DOCUMENTATION_CONTEXT_PATTERN = re.compile(
    r"\b(?:docs?|documentation|wiki|readme|user guide|manual|release notes?)\b",
    re.IGNORECASE,
)
DOCUMENTATION_LINK_PATTERN = re.compile(
    r"(?:https?://[^\s)\]]+/(?:-|wikis?)/[^\s)\]]+|https?://[^\s)\]]+/wikis?[^\s)\]]*)",
    re.IGNORECASE,
)
NO_DOCUMENTATION_NEEDED_PATTERN = re.compile(
    r"\b(?:no docs?|no documentation|documentation\s*(?:is\s*)?(?:n/?a|not required)|docs?\s*(?:are\s*)?(?:n/?a|not required))\b",
    re.IGNORECASE,
)


def _meaningful_description(value: Any) -> bool:
    """Treat empty template headings and placeholders as missing descriptions."""
    text = str(value or "").strip()
    if not text:
        return False
    body = " ".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", body.casefold()).strip()
    return normalized not in {"", "description", "summary", "tbd", "todo", "n a", "none"}


def _review_check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _pipeline_status(raw: dict[str, Any] | str | None) -> str:
    status = (
        str(raw.get("status") or "none")
        if isinstance(raw, dict)
        else str(raw or "none")
    ).casefold()
    return {"success": "passed"}.get(status, status)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return "".join(part[0].upper() for part in parts[:2]) or "?"


def _sd_sort_key(sd_id: str) -> int:
    """Numeric part of an SD id so SD-18808 sorts after SD-244."""
    match = re.search(r"\d+", sd_id or "")
    return int(match.group()) if match else 0


def _match_person(query: str, people: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a typed name/username against people, tolerant of order and commas.

    "Anant Singh" matches "Singh, Anant"; ties are broken by merge-request count so a
    real contributor beats an unmapped placeholder account.
    """
    needle = (query or "").strip().casefold()
    if not needle:
        return None
    for person in people:
        if (person.get("username") or "").casefold() == needle:
            return person
    for person in people:
        if (person.get("name") or "").strip().casefold() == needle:
            return person
    tokens = [token for token in re.split(r"[\s,]+", needle) if token]
    best: dict[str, Any] | None = None
    for person in people:
        name = (person.get("name") or "").casefold()
        if tokens and all(token in name for token in tokens):
            if best is None or person.get("count", 0) > best.get("count", 0):
                best = person
    return best


def _person(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    name = raw.get("name") or raw.get("username") or "Unknown"
    return {
        "id": raw.get("id"),
        "name": name,
        "username": raw.get("username", ""),
        "avatar_url": raw.get("avatar_url"),
        "initials": _initials(name),
    }


MAX_PROMPT_DIFF_CHARS = 60000


def _build_review_prompt(
    project_path: str,
    source: str,
    target: str,
    sd_ids: list[str],
    commits: list[dict[str, Any]],
    files: list[dict[str, Any]],
    added: int,
    removed: int,
) -> tuple[str, bool]:
    """Assemble a self-contained code-review prompt to paste into Claude."""
    header = [
        "You are an experienced senior engineer performing a thorough code review of a GitLab branch.",
        "",
        f"Project: {project_path}",
        f"Branch under review: {source}",
        f"Target branch: {target}",
        f"Work item(s): {', '.join(sd_ids) if sd_ids else 'none detected'}",
        f"Change size: {len(files)} file(s), +{added} / -{removed}",
        "If a checkout of this repository is available in your current working directory, "
        "open the changed files and their surrounding code for fuller context before judging.",
        "",
        f"Commits ({len(commits)}):",
    ]
    for commit in commits[:40]:
        header.append(
            f"  - {commit.get('short_id') or '???'} {commit.get('title') or ''}".rstrip()
        )
    if len(commits) > 40:
        header.append(f"  ... and {len(commits) - 40} more commits")
    header += [
        "",
        "Review the unified diff below and report findings grouped by severity "
        "(Blocker / Major / Minor / Nit). For each finding give: file and line, what is "
        "wrong, why it matters, and a concrete suggested fix. Cover at least:",
        "  1. Correctness & logic - edge cases, off-by-one, null/empty, error handling, concurrency.",
        "  2. Security - input validation, injection, secrets/credentials, authz, unsafe deserialization.",
        "  3. Tests - are the changes covered? Call out missing or weak tests.",
        "  4. Readability & maintainability - naming, duplication, dead code, complexity.",
        "  5. Performance - needless allocations, N+1 calls, blocking I/O on hot paths.",
        "",
        "Then add a section titled exactly '## Verdict' followed by ONE plain-text paragraph - no "
        "bullet points, no sub-headings, no code blocks - written so it can be pasted directly as "
        "a GitLab merge-request comment. State the recommendation (Approve / Approve with nits / "
        "Request changes) and summarise the key reasons in prose.",
        "",
        "Finally, output a line containing exactly '=== POSTABLE COMMENTS (JSON) ===' and then a "
        "JSON array (and nothing else after it) of the 3-10 most important, actionable review "
        "comments to post on GitLab. Each element must be an object with keys: 'file' (string or "
        "null), 'line' (integer or null), 'severity' (one of 'Blocker','Major','Minor','Nit'), and "
        "'comment' (plain prose, ready to post, no markdown headings). Output only valid JSON there.",
        "",
        "=== UNIFIED DIFF ===",
    ]
    body: list[str] = []
    used = 0
    omitted: list[str] = []
    for file in files:
        block = (
            f"\n--- {file['change'].upper()}: {file['path']} "
            f"(+{file['added']}/-{file['removed']}) ---\n{file['diff']}"
        )
        if used + len(block) > MAX_PROMPT_DIFF_CHARS:
            omitted.append(file["path"])
            continue
        body.append(block)
        used += len(block)
    prompt = "\n".join(header) + "".join(body)
    if omitted:
        listed = ", ".join(omitted[:50]) + ("..." if len(omitted) > 50 else "")
        prompt += (
            "\n\n[Diff truncated to keep the prompt manageable. "
            f"Review these files directly in GitLab: {listed}]"
        )
    return prompt, bool(omitted)


_COMMENTS_MARKER = "=== POSTABLE COMMENTS (JSON) ==="


def _split_review_and_comments(raw: str) -> tuple[str, list[dict[str, Any]]]:
    """Separate the human review text from the trailing machine-readable comment JSON."""
    text = (raw or "").strip()
    if _COMMENTS_MARKER not in text:
        return text, []
    human, _, tail = text.partition(_COMMENTS_MARKER)
    comments: list[dict[str, Any]] = []
    start = tail.find("[")
    end = tail.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(tail[start : end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                comment = str(item.get("comment") or "").strip()
                if not comment:
                    continue
                file = str(item.get("file") or "").strip()
                line = item.get("line")
                severity = str(item.get("severity") or "").strip() or "Comment"
                location = f"{file}:{line}" if file and line else file
                body = (
                    f"**{location}** — _{severity}_\n\n{comment}"
                    if location
                    else f"_{severity}_ — {comment}"
                )
                comments.append(
                    {
                        "file": file or None,
                        "line": line if isinstance(line, int) else None,
                        "severity": severity,
                        "comment": comment,
                        "body": body,
                    }
                )
    return human.strip(), comments


def _demo_branches(query: str) -> dict[str, Any]:
    branches = [
        {"name": "main", "default": True, "merged": False, "protected": True,
         "commit": {"short_id": "9f3a1c2", "title": "Release 2025.2.0", "author_name": "Maya S.", "committed_date": None}},
        {"name": "feature/SD-18801-acquisition-duration", "default": False, "merged": False, "protected": False,
         "commit": {"short_id": "a1b2c3d", "title": "feat(SD-18801): compute acquisition duration", "author_name": "You", "committed_date": None}},
        {"name": "bugfix/SD-18808-version-decouple", "default": False, "merged": False, "protected": False,
         "commit": {"short_id": "b2c3d4e", "title": "fix(SD-18808): decouple package version", "author_name": "You", "committed_date": None}},
    ]
    needle = query.strip().casefold()
    if needle:
        branches = [branch for branch in branches if needle in branch["name"].casefold()]
    return {
        "default_branch": "main",
        "project_path": "pia_restricted/demo/acquisition-portal",
        "web_url": "https://gitlab.example/pia_restricted/demo/acquisition-portal",
        "items": branches,
    }


def _demo_branch_review(source: str, target: str) -> dict[str, Any]:
    source = source or "feature/SD-18801-acquisition-duration"
    target = target or "main"
    files = [
        {
            "path": "src/acquisition/DurationResolver.java",
            "old_path": "src/acquisition/DurationResolver.java",
            "change": "modified",
            "added": 12,
            "removed": 3,
            "diff": (
                "@@ -10,7 +10,16 @@ public class DurationResolver {\n"
                "-    // TODO: implement\n"
                "+    public Duration resolve(Series series) {\n"
                "+        return Duration.between(series.getStart(), series.getEnd());\n"
                "+    }\n"
            ),
        },
        {
            "path": "tests/DurationResolverTest.java",
            "old_path": None,
            "change": "added",
            "added": 18,
            "removed": 0,
            "diff": (
                "@@ -0,0 +1,18 @@\n"
                "+class DurationResolverTest {\n"
                "+    @Test void resolvesDuration() { /* ... */ }\n"
                "+}\n"
            ),
        },
    ]
    commits = [
        {"short_id": "a1b2c3d", "title": "feat(SD-18801): compute acquisition duration", "author_name": "You", "created_at": None},
        {"short_id": "e4f5g6h", "title": "test(SD-18801): add resolver tests", "author_name": "You", "created_at": None},
    ]
    added = sum(file["added"] for file in files)
    removed = sum(file["removed"] for file in files)
    sd_ids = ["SD-18801"]
    prompt, truncated = _build_review_prompt(
        "pia_restricted/demo/acquisition-portal", source, target, sd_ids, commits, files, added, removed
    )
    return {
        "project_path": "pia_restricted/demo/acquisition-portal",
        "source_branch": source,
        "target_branch": target,
        "sd_ids": sd_ids,
        "compare_url": f"https://gitlab.example/pia_restricted/demo/acquisition-portal/-/compare/{target}...{source}",
        "stats": {"files": len(files), "additions": added, "deletions": removed, "commits": len(commits)},
        "commits": commits,
        "files": files,
        "review_prompt": prompt,
        "prompt_truncated": truncated,
        "generated_at": datetime.now(UTC).isoformat(),
    }


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, Any] | None = None
        self._cached_at: datetime | None = None
        self._contributors_cache: list[dict[str, Any]] | None = None
        self._contributors_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        return None

    async def validate_token(self, token: str) -> tuple[Settings, dict[str, Any], dict[str, Any]]:
        candidate = replace(self.settings, token=token)
        client = GitLabClient(candidate)
        try:
            user, group = await asyncio.gather(client.current_user(), client.group())
            return candidate, user, group
        finally:
            await client.close()

    def reconfigure(self, settings: Settings) -> None:
        self.settings = settings
        self._cache = None
        self._cached_at = None
        self._contributors_cache = None
        self._contributors_at = None

    def _cache_valid(self) -> bool:
        if not self._cache or not self._cached_at:
            return False
        return datetime.now(UTC) - self._cached_at < timedelta(seconds=self.settings.cache_seconds)

    async def dashboard(self, force: bool = False) -> dict[str, Any]:
        if self.settings.demo_mode:
            return demo_payload(self.settings.group_url)
        if not force and self._cache_valid():
            return self._cache  # type: ignore[return-value]

        async with self._lock:
            if not force and self._cache_valid():
                return self._cache  # type: ignore[return-value]
            payload = await self._load_live()
            self._cache = payload
            self._cached_at = datetime.now(UTC)
            return payload

    async def reviewer_readiness(self, project_id: int, iid: int) -> dict[str, Any]:
        """Load the authoritative detail only when a reviewer opens an MR drawer."""
        if self.settings.demo_mode:
            detail = next(
                (
                    mr
                    for mr in demo_payload(self.settings.group_url)["merge_requests"]
                    if mr["project_id"] == project_id and mr["iid"] == iid
                ),
                None,
            )
            if detail is None:
                raise GitLabError("Merge request was not found.", 404)
            return self._build_reviewer_readiness(
                detail,
                {"approvals_left": detail.get("approvals_left", 0)},
            )

        client = GitLabClient(self.settings)
        try:
            detail, approvals = await asyncio.gather(
                client.merge_request(project_id, iid),
                client.approvals(project_id, iid),
            )
            return self._build_reviewer_readiness(detail, approvals)
        finally:
            await client.close()

    @staticmethod
    def _build_reviewer_readiness(
        detail: dict[str, Any], approvals: dict[str, Any],
    ) -> dict[str, Any]:
        description = str(detail.get("description") or "")
        labels = [str(label) for label in (detail.get("labels") or [])]
        pipeline_status = _pipeline_status(
            detail.get("head_pipeline")
            or detail.get("pipeline")
            or detail.get("pipeline_status")
        )
        traceability_text = "\n".join(
            (
                str(detail.get("title") or ""),
                str(detail.get("source_branch") or ""),
                description,
            )
        )
        ticket_ids = sorted(
            {match.upper() for match in SD_TICKET_PATTERN.findall(traceability_text)}
        )
        title = str(detail.get("title") or "").strip()
        source_branch = str(detail.get("source_branch") or "")
        title_has_sd = bool(SD_TICKET_PATTERN.search(title))
        title_is_branchy = (
            title.casefold() == source_branch.casefold()
            or title.lower().startswith(("wip:", "wip ", "draft:", "draft "))
        )
        title_ok = bool(title) and len(title) >= 12 and not title_is_branchy
        description_present = _meaningful_description(description)
        test_evidence_present = bool(
            description_present
            and TEST_EVIDENCE_PATTERN.search(description)
            and not NO_TEST_EVIDENCE_PATTERN.search(description)
        )
        documentation_context = "\n".join(
            (str(detail.get("title") or ""), description, " ".join(labels))
        )
        documentation_required = bool(DOCUMENTATION_CONTEXT_PATTERN.search(documentation_context)) and not bool(
            NO_DOCUMENTATION_NEEDED_PATTERN.search(documentation_context)
        )
        documentation_link = DOCUMENTATION_LINK_PATTERN.search(description)
        approvals_left = max(0, int(approvals.get("approvals_left") or 0))
        has_conflicts = bool(detail.get("has_conflicts"))
        draft = bool(detail.get("draft") or detail.get("work_in_progress"))
        discussions_resolved = detail.get("blocking_discussions_resolved")
        merge_status = str(
            detail.get("detailed_merge_status") or detail.get("merge_status") or "unknown"
        ).casefold()

        checks = [
            _review_check(
                "title",
                "Title",
                "pass" if title_ok and title_has_sd else "warning" if title_ok else "blocker",
                "Title is descriptive and references an SD id."
                if title_ok and title_has_sd
                else "Title is descriptive; add the SD- id to it for traceability."
                if title_ok
                else "Give the merge request a clear, descriptive title (not the branch name).",
            ),
            _review_check(
                "jira",
                "SD work item",
                "pass" if ticket_ids else "blocker",
                f"Linked: {', '.join(ticket_ids)}" if ticket_ids else "Add an SD- Jira ID in the title, branch, or description.",
            ),
            _review_check(
                "description",
                "Description",
                "pass" if description_present else "blocker",
                "A meaningful merge-request description is present."
                if description_present
                else "Add context, scope, and expected behavior.",
            ),
            _review_check(
                "testing",
                "Testing evidence",
                "pass" if test_evidence_present else "blocker",
                "Testing or validation is documented in the description."
                if test_evidence_present
                else "Add tests run, QA validation, or an explicit rationale.",
            ),
            _review_check(
                "documentation",
                "Documentation / wiki",
                (
                    "pass"
                    if documentation_required and documentation_link
                    else "blocker"
                    if documentation_required
                    else "info"
                ),
                (
                    "Documentation or wiki reference found."
                    if documentation_required and documentation_link
                    else "A docs-related change was detected; link the affected wiki or documentation page."
                    if documentation_required
                    else "No documentation change detected; no wiki link is required."
                ),
            ),
            _review_check(
                "pipeline",
                "Build pipeline",
                "pass" if pipeline_status == "passed" else "blocker" if pipeline_status == "failed" else "warning",
                "Latest pipeline passed."
                if pipeline_status == "passed"
                else "Latest pipeline failed."
                if pipeline_status == "failed"
                else f"Latest pipeline is {pipeline_status or 'not available'}; wait for a passing build.",
            ),
            _review_check(
                "approvals",
                "Required approvals",
                "pass" if approvals_left == 0 else "blocker",
                "All required approvals are complete."
                if approvals_left == 0
                else f"{approvals_left} required approval{'s' if approvals_left != 1 else ''} still pending.",
            ),
            _review_check(
                "draft",
                "Draft status",
                "blocker" if draft else "pass",
                "Mark the merge request ready before approval." if draft else "Merge request is ready for review.",
            ),
            _review_check(
                "conflicts",
                "Merge conflicts",
                "blocker" if has_conflicts else "pass",
                "Resolve merge conflicts before approval." if has_conflicts else "No merge conflicts reported by GitLab.",
            ),
        ]
        if discussions_resolved is True:
            checks.append(_review_check("discussions", "Blocking discussions", "pass", "All blocking discussions are resolved."))
        elif discussions_resolved is False:
            checks.append(_review_check("discussions", "Blocking discussions", "blocker", "Resolve all blocking discussions."))
        else:
            checks.append(_review_check("discussions", "Blocking discussions", "info", "GitLab did not report discussion resolution status."))

        merge_blockers = {
            "cannot_be_merged": "GitLab reports that this merge request cannot be merged.",
            "ci_must_pass": "GitLab requires a passing pipeline before merge.",
            "discussions_not_resolved": "GitLab reports unresolved discussions.",
            "not_approved": "GitLab reports pending approvals.",
        }
        if merge_status in {"mergeable", "can_be_merged"}:
            checks.append(_review_check("mergeability", "GitLab mergeability", "pass", "GitLab reports this merge request is mergeable."))
        elif merge_status in merge_blockers:
            checks.append(_review_check("mergeability", "GitLab mergeability", "blocker", merge_blockers[merge_status]))
        else:
            checks.append(_review_check("mergeability", "GitLab mergeability", "info", f"GitLab merge status: {merge_status.replace('_', ' ')}."))

        blockers = sum(check["status"] == "blocker" for check in checks)
        warnings = sum(check["status"] == "warning" for check in checks)
        comment_templates = {
            "title": "Could you make the title descriptive and include the SD- id (not just the branch name)?",
            "jira": "Could you add the related SD- Jira ID so this change can be traced?",
            "description": "Could you add a short description covering the change scope and expected behavior?",
            "testing": "Could you add the testing evidence, QA validation, or the reason testing is not applicable?",
            "documentation": "Could you link the affected wiki or documentation page?",
            "pipeline": "The latest pipeline has not passed yet. Could you share a passing build before approval?",
            "approvals": "This merge request still has required approvals pending. Please arrange the remaining approval(s).",
            "draft": "Please mark this merge request as ready for review once it is complete.",
            "conflicts": "Could you resolve the merge conflicts before this is approved?",
            "discussions": "Could you resolve the remaining blocking discussions?",
            "mergeability": "GitLab currently reports this merge request is not mergeable. Could you address the reported merge condition?",
        }
        suggested_comments = [
            {
                "check_id": check["id"],
                "label": check["label"],
                "body": comment_templates[check["id"]],
            }
            for check in checks
            if check["status"] in {"blocker", "warning"}
            and check["id"] in comment_templates
        ]
        return {
            "summary": {
                "status": "ready" if not blockers and not warnings else "needs_attention",
                "blocking_count": blockers,
                "warning_count": warnings,
                "passing_count": sum(check["status"] == "pass" for check in checks),
            },
            "checks": checks,
            "suggested_comments": suggested_comments,
            "change_summary": {
                "commits": detail.get("commits_count"),
                "changes": detail.get("changes_count"),
            },
            "manual_prompts": [
                f"Compare {', '.join(ticket_ids)} with its acceptance criteria."
                if ticket_ids
                else "Ask the author to link the SD work item before approving.",
                "Review functional impact, privacy or security implications, and rollout or rollback needs where relevant.",
            ],
        }

    async def _load_live(self) -> dict[str, Any]:
        client = GitLabClient(self.settings)
        warnings: list[str] = []
        try:
            user, group, projects_raw, mrs_raw, issues_raw = await asyncio.gather(
                client.current_user(),
                client.group(),
                client.projects(),
                client.merge_requests(),
                client.assigned_issues(),
            )

            project_map = {int(project["id"]): project for project in projects_raw}
            relevant = [
                mr
                for mr in mrs_raw
                if int(mr.get("project_id", 0)) in project_map
            ]
            enriched = await self._enrich_merge_requests(
                client, relevant, user, project_map, warnings
            )
            projects = self._normalize_projects(projects_raw, enriched)
            tasks = self._normalize_issues(issues_raw, project_map)
            summary = self._summary(enriched, tasks)
            my_merge_requests = await self._load_my_merge_requests(
                client, user, enriched, project_map, warnings
            )

            return {
                "mode": "live",
                "group": {
                    "id": group.get("id"),
                    "name": group.get("name", self.settings.group_path),
                    "path": group.get("full_path", self.settings.group_path),
                    "web_url": group.get("web_url", self.settings.group_url),
                },
                "current_user": _person(user),
                "summary": summary,
                "projects": projects,
                "merge_requests": enriched,
                "my_merge_requests": my_merge_requests,
                "tasks": tasks,
                "refreshed_at": datetime.now(UTC).isoformat(),
                "warnings": warnings,
            }
        finally:
            await client.close()

    async def _load_my_merge_requests(
        self,
        client: GitLabClient,
        user: dict[str, Any],
        enriched: list[dict[str, Any]],
        project_map: dict[int, dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group the current user's authored merge requests into merged / in review / closed."""
        user_id = user.get("id")
        in_review = [
            {**mr, "state": "opened", "merged_at": None, "closed_at": None}
            for mr in enriched
            if (mr.get("author") or {}).get("id") == user_id
        ]
        if not user_id:
            return {"merged": [], "in_review": in_review, "closed": []}

        merged_raw, closed_raw = await asyncio.gather(
            client.merge_requests_by_author(user_id, "merged"),
            client.merge_requests_by_author(user_id, "closed"),
            return_exceptions=True,
        )
        if isinstance(merged_raw, Exception):
            warnings.append("Could not load your merged merge requests.")
            merged_raw = []
        if isinstance(closed_raw, Exception):
            warnings.append("Could not load your closed merge requests.")
            closed_raw = []

        merged = [self._light_merge_request(mr, project_map) for mr in merged_raw][:50]
        closed = [self._light_merge_request(mr, project_map) for mr in closed_raw][:50]
        return {"merged": merged, "in_review": in_review, "closed": closed}

    @staticmethod
    def _light_merge_request(
        mr: dict[str, Any], project_map: dict[int, dict[str, Any]]
    ) -> dict[str, Any]:
        """Normalize a merged/closed merge request without extra per-MR API calls."""
        project_id = int(mr.get("project_id", 0))
        references = mr.get("references") or {}
        project_path = project_map.get(project_id, {}).get("path_with_namespace")
        if not project_path:
            project_path = references.get("full", "").split("!", 1)[0] or str(project_id)
        return {
            "id": mr.get("id"),
            "iid": mr.get("iid"),
            "project_id": project_id,
            "title": mr.get("title", "Untitled merge request"),
            "project_path": project_path,
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", ""),
            "state": mr.get("state"),
            "author": _person(mr.get("author")),
            "labels": mr.get("labels") or [],
            "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
            "pipeline_status": _pipeline_status(
                mr.get("head_pipeline") or mr.get("pipeline") or {}
            ),
            "created_at": mr.get("created_at"),
            "updated_at": mr.get("updated_at"),
            "merged_at": mr.get("merged_at"),
            "closed_at": mr.get("closed_at"),
            "web_url": mr.get("web_url"),
            "user_notes_count": mr.get("user_notes_count", 0),
        }

    async def _enrich_merge_requests(
        self,
        client: GitLabClient,
        merge_requests: list[dict[str, Any]],
        user: dict[str, Any],
        project_map: dict[int, dict[str, Any]],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        user_id = user.get("id")

        async def enrich(mr: dict[str, Any]) -> dict[str, Any]:
            reviewers = mr.get("reviewers") or []
            assignees = mr.get("assignees") or []
            authored_by_me = (mr.get("author") or {}).get("id") == user_id
            reviewer_to_me = any(item.get("id") == user_id for item in reviewers)
            assigned_to_me = any(item.get("id") == user_id for item in assignees)
            approval: dict[str, Any] = {}
            notes: list[dict[str, Any]] = []

            calls: list[Any] = []
            call_names: list[str] = []
            if reviewer_to_me or authored_by_me:
                calls.append(client.approvals(int(mr["project_id"]), int(mr["iid"])))
                call_names.append("approval")
            if authored_by_me:
                calls.append(client.notes(int(mr["project_id"]), int(mr["iid"])))
                call_names.append("notes")

            if calls:
                results = await asyncio.gather(*calls, return_exceptions=True)
                for name, result in zip(call_names, results, strict=True):
                    if isinstance(result, Exception):
                        continue
                    if name == "approval":
                        approval = result
                    elif name == "notes":
                        notes = result

            approved_by = approval.get("approved_by") or []
            approved_by_me = any((item.get("user") or {}).get("id") == user_id for item in approved_by)
            approvals_left = max(0, int(approval.get("approvals_left") or 0))

            latest_human_note = next((note for note in notes if not note.get("system")), None)
            waiting_for_reply = bool(
                authored_by_me
                and reviewers
                and (
                    latest_human_note is None
                    or (latest_human_note.get("author") or {}).get("id") == user_id
                )
            )
            updated = _parse_datetime(mr.get("updated_at"))
            stale = bool(updated and datetime.now(UTC) - updated > timedelta(days=self.settings.stale_days))
            draft = bool(mr.get("draft") or mr.get("work_in_progress"))
            pipeline = mr.get("head_pipeline") or mr.get("pipeline") or {}
            pipeline_status = _pipeline_status(pipeline)

            if reviewer_to_me and not approved_by_me and not draft:
                attention = "needs_approval"
                attention_label = "Needs your approval"
            elif assigned_to_me and not authored_by_me:
                attention = "assigned"
                attention_label = "Assigned to you"
            elif waiting_for_reply:
                attention = "waiting_reply"
                attention_label = "Waiting for reply"
            elif draft:
                attention = "draft"
                attention_label = "Draft"
            elif stale:
                attention = "stale"
                attention_label = "Stale"
            else:
                attention = "healthy"
                attention_label = "On track"

            project_id = int(mr["project_id"])
            references = mr.get("references") or {}
            project_path = project_map.get(project_id, {}).get("path_with_namespace")
            if not project_path:
                project_path = references.get("full", "").split("!", 1)[0] or str(project_id)
            return {
                "id": mr.get("id"),
                "iid": mr.get("iid"),
                "project_id": project_id,
                "title": mr.get("title", "Untitled merge request"),
                "project_path": project_path,
                "source_branch": mr.get("source_branch", ""),
                "target_branch": mr.get("target_branch", ""),
                "author": _person(mr.get("author")),
                "reviewers": [_person(item)["name"] for item in reviewers],
                "assignees": [_person(item)["name"] for item in assignees],
                "labels": mr.get("labels") or [],
                "draft": draft,
                "attention": attention,
                "attention_label": attention_label,
                "pipeline_status": pipeline_status,
                "approved_by_me": approved_by_me,
                "approvals_left": approvals_left,
                "updated_at": mr.get("updated_at"),
                "created_at": mr.get("created_at"),
                "web_url": mr.get("web_url"),
                "user_notes_count": mr.get("user_notes_count", 0),
                "has_conflicts": mr.get("has_conflicts", False),
            }

        normalized = await asyncio.gather(*(enrich(mr) for mr in merge_requests))

        return normalized

    def _normalize_projects(
        self,
        projects: list[dict[str, Any]],
        merge_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        counts: dict[int, dict[str, int]] = {}
        for mr in merge_requests:
            entry = counts.setdefault(mr["project_id"], {"open": 0, "attention": 0})
            entry["open"] += 1
            if mr["attention"] not in {"healthy", "draft"}:
                entry["attention"] += 1

        normalized = []
        for project in projects:
            project_id = int(project["id"])
            path = project.get("path_with_namespace", project.get("name", str(project_id)))
            namespace = path.rsplit("/", 1)[0] if "/" in path else "root"
            normalized.append(
                {
                    "id": project_id,
                    "name": project.get("name", path.rsplit("/", 1)[-1]),
                    "path_with_namespace": path,
                    "namespace": namespace,
                    "web_url": project.get("web_url"),
                    "open_mr_count": counts.get(project_id, {}).get("open", 0),
                    "attention_count": counts.get(project_id, {}).get("attention", 0),
                    "last_activity_at": project.get("last_activity_at"),
                }
            )
        return normalized

    def _normalize_issues(
        self,
        issues: list[dict[str, Any]],
        project_map: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for issue in issues:
            project = project_map.get(int(issue.get("project_id", 0)), {})
            result.append(
                {
                    "id": issue.get("id"),
                    "iid": issue.get("iid"),
                    "title": issue.get("title", "Untitled issue"),
                    "project_path": project.get("path_with_namespace", str(issue.get("project_id", ""))),
                    "labels": issue.get("labels") or [],
                    "due_date": issue.get("due_date"),
                    "updated_at": issue.get("updated_at"),
                    "web_url": issue.get("web_url"),
                }
            )
        return result

    @staticmethod
    def _summary(merge_requests: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "open_merge_requests": len(merge_requests),
            "needs_my_approval": sum(mr["attention"] == "needs_approval" for mr in merge_requests),
            "waiting_for_reply": sum(mr["attention"] == "waiting_reply" for mr in merge_requests),
            "assigned_to_me": sum(mr["attention"] == "assigned" for mr in merge_requests),
            "my_open_issues": len(tasks),
            "stale": sum(mr["attention"] == "stale" for mr in merge_requests),
            "failed_pipelines": sum(mr["pipeline_status"] == "failed" for mr in merge_requests),
        }

    async def approve(self, project_id: int, iid: int) -> dict[str, Any]:
        if self.settings.demo_mode:
            raise GitLabError("Approval is unavailable while demo data is active.", 409)
        if not self.settings.enable_write_actions:
            raise GitLabError(
                "Write actions are disabled. Set GITLAB_ENABLE_WRITE_ACTIONS=true in .env.",
                403,
            )
        client = GitLabClient(self.settings)
        try:
            current = await client.merge_request(project_id, iid)
            diff_refs = current.get("diff_refs") or {}
            sha = current.get("sha") or diff_refs.get("head_sha")
            result = await client.approve(project_id, iid, sha)
            self._cache = None
            self._cached_at = None
            return result
        finally:
            await client.close()

    async def add_note(self, project_id: int, iid: int, body: str) -> dict[str, Any]:
        if self.settings.demo_mode:
            raise GitLabError("Comments are unavailable while demo data is active.", 409)
        if not self.settings.enable_write_actions:
            raise GitLabError(
                "Write actions are disabled. Set GITLAB_ENABLE_WRITE_ACTIONS=true in .env.",
                403,
            )
        client = GitLabClient(self.settings)
        try:
            return await client.create_note(project_id, iid, body)
        finally:
            await client.close()

    async def authors(self) -> list[dict[str, str]]:
        """Names + usernames to power the report author picker."""
        if self.settings.demo_mode:
            seen: dict[str, str] = {}
            payload = demo_payload(self.settings.group_url)
            pool = list(payload["merge_requests"])
            for bucket in payload.get("my_merge_requests", {}).values():
                pool.extend(bucket)
            for mr in pool:
                author = mr.get("author") or {}
                username = author.get("username") or (author.get("name") or "").lower()
                if username:
                    seen[username] = author.get("name") or username
            return [
                {"username": username, "name": name}
                for username, name in sorted(seen.items(), key=lambda item: item[1].lower())
            ]

        client = GitLabClient(self.settings)
        try:
            members, me, contributors = await asyncio.gather(
                client.group_members(),
                client.current_user(),
                self._contributors(client),
            )
        finally:
            await client.close()
        # Contributors (real merge-request authors) come first so their names win,
        # then add members and the current user who may not have authored anything yet.
        unique: dict[str, str] = {}
        for person in [*contributors, *members, me]:
            username = person.get("username")
            if username:
                name = (person.get("name") or username).strip() or username
                unique.setdefault(username, name)
        return [
            {"username": username, "name": name}
            for username, name in sorted(unique.items(), key=lambda item: item[1].lower())
        ]

    async def _contributors(self, client: GitLabClient) -> list[dict[str, Any]]:
        """Distinct real merge-request authors in the group, with their MR counts (cached)."""
        if (
            self._contributors_cache is not None
            and self._contributors_at is not None
            and datetime.now(UTC) - self._contributors_at
            < timedelta(seconds=self.settings.cache_seconds)
        ):
            return self._contributors_cache
        try:
            merge_requests = await client.merge_requests_all_states()
        except GitLabError:
            return self._contributors_cache or []
        authors: dict[str, dict[str, Any]] = {}
        for mr in merge_requests:
            author = mr.get("author") or {}
            username = author.get("username")
            if not username:
                continue
            entry = authors.setdefault(
                username,
                {"username": username, "name": (author.get("name") or username).strip(), "count": 0},
            )
            entry["count"] += 1
        result = list(authors.values())
        self._contributors_cache = result
        self._contributors_at = datetime.now(UTC)
        return result

    async def merge_request_report(
        self, author: str, states: list[str]
    ) -> dict[str, Any]:
        """Group an author's merge requests under the SD ids they reference."""
        wanted = [s for s in states if s in {"opened", "merged", "closed"}] or [
            "opened",
            "merged",
        ]
        if self.settings.demo_mode:
            return self._demo_report(author, wanted)

        client = GitLabClient(self.settings)
        try:
            username, display = await self._resolve_author(client, author)
            results = await asyncio.gather(
                *(client.merge_requests_by_username(username, state) for state in wanted),
                return_exceptions=True,
            )
        finally:
            await client.close()

        raw: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, Exception):
                raw.extend(result)
        return self._build_report(username, display, wanted, raw)

    async def _resolve_author(
        self, client: GitLabClient, author: str
    ) -> tuple[str, str]:
        """Accept either a username or a display name and return (username, name)."""
        needle = author.strip()
        if not needle:
            return needle, needle

        # 1) Real merge-request authors — authoritative, and avoids matching an
        #    unmapped "placeholder" account that has no merge requests.
        try:
            contributors = await self._contributors(client)
        except GitLabError:
            contributors = []
        hit = _match_person(needle, contributors)
        if hit:
            return hit["username"], hit.get("name") or hit["username"]

        # 2) Group members (covers people who haven't authored anything yet).
        try:
            members = await client.group_members()
        except GitLabError:
            members = []
        hit = _match_person(needle, members)
        if hit:
            return hit["username"], hit.get("name") or hit["username"]

        # 3) Global user search (inherited access, users outside this group).
        try:
            found = await client.search_users(needle)
        except GitLabError:
            found = []
        hit = _match_person(needle, found)
        if hit:
            return hit["username"], hit.get("name") or hit["username"]
        if found and found[0].get("username"):
            return found[0]["username"], found[0].get("name") or needle

        return needle, needle

    @staticmethod
    def _report_merge_request(mr: dict[str, Any]) -> dict[str, Any]:
        references = mr.get("references") or {}
        project_path = (
            mr.get("project_path")
            or references.get("full", "").split("!", 1)[0]
            or str(mr.get("project_id", ""))
        )
        text = " ".join(
            (
                str(mr.get("title") or ""),
                str(mr.get("source_branch") or ""),
                str(mr.get("description") or ""),
            )
        )
        sd_ids = sorted(
            {match.upper() for match in SD_TICKET_PATTERN.findall(text)},
            key=_sd_sort_key,
            reverse=True,
        )
        return {
            "id": mr.get("id"),
            "iid": mr.get("iid"),
            "project_id": int(mr.get("project_id", 0)),
            "project_path": project_path,
            "title": mr.get("title", "Untitled merge request"),
            "state": mr.get("state") or "opened",
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", ""),
            "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
            "pipeline_status": _pipeline_status(
                mr.get("head_pipeline") or mr.get("pipeline") or {}
            ),
            "web_url": mr.get("web_url"),
            "created_at": mr.get("created_at"),
            "updated_at": mr.get("updated_at"),
            "merged_at": mr.get("merged_at"),
            "closed_at": mr.get("closed_at"),
            "sd_ids": sd_ids,
        }

    def _build_report(
        self,
        username: str,
        display: str,
        states: list[str],
        raw: list[dict[str, Any]],
    ) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        counts = {"opened": 0, "merged": 0, "closed": 0}
        seen_ids: set[Any] = set()
        author_name = display
        for mr in raw:
            marker = mr.get("id") or (mr.get("project_id"), mr.get("iid"))
            item = self._report_merge_request(mr)
            author = mr.get("author") or {}
            author_name = author.get("name") or author_name
            if marker not in seen_ids:
                seen_ids.add(marker)
                if item["state"] in counts:
                    counts[item["state"]] += 1
            for key in item["sd_ids"] or ["__none__"]:
                groups.setdefault(key, []).append(item)

        ordered = sorted(
            (key for key in groups if key != "__none__"),
            key=_sd_sort_key,
            reverse=True,
        )

        def group_entry(key: str) -> dict[str, Any]:
            items = groups[key]
            return {
                "sd_id": None if key == "__none__" else key,
                "merge_requests": items,
                "count": len(items),
                "opened": sum(mr["state"] == "opened" for mr in items),
                "merged": sum(mr["state"] == "merged" for mr in items),
                "closed": sum(mr["state"] == "closed" for mr in items),
            }

        group_list = [group_entry(key) for key in ordered]
        if "__none__" in groups:
            group_list.append(group_entry("__none__"))

        return {
            "author": {"username": username, "name": author_name},
            "states": states,
            "generated_at": datetime.now(UTC).isoformat(),
            "totals": {
                "merge_requests": len(seen_ids),
                "sd_ids": len(ordered),
                "opened": counts["opened"],
                "merged": counts["merged"],
                "closed": counts["closed"],
                "unlinked": len(groups.get("__none__", [])),
            },
            "groups": group_list,
        }

    def _demo_report(self, author: str, states: list[str]) -> dict[str, Any]:
        payload = demo_payload(self.settings.group_url)
        pool = [dict(mr, state=mr.get("state", "opened")) for mr in payload["merge_requests"]]
        for bucket in payload.get("my_merge_requests", {}).values():
            pool.extend(dict(mr) for mr in bucket)

        needle = author.strip().casefold()
        matched = [
            mr
            for mr in pool
            if mr.get("state") in states
            and needle
            in (
                f"{(mr.get('author') or {}).get('name', '')} "
                f"{(mr.get('author') or {}).get('username', '')}"
            ).casefold()
        ]
        display = author
        if matched:
            display = (matched[0].get("author") or {}).get("name") or author
        return self._build_report(author, display, states, matched)

    async def run_code_review(
        self,
        prompt: str,
        project_id: int | None = None,
        project_path: str = "",
        slug: str = "",
    ) -> dict[str, Any]:
        """Run a prepared review prompt through the local Claude Code CLI.

        When a local checkout of the project exists under the configured root, Claude
        runs inside it so it can read the real repository, not just the diff.
        """
        text = (prompt or "").strip()
        if not text:
            raise GitLabError("There is no review prompt to run.", 400)
        cwd = str(_BASE_DIR)
        checkout: dict[str, Any] | None = None
        if project_id:
            checkout = _resolve_checkout(
                self.settings, int(project_id), project_path, slug
            )
            if checkout["found"]:
                cwd = checkout["path"]
        try:
            raw = await asyncio.to_thread(
                run_claude, text, cwd=cwd, timeout=self.settings.code_review_timeout
            )
        except ClaudeCliError as exc:
            raise GitLabError(str(exc), 503) from exc
        review_text, comments = _split_review_and_comments(raw)
        return {
            "review": review_text,
            "comments": comments,
            "checkout_used": checkout["path"] if checkout and checkout["found"] else None,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def code_review_config(self) -> dict[str, Any]:
        return {"root": self.settings.code_review_root}

    def set_project_checkout(self, project_id: int, folder: str) -> dict[str, Any]:
        cleaned = (folder or "").strip()
        if cleaned and not os.path.isdir(cleaned):
            raise GitLabError("That folder does not exist on this machine.", 400)
        set_override(_REVIEW_STORE, int(project_id), cleaned)
        return {"project_id": project_id, "path": cleaned or None}

    async def project_branches(self, project_id: int, query: str = "") -> dict[str, Any]:
        """Branch list for the code-review picker, with the project's default branch."""
        if self.settings.demo_mode:
            return _demo_branches(query)

        client = GitLabClient(self.settings)
        try:
            detail, branches = await asyncio.gather(
                client.project_detail(project_id),
                client.branches(project_id, query),
            )
        finally:
            await client.close()
        items = [
            {
                "name": branch.get("name"),
                "default": bool(branch.get("default")),
                "merged": bool(branch.get("merged")),
                "protected": bool(branch.get("protected")),
                "commit": {
                    "short_id": (branch.get("commit") or {}).get("short_id"),
                    "title": (branch.get("commit") or {}).get("title"),
                    "author_name": (branch.get("commit") or {}).get("author_name"),
                    "committed_date": (branch.get("commit") or {}).get("committed_date"),
                },
            }
            for branch in branches
        ]
        # Most recently updated branches first, so the useful ones are easy to pick.
        items.sort(key=lambda item: item["commit"].get("committed_date") or "", reverse=True)
        return {
            "default_branch": detail.get("default_branch"),
            "project_path": detail.get("path_with_namespace"),
            "web_url": detail.get("web_url"),
            "items": items,
        }

    async def branch_review(
        self, project_id: int, source: str, target: str = ""
    ) -> dict[str, Any]:
        """Fetch branch details + diff and assemble a Claude-ready review prompt."""
        if self.settings.demo_mode:
            review = _demo_branch_review(source, target)
            review["checkout"] = _resolve_checkout(
                self.settings, 0, "pia_restricted/demo/acquisition-portal", "acquisition-portal"
            )
            review["project_id"] = 0
            review["project_slug"] = "acquisition-portal"
            demo_mr = {
                "iid": 248,
                "title": f"feat(SD-18801): {source}",
                "source_branch": source,
                "description": "## Summary\nAdds acquisition duration.\n\n## Testing\n- Unit tests pass.",
                "labels": ["backend"],
                "draft": False,
                "has_conflicts": False,
                "blocking_discussions_resolved": True,
                "detailed_merge_status": "mergeable",
                "head_pipeline": {"status": "success"},
                "state": "opened",
                "web_url": "https://gitlab.example/pia_restricted/demo/acquisition-portal/-/merge_requests/248",
            }
            review["readiness"] = self._build_reviewer_readiness(demo_mr, {"approvals_left": 1})
            review["merge_request"] = {
                "iid": 248, "title": demo_mr["title"], "state": "opened",
                "web_url": demo_mr["web_url"], "draft": False,
            }
            return review

        client = GitLabClient(self.settings)
        merge_request: dict[str, Any] | None = None
        readiness: dict[str, Any] | None = None
        try:
            detail = await client.project_detail(project_id)
            resolved_target = target.strip() or detail.get("default_branch") or "main"
            if resolved_target == source:
                raise GitLabError(
                    "Choose a target branch that differs from the branch under review.",
                    400,
                )
            comparison, branch_mrs = await asyncio.gather(
                client.compare(project_id, resolved_target, source),
                client.merge_requests_for_branch(project_id, source),
            )
            picked = next(
                (mr for mr in branch_mrs if mr.get("state") == "opened"),
                branch_mrs[0] if branch_mrs else None,
            )
            if picked:
                iid = int(picked["iid"])
                mr_detail, approvals = await asyncio.gather(
                    client.merge_request(project_id, iid),
                    client.approvals(project_id, iid),
                )
                readiness = self._build_reviewer_readiness(mr_detail, approvals)
                merge_request = {
                    "iid": iid,
                    "title": mr_detail.get("title"),
                    "state": mr_detail.get("state"),
                    "web_url": mr_detail.get("web_url"),
                    "draft": bool(mr_detail.get("draft") or mr_detail.get("work_in_progress")),
                }
        finally:
            await client.close()
        review = self._build_branch_review(detail, source, resolved_target, comparison)
        review["merge_request"] = merge_request
        review["readiness"] = readiness
        review["checkout"] = _resolve_checkout(
            self.settings,
            int(detail.get("id") or project_id),
            detail.get("path_with_namespace") or "",
            detail.get("path") or "",
        )
        review["project_id"] = int(detail.get("id") or project_id)
        review["project_slug"] = detail.get("path") or ""
        return review

    def _build_branch_review(
        self,
        detail: dict[str, Any],
        source: str,
        target: str,
        comparison: dict[str, Any],
    ) -> dict[str, Any]:
        commits = comparison.get("commits") or []
        diffs = comparison.get("diffs") or []

        files: list[dict[str, Any]] = []
        total_added = 0
        total_removed = 0
        for entry in diffs:
            diff_text = entry.get("diff") or ""
            added = sum(
                1
                for line in diff_text.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            removed = sum(
                1
                for line in diff_text.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            total_added += added
            total_removed += removed
            path = entry.get("new_path") or entry.get("old_path") or "unknown"
            if entry.get("new_file"):
                change = "added"
            elif entry.get("deleted_file"):
                change = "deleted"
            elif entry.get("renamed_file"):
                change = "renamed"
            else:
                change = "modified"
            files.append(
                {
                    "path": path,
                    "old_path": entry.get("old_path"),
                    "change": change,
                    "added": added,
                    "removed": removed,
                    "diff": diff_text,
                }
            )

        commit_view = [
            {
                "short_id": commit.get("short_id"),
                "title": commit.get("title"),
                "author_name": commit.get("author_name"),
                "created_at": commit.get("created_at"),
            }
            for commit in commits
        ]

        traceability = " ".join(
            [source, *(commit.get("title", "") for commit in commits)]
        )
        sd_ids = sorted(
            {match.upper() for match in SD_TICKET_PATTERN.findall(traceability)},
            key=_sd_sort_key,
            reverse=True,
        )

        origin = detail.get("web_url") or self.settings.group_url
        compare_url = f"{origin}/-/compare/{target}...{source}"
        prompt, truncated = _build_review_prompt(
            detail.get("path_with_namespace") or str(detail.get("id")),
            source,
            target,
            sd_ids,
            commit_view,
            files,
            total_added,
            total_removed,
        )
        return {
            "project_path": detail.get("path_with_namespace"),
            "source_branch": source,
            "target_branch": target,
            "sd_ids": sd_ids,
            "compare_url": compare_url,
            "stats": {
                "files": len(files),
                "additions": total_added,
                "deletions": total_removed,
                "commits": len(commit_view),
            },
            "commits": commit_view,
            "files": [
                {key: value for key, value in file.items() if key != "diff"}
                | {"diff": file["diff"][:20000]}
                for file in files
            ],
            "review_prompt": prompt,
            "prompt_truncated": truncated,
            "generated_at": datetime.now(UTC).isoformat(),
        }
