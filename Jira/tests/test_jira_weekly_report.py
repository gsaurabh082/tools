import unittest
from datetime import date, datetime, timedelta, timezone

from jira_weekly_report import (
    FieldResolver,
    analyze_assignments,
    analyze_issues,
    assignment_placement,
    build_jql,
    build_timesheet,
    check_value_present,
    flatten_text,
    is_present,
    normalize_jira_base_url,
    resolve_checks,
    summarize,
    sprint_details,
)


class JiraWeeklyReportTests(unittest.TestCase):
    def test_adf_empty_and_nonempty_values(self):
        empty = {"type": "doc", "version": 1, "content": [{"type": "paragraph"}]}
        text = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Ready"}]}
            ],
        }
        self.assertFalse(is_present(empty))
        self.assertTrue(is_present(text))
        self.assertEqual(flatten_text(text), "Ready")

    def test_jql_template_uses_sprint_override(self):
        config = {
            "sprint": "1051",
            "jql_template": "project = SD AND Sprint = {sprint}",
        }
        self.assertEqual(
            build_jql(config, "Sprint 44", None),
            'project = SD AND Sprint = "Sprint 44"',
        )

    def test_explicit_custom_field_id_is_accepted(self):
        resolver = FieldResolver([])
        self.assertEqual(
            resolver.resolve(["customfield_12345"]),
            ["customfield_12345"],
        )

    def test_browser_issue_url_is_normalized_to_jira_root(self):
        self.assertEqual(
            normalize_jira_base_url(
                "https://jira.example.com/jira/browse/SD-123?focusedCommentId=9"
            ),
            "https://jira.example.com/jira",
        )

    def test_logged_time_requires_a_positive_value(self):
        self.assertFalse(check_value_present(0, allow_zero=False))
        self.assertTrue(check_value_present(3600, allow_zero=False))

    def test_issue_content_reports_actual_description_and_comment_count(self):
        raw = [
            {
                "key": "SD-20",
                "fields": {
                    "summary": "Content status",
                    "description": "Implementation notes",
                    "comment": {"total": 2, "comments": []},
                    "issuetype": {"name": "Story"},
                    "status": {
                        "name": "To Do",
                        "statusCategory": {"name": "To Do"},
                    },
                    "assignee": {"displayName": "Ada"},
                    "labels": [],
                    "issuelinks": [],
                },
            }
        ]
        issues = analyze_issues(
            raw,
            [],
            {"flagged": []},
            {"thresholds": {}},
            "https://jira.test",
        )
        self.assertTrue(issues[0]["description_present"])
        self.assertTrue(issues[0]["comment_available"])
        self.assertEqual(issues[0]["comment_count"], 2)

    def test_timesheet_aggregates_person_by_date_and_story(self):
        issues = [
            {
                "key": "SD-10",
                "url": "https://jira.test/browse/SD-10",
                "summary": "Dose report",
                "owner": "Anant",
                "worklog_available": True,
                "worklogs": [
                    {
                        "author": "Anant",
                        "date": "2026-07-27",
                        "seconds": 7200,
                        "time_spent": "2h",
                        "comment": "Implementation",
                    }
                ],
            }
        ]
        timesheet = build_timesheet(
            issues,
            date(2026, 7, 27),
            date(2026, 8, 2),
            expected_hours_per_day=8,
        )
        self.assertEqual(timesheet["total_hours"], 2)
        self.assertEqual(timesheet["rows"][0]["daily_hours"]["2026-07-27"], 2)
        self.assertEqual(timesheet["rows"][0]["status"], "Partial")
        self.assertEqual(timesheet["entries"][0]["issue"], "SD-10")

    def test_assignment_placement_identifies_backlog_and_active_sprint(self):
        self.assertEqual(assignment_placement([], "1051", "To Do"), "Backlog")
        active = sprint_details(
            [{"id": 1051, "name": "Sprint 1051", "state": "active"}]
        )
        self.assertEqual(
            assignment_placement(active, "1051", "In Progress"),
            "Current Sprint",
        )
        future = sprint_details(
            "com.atlassian.greenhopper.service.sprint.Sprint@1[id=1052,state=FUTURE,name=Sprint 1052]"
        )
        self.assertEqual(
            assignment_placement(future, "1051", "To Do"),
            "Future Sprint",
        )

    def test_assignment_analysis_groups_every_assigned_person(self):
        raw = [
            {
                "key": "ALPHA-1",
                "fields": {
                    "summary": "Backlog item",
                    "issuetype": {"name": "Story"},
                    "status": {
                        "name": "To Do",
                        "statusCategory": {"name": "To Do"},
                    },
                    "assignee": {"displayName": "Ada"},
                    "customfield_10020": [],
                },
            },
            {
                "key": "BETA-2",
                "fields": {
                    "summary": "Current-sprint item",
                    "issuetype": {"name": "Bug"},
                    "status": {
                        "name": "In Progress",
                        "statusCategory": {"name": "In Progress"},
                    },
                    "assignee": {"displayName": "Ben"},
                    "customfield_10020": [
                        {"id": "1051", "name": "Sprint 1051", "state": "ACTIVE"}
                    ],
                },
            },
        ]

        analysis = analyze_assignments(
            raw,
            ["customfield_10020"],
            "1051",
            "https://jira.test",
        )

        self.assertEqual(analysis["summary"]["total"], 2)
        self.assertEqual(analysis["summary"]["backlog"], 1)
        self.assertEqual(analysis["summary"]["current_sprint"], 1)
        self.assertEqual(
            [owner["owner"] for owner in analysis["summary"]["owners"]],
            ["Ada", "Ben"],
        )

    def test_field_resolution_and_analysis(self):
        metadata = [
            {"id": "assignee", "name": "Assignee"},
            {"id": "duedate", "name": "Due Date"},
            {"id": "customfield_1", "name": "Test Evidence"},
        ]
        config = {
            "thresholds": {
                "no_activity_days": 7,
                "in_progress_days": 14,
                "due_soon_days": 7,
            },
            "checks": [
                {
                    "key": "assignee",
                    "label": "Assignee",
                    "field_ids": ["assignee"],
                    "required": True,
                },
                {
                    "key": "due_date",
                    "label": "Due Date",
                    "field_ids": ["duedate"],
                    "required": True,
                },
                {
                    "key": "test_evidence",
                    "label": "Test Evidence",
                    "field_names": ["Test Evidence"],
                    "status_categories": ["Done"],
                    "required": True,
                },
            ],
        }
        checks, unresolved = resolve_checks(config, FieldResolver(metadata))
        self.assertFalse(unresolved)
        now = datetime.now(timezone.utc)
        raw = [
            {
                "key": "SD-1",
                "fields": {
                    "summary": "Example",
                    "issuetype": {"name": "Story"},
                    "status": {
                        "name": "Done",
                        "statusCategory": {"name": "Done"},
                    },
                    "assignee": {"displayName": "Anant"},
                    "duedate": None,
                    "customfield_1": "attached",
                    "created": (now - timedelta(days=10)).isoformat(),
                    "updated": now.isoformat(),
                    "statuscategorychangedate": now.isoformat(),
                    "labels": [],
                    "issuelinks": [],
                },
            }
        ]
        issues = analyze_issues(raw, checks, {"flagged": []}, config, "https://jira.test")
        self.assertEqual(issues[0]["missing"], ["Due Date"])
        self.assertEqual(issues[0]["score"], 66.7)
        report_summary = summarize(issues, checks)
        self.assertEqual(report_summary["total"], 1)
        self.assertEqual(report_summary["owners"][0]["owner"], "Anant")


if __name__ == "__main__":
    unittest.main()
