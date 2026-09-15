# Jira Friday Status Report

This project now includes a local FastAPI dashboard and a reusable Python
reporting engine. It runs the configured sprint JQL and creates:

- An HTML dashboard for reading or printing
- An issue-level CSV for filtering in Excel
- An owner-level CSV matching the weekly summary format
- A JSON file for future automation or dashboards

The weekly report and assignment scans are read-only. The optional GitLab
sync feature (see below) can post a Jira comment, but only when you click
"Push to Jira" on a specific merge request and only after you explicitly
enable comment posting — it is off by default.

## Start the FastAPI dashboard

The easiest one-click option is to double-click:

`start_jira_ui.bat`

The launcher keeps its window open if startup fails so the error remains
visible.

The dashboard has three weekly views:

- **Everything** — sprint progress, blockers, aging, due dates, and hygiene
- **Sprint** — delivery status and owner progress
- **Hygiene** — mandatory fields and compliance scoring

Use the separate **Assignment & Backlog** tab to scan the configured project,
sprint, and assignees. It classifies each assigned item as Current Sprint,
Future Sprint, Backlog, or Other Sprint and provides owner-wise and issue-level
CSV reports. The assignment query is configured through `assignment_jql`.

Choose **This week**, **Last week**, or any custom From/To date range for
worklogs. The dashboard includes:

- Timesheet filled-up status by person
- Date-wise hours with an 8-hour weekday expectation by default
- Complete, Partial, Not filled, or Unavailable status
- Every worklog grouped by date, person, and Jira story
- A separate “Worked this period by” column on each issue
- Timesheet and worklog CSV downloads

Change `expected_hours_per_day` in `jira_weekly_config.json` if the team uses a
different daily expectation.

When **Remember securely** is selected, the token is encrypted for the current
Windows account and retained until it is replaced. Leave the token field empty
on later runs, enter a new token to replace it, or select **Forget token** to
remove it.

From PowerShell:

```powershell
.\start_jira_ui.ps1
```

The launcher selects a free local port and prints the dashboard address (for
example, `http://127.0.0.1:51234`). Enter:

1. The Jira URL you normally use in a browser
2. Data Center/Server or Cloud, or leave automatic detection selected
3. Your username/email when using Cloud basic authentication
4. Your API token or personal access token
5. The sprint number

The token is used for the current scan only. It is not stored in the
configuration or browser storage. Keep the PowerShell window open while using
the dashboard and press `Ctrl+C` to stop it.

If required, the start script installs the packages listed in
`requirements.txt`.

## Optional configuration

1. Open `jira_weekly_config.json`.
2. Replace `base_url` with the address you use to open Jira.
3. Keep `api_mode` as `auto` unless automatic detection fails.
4. Review the custom field names under `checks`. The first report will list any
   field names it could not match.

The dashboard Jira URL overrides the configured URL for that run. No password
or token is stored in the configuration.

### Jira Data Center / Server with a personal access token

Run the report and enter the personal access token at the hidden prompt, or set
it for the current PowerShell window:

```powershell
$env:JIRA_PAT = Read-Host "Jira personal access token" -MaskInput
```

### Jira Cloud with an email and API token

Run the report and enter the email/token at the prompts, or set them for the
current PowerShell window:

```powershell
$env:JIRA_USER = "your.name@company.com"
$env:JIRA_API_TOKEN = Read-Host "Atlassian API token"
```

These variables disappear when the PowerShell window is closed.

## Run without the UI

For the default Sprint 1051:

```powershell
.\run_friday_report.ps1
```

For the next sprint:

```powershell
.\run_friday_report.ps1 -Sprint 1052
```

The HTML report opens automatically. Files are saved in the `reports` folder.
Use `-NoOpen` when running non-interactively.

You can also supply a one-off query without editing the configuration:

```powershell
.\run_friday_report.ps1 -Jql 'project = SD AND Sprint = 1051'
```

## Checks included

The report evaluates configured Jira fields for assignee, epic/parent, due date,
fix and affected versions, sprint, labels, description, acceptance criteria,
story points, commit/development data, test evidence, documentation, and
workstream/platform.

It also identifies:

- No activity for more than 7 days
- In Progress for more than 14 days, using Jira's status-category change date
- Blocked items with no linked dependency
- Open bugs, unassigned work, overdue items, and items due within 7 days
- Missing logged time on In Progress and Done issues

Test evidence and documentation are required for Done items by default. Commit
information is checked for In Progress and Done items. Workstream/platform is an
advisory check until `"required": true` is set.

If a custom field cannot be found, it is excluded from scoring and highlighted
under **Configuration attention**. Add the exact Jira field name or its
`customfield_12345` ID to the corresponding configuration entry.

## Bulk ticket creation

