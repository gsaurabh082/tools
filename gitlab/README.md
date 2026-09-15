# GitLab Focus

GitLab Focus is a local FastAPI dashboard for merge-request hygiene across the `pia_restricted` group and all of its subgroups. It turns GitLab activity into a practical personal queue: what needs your approval, what is assigned to you, which conversations are waiting for a reply, what has gone stale, and which pipelines are failing.

## Start on Windows

Double-click `start_dashboard.bat`.

The launcher creates `.venv`, installs the pinned dependencies, opens the browser, and starts the dashboard at [http://127.0.0.1:8765](http://127.0.0.1:8765). With no token configured, the dashboard opens in preview mode so the complete UI can still be evaluated.

## Connect GitLab

1. Create a GitLab personal access token with `read_api` scope.
2. Start the dashboard and select **Connect GitLab** in the preview banner.
3. Paste the token and select **Validate & save token**.
4. Press **Refresh** when you are ready to replace preview data with live GitLab data.

The server validates the token against both your user account and the configured group before saving it. The displayed data remains unchanged until you refresh. The token input is cleared after the prompt closes and is never stored in browser storage or returned by an API.

You can also configure it manually by copying `.env.example` to `.env` and adding:

   ```dotenv
   GITLAB_TOKEN=your-token-here
   ```

Then restart `start_dashboard.bat`.

The token is used only by the FastAPI server and is never returned to the browser. `.env` is excluded by `.gitignore`.

To enable approval and suggested-comment buttons, use a token with `api` scope and explicitly set:

```dotenv
GITLAB_ENABLE_WRITE_ACTIONS=true
```

You can also enable or disable this safety switch from **Connection → Enable GitLab write actions**. Every approval and GitLab comment requires an additional confirmation in the UI. Write actions are disabled by default.

## What is included

- All open merge requests across projects and subgroups
- Project-folder directory and search
- Global merge-request/project/author search
- Needs-my-approval, assigned-to-me, waiting-for-reply, draft, and stale signals
- Pipeline status and failed-pipeline queue
- Personal GitLab issues assigned to the signed-in user
- Merge-request detail drawer with an on-demand reviewer-readiness checklist: SD- Jira ID, description, testing evidence, documentation/wiki reference when a docs change is detected, pipeline, approvals, draft/conflict/discussion, and GitLab mergeability signals
- Review-ready actions: approve from the detail drawer and select, edit, then post a checklist-derived comment to GitLab after confirmation
- Server-side caching with a manual refresh
- Responsive desktop, tablet, and mobile layouts
- Safe preview mode and guarded write actions

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `GITLAB_GROUP_URL` | `https://gitlab.apps.ge-healthcare.net/pia_restricted` | Root group to scan |
| `GITLAB_TOKEN` | empty | Personal access token; empty enables preview mode |
| `GITLAB_ENABLE_WRITE_ACTIONS` | `false` | Enables approval API and buttons |
| `GITLAB_CA_BUNDLE` | empty | Optional corporate CA certificate file |
| `GITLAB_VERIFY_SSL` | `true` | TLS verification; keep enabled whenever possible |
| `DASHBOARD_HOST` | `127.0.0.1` | Local bind address |
| `DASHBOARD_PORT` | `8765` | Local HTTP port |
| `DASHBOARD_CACHE_SECONDS` | `90` | Live-data cache lifetime |
| `DASHBOARD_STALE_DAYS` | `5` | MR inactivity threshold |

## Developer commands

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8765
.venv\Scripts\python.exe -m pytest
```

FastAPI API documentation is available at [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) while the app is running.
