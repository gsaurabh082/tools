# Jenkins Pipeline Chain

A local dashboard that chains Jenkins jobs together: trigger the first job, poll it to
completion, optionally merge a linked GitLab merge request, then trigger the next job -
repeated across as many jobs as you link. On failure it auto-retries a configurable
number of times, then pauses and waits for you to click **Retrigger**.

Built to drive pipelines like:

```
DoseWatch / dosewatch-all / release/2026.2.0
  -> (merge MR)
DoseWatch / dosewatch-deliverables / release/2026.2.0
```

## Start on Windows

Double-click `start_dashboard.bat`. It creates `.venv`, installs dependencies, and opens
the dashboard in your browser on a free local port.

## Configure

Copy `.env.example` to `.env`, or fill in the **Settings** tab in the dashboard once it's
running:

- `JENKINS_USER` / `JENKINS_API_TOKEN` - from Jenkins > your user > Configure > API Token.
- `GITLAB_TOKEN` - a GitLab personal access token with `api` scope (needed to merge MRs;
  leave blank if you never want auto-merge).

Credentials are written to this app's own `.env` (excluded by `.gitignore`) and are only
ever used server-side.

## How it works

1. **Chains** tab - define an ordered list of Jenkins job URLs (multibranch branch job
   URLs work as-is, paste them straight from the browser address bar). Each step can
   optionally carry a GitLab merge request URL: when that step's Jenkins build succeeds,
   the tool merges that MR before moving to the next step.
2. Click **Run** on a chain. If any step has a linked MR, you're asked once whether this
   run is allowed to auto-merge; otherwise each merge step pauses in the **Runs** tab for
   you to click **Merge & continue**.
3. The **Runs** tab polls live: each step shows queued/running/success/failed, the Jenkins
   build number and console link, and the linked MR. Runs auto-refresh every 4 seconds.
4. On a Jenkins failure the job is retried automatically up to `JENKINS_MAX_RETRIES`
   times (with a delay between attempts); after that the run pauses with a **Retrigger**
   button. A failed GitLab merge pauses the same way. **Skip step** moves on without
   retrying, in case you resolved it manually outside the tool.
5. When a step's build succeeds, the tool reads the exact branch and commit Jenkins
   actually built (from the build's git metadata) and shows it next to that step. If the
   step has a linked MR, merging is **pinned to that exact commit** by default: if someone
   pushed new commits to the source branch after the build started, GitLab refuses the
   merge instead of silently merging code Jenkins never tested. After merging, the tool
   also waits for the MR's target branch to actually advance to the merge commit before
   moving on to the next step (so a job that builds off that branch doesn't start against
   stale code). Both behaviors are per-step checkboxes if you need to turn them off.
6. In the chain editor, use **Search** next to the Jenkins job field to look up jobs by
   name (Jenkins' own search index) instead of copy-pasting URLs, and **Search** next to
   the MR field to list a GitLab project's open merge requests once you've entered its
   project path.

Chain definitions persist in `data/chains.json`. Every run (in progress, paused, or
finished - last 200) is checkpointed to `data/runs.json` after each state change, not
just at the end. If the dashboard process is restarted or killed while a run is active,
it reloads that run on the next startup and reconnects to whatever Jenkins queue item or
build was already in flight instead of triggering a duplicate - a run only actually stops
progressing if you `Cancel` it.

## Developer commands

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8790
```

FastAPI API documentation is available at `http://127.0.0.1:8790/docs` while running.