Use the separate **Bulk create** tab to generate many Jira stories in one pass
for a person, a heading, and an epic link. Unlike the reports above, this tab
**writes new issues to Jira** and always asks for confirmation before it runs.

1. Fill in the Jira connection, the target **Project key**, the **Issue
   type** (defaults to Story), and the story point cap to split above
   (defaults to 5).
2. Provide the stories either by:
   - **Paste rows** — one story per line, columns separated by a tab or a
     pipe (`|`): `Person | Heading | Epic Link | Story Points`. Story points
     is optional and defaults to 3. Pasting straight from Excel (tab-
     separated), including a header row, also works.
   - **Upload Excel** — reads the `Jira Stories` sheet if present (the same
     layout produced when generating stories from a planning sheet),
     otherwise the first sheet. Column headers are matched by name
     (Person/Owner, Heading/Title/Story Title, Epic Link/Epic, Story
     Points), so column order doesn't matter.
3. Click **Parse rows** to preview exactly what will be created, including
   how any story over the point cap is split into "Part 1 of N" / "Part 2 of
   N" issues with the points divided evenly.
4. Click **Create Jira issues**. A confirmation dialog shows the issue count
   and project before anything is written. Each row's outcome (created issue
   key and link, or an error) is shown once the run finishes.

A **Default epic link** field applies to any row that doesn't set its own
epic. Assignee matching uses the Jira username directly on Data Center/Server,
or a display-name search on Jira Cloud; if no confident match is found the
issue is created unassigned rather than guessing.

## GitLab sync (optional)

The weekly report dashboard can optionally connect to a GitLab group to match
merge requests with the Jira issues in the current report, using the standard
`SD-1234`-style key found in a merge request's branch name, title, or
description. This is off until you connect a GitLab group, and it never
writes to GitLab.

In the **GitLab sync** panel under the report form:

1. Enter your GitLab group URL, for example
   `https://gitlab.apps.ge-healthcare.net/pia_restricted`.
2. Enter a personal access token with `read_api` scope and select
   **Connect GitLab**. The token is encrypted for the current Windows account,
   the same way the Jira token is.
3. Run a report as usual. Each issue row gets a **Content** column showing
   whether Jira has a description and how many Jira comments it has. The
   **GitLab & actions** column shows matched merge request(s), hygiene flags,
   and an **Actions** dropdown:
   - **No linked MR** — no merge request could be matched to this issue.
   - **MR missing description** — a matched merge request has an empty or
     very short description.
   - **MR merged, issue still open** — a matched merge request was merged but
     the Jira issue has not moved to Done.
   - **No MR link in Jira** — no merge request was found *and* the
     configured Commit IDs / Development field is also empty.
4. A separate **GitLab hygiene** panel lists open merge requests where no
   Jira key could be detected at all, so they can be fixed at the source.

### Posting merge request details as a Jira comment

By default the sync above is read-only — nothing is written anywhere. To
allow pushing a merge request's title, status, branch, and description into
a Jira comment:

1. Check **Allow posting MR details as Jira comments** in the GitLab sync
   panel. This is stored in `jira_weekly_config.json` (`gitlab.allow_comments`)
   and stays off until you explicitly turn it on.
2. When the issue has a Jira description or an existing Jira comment, open
   **Actions** in the **GitLab & actions** column and choose **Add Jira
   comment** for the merge request (or use the same option in the issue's
   GitLab details). You will be asked to confirm before anything is written.
3. The Jira issue receives one comment with the merge request's title,
   project, status, branch, author, link, and description.

Turn the checkbox off again at any time to disable comment posting without
losing the GitLab connection.

### Scoped to your team, and how matching works

Merge requests are only matched/listed when the author, an assignee, or a
reviewer is one of the SSO IDs in `team_members` in `jira_weekly_config.json`
(the same list already used for the sprint JQL). Set `gitlab.filter_to_team_members`
to `false` to see every merge request in the group instead.

An issue is linked to a merge request through several fallbacks, checked in
order:

1. The Jira issue key (e.g. `SD-1234`) appears in the MR's branch name,
   title, or description.
2. If not, and the MR has no key at all, its GitLab comments are checked too.
3. If a Jira issue still shows no linked MR, that issue's Jira comments are
   scanned for a pasted GitLab merge request URL, which is then matched back
   to the fetched MR list.

Click **View details** on any issue to see exactly which merge request(s)
matched and how.

### Description content checks

Acceptance Criteria, Test Evidence, and Wiki/Documentation Link aren't
separate custom fields in every Jira setup — some teams write them as
sections inside the Description field instead. When no dedicated field is
found, the report searches the Description text for a matching header (for
example "Acceptance Criteria:") and counts it as present if there's real
content after it. Issues missing one of these sections show up in a
dedicated **Description content checks** panel, separate from the main
hygiene table.
