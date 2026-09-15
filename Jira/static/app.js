const form = document.querySelector("#report-form");
const runButton = document.querySelector("#run-button");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const resultsPanel = document.querySelector("#report-results");
const authType = document.querySelector("#auth-type");
const usernameField = document.querySelector("#username-field");
const tokenInput = document.querySelector("#token");
const forgetTokenButton = document.querySelector("#forget-token");
const savedTokenStatus = document.querySelector("#saved-token-status");
const issueSearch = document.querySelector("#issue-search");
const issueFilter = document.querySelector("#issue-filter");
const issueSort = document.querySelector("#issue-sort");
const worklogStart = document.querySelector("#worklog-start");
const worklogEnd = document.querySelector("#worklog-end");

const sprintInput = document.querySelector("#sprint");
const refreshSprintsButton = document.querySelector("#refresh-sprints");
const sprintPicker = document.querySelector("#sprint-picker");
const sprintOptions = document.querySelector("#sprint-options");
const sprintPickerStatus = document.querySelector("#sprint-picker-status");
let availableSprints = [];
let sprintsLoading = false;

const gitlabUrlInput = document.querySelector("#gitlab-url");
const gitlabTokenInput = document.querySelector("#gitlab-token");
const gitlabSaveToken = document.querySelector("#gitlab-save-token");
const gitlabForgetTokenButton = document.querySelector("#gitlab-forget-token");
const gitlabSavedTokenStatus = document.querySelector("#gitlab-saved-token-status");
const gitlabAllowComments = document.querySelector("#gitlab-allow-comments");
const gitlabConnectButton = document.querySelector("#gitlab-connect-button");
const gitlabError = document.querySelector("#gitlab-error");
const gitlabStatus = document.querySelector("#gitlab-status");
const orphanMrCard = document.querySelector("#orphan-mr-card");
const orphanMrTable = document.querySelector("#orphan-mr-table");
const orphanMrCount = document.querySelector("#orphan-mr-count");

const gitlabToggleButton = document.querySelector("#gitlab-toggle-button");
const gitlabPanel = document.querySelector("#gitlab-panel");
const gitlabToggleDot = document.querySelector("#gitlab-toggle-dot");

let currentIssues = [];
let jqlModified = false;
let savedCredentialAvailable = false;
let gitlabSavedCredentialAvailable = false;
let gitlabSyncByKey = {};
let lastReportMeta = null; // { jira_base_url, api_mode, auth_type, username }

const storedFields = ["base-url", "api-mode", "auth-type", "username", "sprint", "jql"];
const gitlabStoredFields = ["gitlab-url"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function scoreTone(score) {
  if (score >= 90) return "good";
  if (score >= 70) return "warn";
  return "bad";
}

function compareOwnerNames(left, right) {
  const leftUnassigned = left === "Unassigned";
  const rightUnassigned = right === "Unassigned";
  if (leftUnassigned !== rightUnassigned) return leftUnassigned ? 1 : -1;
  return left.localeCompare(right, undefined, { sensitivity: "base", numeric: true });
}

function dateInputValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setWeek(offsetWeeks = 0) {
  const today = new Date();
  const day = today.getDay();
  const daysFromMonday = day === 0 ? 6 : day - 1;
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - daysFromMonday + offsetWeeks * 7);
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  worklogStart.value = dateInputValue(start);
  worklogEnd.value = dateInputValue(end);
}

function showState(name) {
  emptyState.hidden = name !== "empty";
  loadingState.hidden = name !== "loading";
  resultsPanel.hidden = name !== "results";
}

function updateAuthFields() {
  const selected = authType.value;
  const serverMode = document.querySelector("#api-mode").value;
  usernameField.hidden =
    selected === "bearer" || (selected === "auto" && serverMode === "server");
}

function savePreferences() {
  storedFields.forEach((id) => {
    const element = document.querySelector(`#${id}`);
    if (!element) return;
    if (id === "jql" && !jqlModified) {
      localStorage.removeItem(`jira-report-${id}`);
      return;
    }
    localStorage.setItem(`jira-report-${id}`, element.value);
  });
}

function saveGitlabPreferences() {
  gitlabStoredFields.forEach((id) => {
    const element = document.querySelector(`#${id}`);
    if (!element) return;
    localStorage.setItem(`jira-report-${id}`, element.value);
  });
}

async function loadConfiguration() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    const defaults = {
      "base-url": config.base_url,
      "api-mode": config.api_mode,
      sprint: config.sprint,
      jql: config.jql,
    };
    Object.entries(defaults).forEach(([id, value]) => {
      const element = document.querySelector(`#${id}`);
      const stored = localStorage.getItem(`jira-report-${id}`);
      if (element) element.value = stored ?? value ?? "";
      if (id === "jql") jqlModified = stored !== null;
    });
    ["auth-type", "username"].forEach((id) => {
      const element = document.querySelector(`#${id}`);
      const stored = localStorage.getItem(`jira-report-${id}`);
      if (element && stored !== null) element.value = stored;
    });
    const saved = config.saved_credentials || {};
    if (saved.available) {
      savedCredentialAvailable = true;
      if (!document.querySelector("#base-url").value) {
        document.querySelector("#base-url").value = saved.base_url || "";
      }
      if (!document.querySelector("#username").value) {
        document.querySelector("#username").value = saved.username || "";
      }
      if (localStorage.getItem("jira-report-auth-type") === null && saved.auth_type) {
        authType.value = saved.auth_type;
      }
      tokenInput.placeholder = "Saved token will be used — enter only to replace it";
      savedTokenStatus.textContent = "A token is securely saved for this Windows account.";
      savedTokenStatus.hidden = false;
      forgetTokenButton.hidden = false;
    }
    updateAuthFields();
  } catch {
    formError.textContent = "The local report service is not ready. Refresh this page.";
    formError.hidden = false;
  }
}

function markGitlabConnected(groupUrl) {
  gitlabSavedCredentialAvailable = true;
  gitlabTokenInput.placeholder = "Saved token will be used — enter only to replace it";
  gitlabSavedTokenStatus.textContent = groupUrl
    ? `Connected to ${groupUrl} — token securely saved for this Windows account.`
    : "A GitLab token is securely saved for this Windows account.";
  gitlabSavedTokenStatus.hidden = false;
  gitlabForgetTokenButton.hidden = false;
  gitlabToggleDot.classList.add("connected");
  gitlabToggleDot.textContent = "✓";
  gitlabToggleButton.classList.add("connected");
}

function markGitlabDisconnected() {
  gitlabToggleDot.classList.remove("connected");
  gitlabToggleDot.textContent = "";
  gitlabToggleButton.classList.remove("connected");
}

async function loadGitlabConfiguration() {
  try {
    const response = await fetch("/api/gitlab/config");
    if (!response.ok) return;
    const config = await response.json();
    const storedUrl = localStorage.getItem("jira-report-gitlab-url");
    gitlabUrlInput.value = storedUrl ?? config.group_url ?? "";
    gitlabAllowComments.checked = Boolean(config.allow_comments);
    const saved = config.saved_credentials || {};
    if (saved.available) {
      if (!gitlabUrlInput.value) gitlabUrlInput.value = saved.group_url || "";
      markGitlabConnected(saved.group_url);
    }
  } catch {
    // GitLab sync is optional; silently skip if the endpoint is unavailable.
  }
}

async function runGitlabSync() {
  const groupUrl = gitlabUrlInput.value.trim();
  const token = gitlabTokenInput.value.trim();
  if (!groupUrl && !gitlabSavedCredentialAvailable) return;
  gitlabError.hidden = true;
  try {
    const response = await fetch("/api/gitlab-sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        issues: currentIssues.map((issue) => ({
          key: issue.key,
          missing_keys: issue.missing_keys || [],
          status_category: issue.status_category || "",
        })),
        group_url: groupUrl,
        token: token || null,
        save_token: gitlabSaveToken.checked,
        jira_base_url: lastReportMeta ? lastReportMeta.jira_base_url : "",
        jira_api_mode: lastReportMeta ? lastReportMeta.api_mode : "auto",
        jira_auth_type: lastReportMeta ? lastReportMeta.auth_type : "auto",
        jira_username: lastReportMeta ? lastReportMeta.username : "",
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "GitLab sync failed.");
    }
    gitlabTokenInput.value = "";
    if (data.credential_saved) markGitlabConnected(data.group_url);
    gitlabAllowComments.checked = Boolean(data.allow_comments);
    gitlabSyncByKey = data.issue_gitlab || {};
    renderOrphanMergeRequests(data.orphan_merge_requests || []);
    renderIssues();
  } catch (error) {
    gitlabSyncByKey = {};
    gitlabError.textContent = error.message || "GitLab sync could not run.";
    gitlabError.hidden = false;
    renderIssues();
  }
}

function renderOrphanMergeRequests(mergeRequests) {
  if (!mergeRequests.length) {
    orphanMrCard.hidden = true;
    return;
  }
  orphanMrCard.hidden = false;
  orphanMrTable.innerHTML = mergeRequests
    .map((mr) => `<tr>
      <td><a href="${escapeHtml(mr.web_url)}" target="_blank" rel="noopener">${escapeHtml(mr.title)}</a></td>
      <td>${escapeHtml(mr.project_path)}</td>
      <td>${escapeHtml(mr.author)}</td>
      <td>${mr.has_description ? "Has description" : '<span class="problem-tag">Missing description</span>'}</td>
      <td>${escapeHtml((mr.updated_at || "").slice(0, 10))}</td>
    </tr>`)
    .join("");
  orphanMrCount.textContent =
    `${mergeRequests.length} open merge request(s) have no detectable Jira issue key in the branch, title, or description.`;
}

function gitlabFlagBadges(flags) {
  const badges = [];
  if (flags.no_linked_mr) badges.push('<span class="problem-tag">No linked MR</span>');
  if (flags.mr_missing_description) badges.push('<span class="problem-tag">MR missing description</span>');
  if (flags.mr_merged_issue_open) badges.push('<span class="problem-tag">MR merged, issue still open</span>');
  if (flags.jira_missing_mr_link) badges.push('<span class="problem-tag">No MR link in Jira</span>');
  return badges;
}

function issueHasCommentContext(issue) {
  return Boolean(issue.description_present) || issueCommentCount(issue) > 0;
}

function issueCommentCount(issue) {
  const count = Number(issue.comment_count);
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
}

function issueContentMarkup(issue) {
  const commentCount = issueCommentCount(issue);
  const description = issue.description_present
    ? '<span class="content-status">Description present</span>'
    : '<span class="content-status is-empty">No description</span>';
  const comments = issue.comment_available
    ? `<span class="content-status ${commentCount ? "" : "is-empty"}">${commentCount} Jira comment${commentCount === 1 ? "" : "s"}</span>`
    : '<span class="content-status is-empty">Comments unavailable</span>';
  return `<div class="issue-content-status">${description}${comments}</div>`;
}

function gitlabActionMenuMarkup(issue, matches) {
  const canAddComment = issueHasCommentContext(issue);
  const disabled = !lastReportMeta ? "disabled" : "";
  const commentActions = canAddComment
    ? matches.map((mr) => `<button type="button" class="push-mr-button" ${disabled}
        data-issue-key="${escapeHtml(issue.key)}"
        data-project-id="${mr.project_id}" data-iid="${mr.iid}">Add Jira comment for !${mr.iid}</button>`).join("")
    : '<p class="action-menu-note">Add a Jira description or comment first to enable this action.</p>';
  return `<details class="issue-action-menu">
    <summary>Actions</summary>
    <div class="issue-action-menu-panel">
      <button type="button" class="view-gitlab-button"
        data-issue-key="${escapeHtml(issue.key)}">View merge details</button>
      ${commentActions}
    </div>
  </details>`;
}

function gitlabCellMarkup(issue) {
  const sync = gitlabSyncByKey[issue.key];
  if (!sync) return '<span class="muted">Connect GitLab to match MRs</span>';
  const { matched_merge_requests: matches, flags } = sync;
  const badges = gitlabFlagBadges(flags);
  const badgeMarkup = badges.length
    ? `<div class="problem-list">${badges.join("")}</div>`
    : '<span class="all-good">Linked</span>';
  return `${badgeMarkup}${gitlabActionMenuMarkup(issue, matches)}`;
}

function mrDetailMarkup(issue, mr) {
  const disabled = !lastReportMeta ? "disabled" : "";
  const commentAction = issueHasCommentContext(issue)
    ? `<button type="button" class="push-mr-button" ${disabled}
        data-issue-key="${escapeHtml(issue.key)}"
        data-project-id="${mr.project_id}" data-iid="${mr.iid}">Add Jira comment</button>`
    : '<p class="muted">Add a Jira description or comment first to enable adding this merge request as a comment.</p>';
  const stateLabel = { opened: "Open", merged: "Merged", closed: "Closed" }[mr.state] || mr.state;
  const description = mr.description && mr.description.trim()
    ? mr.description
    : "(No description provided in the merge request.)";
  return `<div class="gitlab-mr-detail">
    <h4><a href="${escapeHtml(mr.web_url)}" target="_blank" rel="noopener">!${mr.iid} ${escapeHtml(mr.title)}</a></h4>
    <dl>
      <dt>Project</dt><dd>${escapeHtml(mr.project_path)}</dd>
      <dt>Status</dt><dd>${escapeHtml(stateLabel)}${mr.draft ? " (draft)" : ""}</dd>
      <dt>Branch</dt><dd>${escapeHtml(mr.source_branch)} &rarr; ${escapeHtml(mr.target_branch)}</dd>
      <dt>Author</dt><dd>${escapeHtml(mr.author)}</dd>
      <dt>Reviewers</dt><dd>${mr.reviewers && mr.reviewers.length ? escapeHtml(mr.reviewers.join(", ")) : "—"}</dd>
      <dt>Assignees</dt><dd>${mr.assignees && mr.assignees.length ? escapeHtml(mr.assignees.join(", ")) : "—"}</dd>
      <dt>Updated</dt><dd>${escapeHtml((mr.updated_at || "").slice(0, 10))}</dd>
      <dt>Description</dt><dd>${mr.has_description ? "Present" : '<span class="problem-tag">Missing</span>'}</dd>
    </dl>
    <div class="gitlab-mr-description">${escapeHtml(description)}</div>
    ${commentAction}
  </div>`;
}

function openGitlabDetails(issueKey) {
  const modal = document.querySelector("#gitlab-detail-modal");
  const issue = currentIssues.find((item) => item.key === issueKey);
  const sync = gitlabSyncByKey[issueKey];
  document.querySelector("#gitlab-modal-title").textContent =
    issue ? `${issue.key} — ${issue.summary}` : issueKey;
  const body = document.querySelector("#gitlab-modal-body");
  if (!sync) {
    body.innerHTML = '<p class="gitlab-empty-detail">Run GitLab sync first.</p>';
  } else {
    const badges = gitlabFlagBadges(sync.flags);
    const badgeMarkup = badges.length
      ? `<div class="problem-list">${badges.join("")}</div>`
      : '<span class="all-good">Linked, no issues found</span>';
    const matches = sync.matched_merge_requests || [];
    const linkNote = sync.link_source === "jira_comment"
      ? '<p class="muted">Matched from a GitLab link pasted in a Jira comment (not found in the MR\'s branch/title/description).</p>'
      : "";
    const mrMarkup = matches.length
      ? linkNote + matches.map((mr) => mrDetailMarkup(issue, mr)).join("")
      : `<p class="gitlab-empty-detail">No merge request among the configured team members' MRs
          matched this issue's key in its branch name, title, description, or comments,
          and no GitLab link was found pasted in this issue's Jira comments.</p>`;
    body.innerHTML = `${badgeMarkup}<div style="margin-top:12px">${mrMarkup}</div>`;
  }
  if (typeof modal.showModal === "function") modal.showModal();
}

async function handlePushToJira(button) {
  if (!lastReportMeta) return;
  const issueKey = button.dataset.issueKey;
  const projectId = Number(button.dataset.projectId);
  const iid = Number(button.dataset.iid);
  if (!gitlabAllowComments.checked) {
    gitlabError.textContent = "Enable \"Allow posting MR details as Jira comments\" first.";
    gitlabError.hidden = false;
    return;
  }
  if (!confirm(`Post merge request !${iid}'s details as a comment on ${issueKey}?`)) return;
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Posting…";
  try {
    const response = await fetch("/api/gitlab-sync/comment", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jira-Friday-Action": "post-comment" },
      body: JSON.stringify({
        issue_key: issueKey,
        project_id: projectId,
        iid: iid,
        jira_base_url: lastReportMeta.jira_base_url,
        jira_api_mode: lastReportMeta.api_mode,
        jira_auth_type: lastReportMeta.auth_type,
        jira_username: lastReportMeta.username,
        group_url: gitlabUrlInput.value.trim(),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Could not post the comment to Jira.");
    }
    button.textContent = "Posted ✓";
  } catch (error) {
    button.disabled = false;
    button.textContent = originalLabel;
    gitlabError.textContent = error.message || "Could not post the comment to Jira.";
    gitlabError.hidden = false;
  }
}

function renderMetrics(summary, reportType) {
  const missing = summary.missing_by_key || {};
  let metrics;
  if (reportType === "sprint") {
    metrics = [
      ["Sprint items", summary.total, "accent"],
      ["Done", summary.done, "good"],
      ["In progress", summary.in_progress, ""],
      ["To do", summary.to_do, ""],
      ["Blocked", summary.blocked, summary.blocked ? "warn" : ""],
      ["Overdue", summary.overdue, summary.overdue ? "bad" : ""],
    ];
  } else if (reportType === "hygiene") {
    metrics = [
      ["Hygiene score", `${summary.score}%`, "accent"],
      ["Fully compliant", summary.compliant, "good"],
      ["Missing epic / parent", missing.epic_parent || 0, missing.epic_parent ? "warn" : ""],
      ["Missing description", missing.description || 0, missing.description ? "warn" : ""],
      ["Missing story points", missing.story_points || 0, missing.story_points ? "warn" : ""],
      ["Missing due date", missing.due_date || 0, missing.due_date ? "warn" : ""],
      ["Missing fix version", missing.fix_version || 0, missing.fix_version ? "warn" : ""],
      ["Missing commits", missing.commit_ids || 0, missing.commit_ids ? "bad" : ""],
      ["Missing logged time", missing.logged_time || 0, missing.logged_time ? "warn" : ""],
      ["Missing test evidence", missing.test_evidence || 0, missing.test_evidence ? "bad" : ""],
      ["Missing documentation", missing.documentation || 0, missing.documentation ? "warn" : ""],
    ];
  } else {
    metrics = [
      ["Hygiene score", `${summary.score}%`, "accent"],
      ["Sprint items", summary.total, ""],
      ["Done", summary.done, "good"],
      ["Blocked", summary.blocked, summary.blocked ? "warn" : ""],
      ["No activity", summary.stale, summary.stale ? "warn" : ""],
      ["Overdue", summary.overdue, summary.overdue ? "bad" : ""],
    ];
  }
  document.querySelector("#metric-grid").innerHTML = metrics
    .map(([label, value, tone]) =>
      `<div class="metric ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

function renderOwners(owners, reportType) {
  const body = document.querySelector("#owner-table");
  const header = document.querySelector("#owner-header");
  const sortedOwners = [...owners].sort((left, right) =>
    compareOwnerNames(left.owner, right.owner));
  let columns;
  if (reportType === "sprint") {
    columns = [
      ["Owner", "owner"], ["Items", "total"], ["Done", "done"],
      ["In progress", "in_progress"], ["To do", "to_do"],
      ["Blocked", "blocked"], ["Overdue", "overdue"],
    ];
  } else if (reportType === "hygiene") {
    columns = [
      ["Owner", "owner"], ["Items", "total"], ["Compliant", "compliant"],
      ["Score", "score"], ["Missing story points", "story_points"], ["Missing due", "due_date"],
      ["Missing fix version", "fix_version"], ["Missing commits", "commit_ids"],
      ["Missing logged time", "logged_time"], ["Missing test evidence", "test_evidence"],
    ];
  } else {
    columns = [
      ["Owner", "owner"], ["Items", "total"], ["Done", "done"],
      ["Compliant", "compliant"], ["Score", "score"],
      ["Blocked", "blocked"], ["Overdue", "overdue"],
    ];
  }
  header.innerHTML = columns.map(([label]) => `<th>${escapeHtml(label)}</th>`).join("");
  if (!sortedOwners.length) {
    body.innerHTML = `<tr><td class="no-rows" colspan="${columns.length}">No issues matched this query.</td></tr>`;
    return;
  }
  body.innerHTML = sortedOwners.map((owner) => {
    const cells = columns.map(([, key]) => {
      if (key === "owner") return `<td class="owner-name">${escapeHtml(owner.owner)}</td>`;
      if (key === "score") {
        return `<td><span class="score-pill ${scoreTone(owner.score)}">${owner.score}%</span></td>`;
      }
      const value = ["story_points", "due_date", "fix_version", "commit_ids", "logged_time", "test_evidence"].includes(key)
        ? (owner.missing[key] ?? 0)
        : (owner[key] ?? 0);
      return `<td>${value}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
}

function issueMatchesFilter(issue, filter) {
  if (filter === "all") return true;
  if (filter === "attention") return !issue.compliant || issue.warnings.length || issue.advisory.length;
  if (filter === "blocked") return issue.blocked;
  if (filter === "overdue") return issue.overdue;
  if (filter === "compliant") return issue.compliant;
  return true;
}

function renderIssues() {
  const query = issueSearch.value.trim().toLowerCase();
  const filter = issueFilter.value;
  const filtered = currentIssues.filter((issue) => {
    const haystack = `${issue.key} ${issue.summary} ${issue.owner} ${issue.status}`.toLowerCase();
    return haystack.includes(query) && issueMatchesFilter(issue, filter);
  });
  filtered.sort((left, right) => {
    if (issueSort.value === "owner-desc") {
      return compareOwnerNames(right.owner, left.owner) || left.key.localeCompare(right.key);
    }
    if (issueSort.value === "key-asc") {
      return left.key.localeCompare(right.key, undefined, { numeric: true });
    }
    if (issueSort.value === "score-asc") {
      return left.score - right.score || compareOwnerNames(left.owner, right.owner);
    }
    return compareOwnerNames(left.owner, right.owner) || left.key.localeCompare(right.key);
  });
  const body = document.querySelector("#issue-table");
  if (!filtered.length) {
    body.innerHTML = '<tr><td class="no-rows" colspan="9">No items match this filter.</td></tr>';
  } else {
    body.innerHTML = filtered.map((issue) => {
      const problems = [...issue.missing, ...issue.advisory, ...issue.warnings];
      const problemMarkup = problems.length
        ? `<div class="problem-list">${problems.map((item) =>
            `<span class="problem-tag">${escapeHtml(item)}</span>`).join("")}</div>`
        : '<span class="all-good">All checked items complete</span>';
      const worklogs = issue.worklogs || [];
      const workedBy = issue.worked_by || [];
      const workedByMarkup = workedBy.length
        ? `<div class="worked-by-list">${workedBy.map((item) =>
            `<span class="worked-by-tag">${escapeHtml(item.person)} · ${item.hours}h</span>`).join("")}</div>`
        : "—";
      return `<tr>
        <td class="issue-cell">
          <a class="issue-key" href="${escapeHtml(issue.url)}" target="_blank" rel="noopener">${escapeHtml(issue.key)}</a>
          <span class="issue-summary">${escapeHtml(issue.summary)}</span>
        </td>
        <td class="owner-cell">${escapeHtml(issue.owner)}</td>
        <td><span class="status-pill">${escapeHtml(issue.status)}</span></td>
        <td title="${escapeHtml(worklogs.map((item) =>
          `${item.author}: ${item.time_spent}${item.started ? ` (${item.started})` : ""}`).join("; "))}">
          ${issue.logged_hours ? `${issue.logged_hours}h` : "—"}
        </td>
        <td>${workedByMarkup}</td>
        <td><span class="score-pill ${scoreTone(issue.score)}">${issue.score}%</span></td>
        <td>${problemMarkup}</td>
        <td>${issueContentMarkup(issue)}</td>
        <td>${gitlabCellMarkup(issue)}</td>
      </tr>`;
    }).join("");
  }
  document.querySelector("#issue-count").textContent =
    `Showing ${filtered.length} of ${currentIssues.length} sprint items`;
}

function renderDescriptionSections() {
  const card = document.querySelector("#description-sections-card");
  const rows = currentIssues
    .map((issue) => ({
      issue,
      missing: (issue.description_sections || []).filter((item) => !item.present),
    }))
    .filter((item) => item.missing.length);
  if (!rows.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  document.querySelector("#description-sections-table").innerHTML = rows
    .map(({ issue, missing }) => `<tr>
      <td>
        <a class="issue-key" href="${escapeHtml(issue.url)}" target="_blank" rel="noopener">${escapeHtml(issue.key)}</a>
        <span class="issue-summary">${escapeHtml(issue.summary)}</span>
      </td>
      <td>${escapeHtml(issue.owner)}</td>
      <td><span class="status-pill">${escapeHtml(issue.status)}</span></td>
      <td><div class="problem-list">${missing.map((item) =>
        `<span class="problem-tag">${escapeHtml(item.label)}</span>`).join("")}</div></td>
    </tr>`)
    .join("");
  document.querySelector("#description-sections-count").textContent =
    `${rows.length} of ${currentIssues.length} issues are missing at least one expected description section.`;
}

function renderDownloads(downloads) {
  const labels = {
    html: ["Open full report", "primary"],
    issues_csv: ["Issue CSV", ""],
    owners_csv: ["Owner CSV", ""],
    timesheet_csv: ["Timesheet CSV", ""],
    worklogs_csv: ["Worklogs CSV", ""],
    json: ["JSON", ""],
  };
  document.querySelector("#download-actions").innerHTML = Object.entries(labels)
    .filter(([key]) => downloads[key])
    .map(([key, [label, tone]]) =>
      `<a class="download-link ${tone}" href="${escapeHtml(downloads[key])}"
        ${key === "html" ? 'target="_blank" rel="noopener"' : "download"}>${label}</a>`)
    .join("");
}

function renderTimesheet(timesheet, worklogErrors) {
  document.querySelector("#timesheet-caption").textContent =
    `${timesheet.start_date} to ${timesheet.end_date} · ${timesheet.expected_hours} expected hours per person`;
  const summaryItems = [
    ["Total hours", `${timesheet.total_hours}h`],
    ["Complete", timesheet.complete],
    ["Partial", timesheet.partial],
    ["Not filled", timesheet.not_filled],
  ];
  document.querySelector("#timesheet-summary").innerHTML = summaryItems
    .map(([label, value]) =>
      `<span class="mini-metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></span>`)
    .join("");

  const dateLabels = timesheet.dates.map((dateValue) => {
    const date = new Date(`${dateValue}T00:00:00`);
    return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  });
  document.querySelector("#timesheet-header").innerHTML = [
    "Person", ...dateLabels, "Total", "Expected", "Status",
  ].map((label) => `<th>${escapeHtml(label)}</th>`).join("");

  const body = document.querySelector("#timesheet-table");
  if (!timesheet.rows.length) {
    body.innerHTML = `<tr><td class="no-rows" colspan="${timesheet.dates.length + 4}">No timesheet data found for this period.</td></tr>`;
  } else {
    body.innerHTML = timesheet.rows.map((row) => {
      const tone = row.status === "Complete" ? "good" : row.status === "Partial" ? "warn" : "bad";
      const daily = timesheet.dates.map((day) => `<td>${row.daily_hours[day] || 0}h</td>`).join("");
      return `<tr>
        <td class="owner-name">${escapeHtml(row.person)}</td>
        ${daily}
        <td><strong>${row.total_hours}h</strong></td>
        <td>${row.expected_hours}h</td>
        <td><span class="score-pill ${tone}">${escapeHtml(row.status)}</span></td>
      </tr>`;
    }).join("");
  }

  const entries = timesheet.entries || [];
  const worklogBody = document.querySelector("#worklog-table");
  if (!entries.length) {
    worklogBody.innerHTML = '<tr><td class="no-rows" colspan="5">No worklogs found for this period.</td></tr>';
  } else {
    worklogBody.innerHTML = entries.map((entry) => `<tr>
      <td>${escapeHtml(entry.date)}</td>
      <td class="owner-name">${escapeHtml(entry.person)}</td>
      <td>
        <a class="issue-key" href="${escapeHtml(entry.issue_url)}" target="_blank" rel="noopener">${escapeHtml(entry.issue)}</a>
        <span class="issue-summary">${escapeHtml(entry.summary)}</span>
      </td>
      <td>${entry.hours}h</td>
      <td>${escapeHtml(entry.comment || "—")}</td>
    </tr>`).join("");
  }
  document.querySelector("#worklog-count").textContent =
    `${entries.length} worklog entries in the selected period` +
    (worklogErrors.length ? ` · ${worklogErrors.length} issue(s) could not expose worklogs` : "");
}

function renderReport(data) {
  const titles = {
    combined: ["Everything report", "Team status and hygiene"],
    sprint: ["Sprint report", "Owner sprint progress"],
    hygiene: ["Jira hygiene report", "Owner hygiene compliance"],
  };
  const [reportTitle, ownerTitle] = titles[data.report_type] || titles.combined;
  document.querySelector("#report-title").textContent = reportTitle;
  document.querySelector("#owner-title").textContent = ownerTitle;
  document.querySelector("#report-caption").textContent =
    `${data.report_date} · ${data.summary.total} items · ${data.api_mode} API`;
  renderMetrics(data.summary, data.report_type);
  renderOwners(data.summary.owners, data.report_type);
  currentIssues = data.issues;
  lastReportMeta = {
    jira_base_url: data.jira_base_url,
    api_mode: data.api_mode,
    auth_type: authType.value,
    username: document.querySelector("#username").value.trim(),
  };
  gitlabSyncByKey = {};
  orphanMrCard.hidden = true;
  issueFilter.value = data.report_type === "sprint" ? "all" : "attention";
  renderIssues();
  renderDescriptionSections();
  runGitlabSync();
  renderTimesheet(data.timesheet, data.worklog_errors || []);
  renderDownloads(data.downloads);

  const alert = document.querySelector("#configuration-alert");
  if (data.unresolved.length || data.worklog_errors.length) {
    const messages = [];
    if (data.unresolved.length) {
      const names = data.unresolved.map((item) => item.label).join(", ");
      messages.push(`${data.unresolved.length} field(s) were excluded from scoring: ${names}.`);
    }
    if (data.worklog_errors.length) {
      messages.push(`${data.worklog_errors.length} issue(s) did not allow worklog access.`);
    }
    document.querySelector("#configuration-text").textContent =
      `${messages.join(" ")} Match exact custom-field names in the configuration and confirm Jira worklog permissions.`;
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }
  showState("results");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  runButton.disabled = true;
  runButton.classList.add("loading");
  showState("loading");
  savePreferences();

  const payload = {
    base_url: document.querySelector("#base-url").value.trim(),
    api_mode: document.querySelector("#api-mode").value,
    auth_type: authType.value,
    username: document.querySelector("#username").value.trim(),
    token: tokenInput.value,
    sprint: document.querySelector("#sprint").value.trim(),
    jql: jqlModified ? document.querySelector("#jql").value.trim() : "",
    report_type: document.querySelector('input[name="report_type"]:checked').value,
    save_token: document.querySelector("#save-token").checked,
    worklog_start: worklogStart.value,
    worklog_end: worklogEnd.value,
  };

  try {
    const response = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "The report could not be completed.");
    }
    tokenInput.value = "";
    if (data.credential_saved) {
      savedCredentialAvailable = true;
      tokenInput.placeholder = "Saved token will be used — enter only to replace it";
      savedTokenStatus.textContent = "A token is securely saved for this Windows account.";
      savedTokenStatus.hidden = false;
      forgetTokenButton.hidden = false;
    }
    renderReport(data);
  } catch (error) {
    showState("empty");
    formError.textContent = error.message || "The report could not be completed.";
    formError.hidden = false;
  } finally {
    runButton.disabled = false;
    runButton.classList.remove("loading");
  }
});

document.querySelector("#toggle-token").addEventListener("click", (event) => {
  const showing = tokenInput.type === "text";
  tokenInput.type = showing ? "password" : "text";
  event.currentTarget.textContent = showing ? "Show" : "Hide";
  event.currentTarget.setAttribute("aria-label", showing ? "Show token" : "Hide token");
});

forgetTokenButton.addEventListener("click", async () => {
  forgetTokenButton.disabled = true;
  try {
    const response = await fetch("/api/credentials", { method: "DELETE" });
    if (!response.ok) throw new Error("Could not remove the saved token.");
    savedCredentialAvailable = false;
    savedTokenStatus.hidden = true;
    forgetTokenButton.hidden = true;
    tokenInput.placeholder = "Enter API token or PAT";
    tokenInput.value = "";
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
  } finally {
    forgetTokenButton.disabled = false;
  }
});

authType.addEventListener("change", updateAuthFields);
document.querySelector("#api-mode").addEventListener("change", updateAuthFields);
issueSearch.addEventListener("input", renderIssues);
issueFilter.addEventListener("change", renderIssues);
issueSort.addEventListener("change", renderIssues);
document.querySelector("#this-week").addEventListener("click", () => setWeek(0));
document.querySelector("#last-week").addEventListener("click", () => setWeek(-1));
document.querySelector("#jql").addEventListener("input", () => {
  jqlModified = true;
});

document.querySelector("#toggle-gitlab-token").addEventListener("click", (event) => {
  const showing = gitlabTokenInput.type === "text";
  gitlabTokenInput.type = showing ? "password" : "text";
  event.currentTarget.textContent = showing ? "Show" : "Hide";
  event.currentTarget.setAttribute("aria-label", showing ? "Show token" : "Hide token");
});

gitlabConnectButton.addEventListener("click", async () => {
  gitlabError.hidden = true;
  gitlabStatus.hidden = true;
  const groupUrl = gitlabUrlInput.value.trim();
  const token = gitlabTokenInput.value.trim();
  if (!groupUrl) {
    gitlabError.textContent = "Enter the GitLab group URL first.";
    gitlabError.hidden = false;
    return;
  }
  if (!token) {
    if (gitlabSavedCredentialAvailable) {
      gitlabStatus.textContent = "Already connected with a saved token. Enter a new token to replace it.";
      gitlabStatus.hidden = false;
      return;
    }
    gitlabError.textContent = "Enter a GitLab access token first.";
    gitlabError.hidden = false;
    return;
  }
  gitlabConnectButton.disabled = true;
  saveGitlabPreferences();
  try {
    const response = await fetch("/api/gitlab/credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group_url: groupUrl,
        token: token,
        save_token: gitlabSaveToken.checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Could not connect to GitLab.");
    }
    gitlabTokenInput.value = "";
    gitlabStatus.textContent = `Connected as ${data.user.name || data.user.username}.`;
    gitlabStatus.hidden = false;
    if (data.credential_saved) markGitlabConnected(data.group_url);
    if (currentIssues.length) runGitlabSync();
  } catch (error) {
    gitlabError.textContent = error.message || "Could not connect to GitLab.";
    gitlabError.hidden = false;
  } finally {
    gitlabConnectButton.disabled = false;
  }
});

gitlabForgetTokenButton.addEventListener("click", async () => {
  gitlabForgetTokenButton.disabled = true;
  try {
    const response = await fetch("/api/gitlab/credentials", { method: "DELETE" });
    if (!response.ok) throw new Error("Could not remove the saved GitLab token.");
    gitlabSavedCredentialAvailable = false;
    gitlabSavedTokenStatus.hidden = true;
    gitlabForgetTokenButton.hidden = true;
    gitlabTokenInput.placeholder = "Enter once, then it is remembered securely";
    gitlabTokenInput.value = "";
    markGitlabDisconnected();
  } catch (error) {
    gitlabError.textContent = error.message;
    gitlabError.hidden = false;
  } finally {
    gitlabForgetTokenButton.disabled = false;
  }
});

gitlabAllowComments.addEventListener("change", async () => {
  const enabled = gitlabAllowComments.checked;
  try {
    const response = await fetch("/api/gitlab/write-actions", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Jira-Friday-Action": "gitlab-write-actions" },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) throw new Error("Could not update the setting.");
  } catch (error) {
    gitlabAllowComments.checked = !enabled;
    gitlabError.textContent = error.message || "Could not update the setting.";
    gitlabError.hidden = false;
  }
});

document.addEventListener("click", (event) => {
  const pushButton = event.target.closest(".push-mr-button");
  if (pushButton) {
    handlePushToJira(pushButton);
    return;
  }
  const viewButton = event.target.closest(".view-gitlab-button");
  if (viewButton) {
    openGitlabDetails(viewButton.dataset.issueKey);
    return;
  }
  const closeButton = event.target.closest(".gitlab-modal-close");
  if (closeButton) {
    document.querySelector("#gitlab-detail-modal").close();
  }
});

document.querySelector("#gitlab-detail-modal").addEventListener("click", (event) => {
  if (event.target.id === "gitlab-detail-modal") event.target.close();
});

function renderSprintOptions(filterText) {
  const needle = filterText.trim().toLowerCase();
  const matches = availableSprints.filter((sprint) => {
    if (!needle) return true;
    return (
      String(sprint.id).includes(needle) ||
      sprint.name.toLowerCase().includes(needle)
    );
  });
  sprintOptions.innerHTML = "";
  if (!matches.length) {
    const empty = document.createElement("div");
    empty.className = "sprint-option sprint-option-empty";
    empty.textContent = "No matching sprints.";
    sprintOptions.appendChild(empty);
  } else {
    matches.forEach((sprint) => {
      const option = document.createElement("div");
      option.className = "sprint-option";
      option.setAttribute("role", "option");
      option.tabIndex = 0;
      option.innerHTML =
        `<span class="sprint-option-number">${escapeHtml(sprint.id)}</span>` +
        `<span class="sprint-option-name">${escapeHtml(sprint.name)}</span>` +
        `<span class="sprint-option-state sprint-state-${escapeHtml(sprint.state.toLowerCase())}">${escapeHtml(sprint.state)}</span>`;
      const select = () => {
        sprintInput.value = String(sprint.id);
        sprintPickerStatus.textContent = `Selected sprint ${sprint.id} — ${sprint.name} (${sprint.state}).`;
        sprintPickerStatus.hidden = false;
        sprintOptions.hidden = true;
        savePreferences();
      };
      option.addEventListener("click", select);
      option.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      sprintOptions.appendChild(option);
    });
  }
  sprintOptions.hidden = false;
}

async function loadSprints() {
  if (sprintsLoading) return;
  sprintsLoading = true;
  refreshSprintsButton.textContent = "Loading…";
  formError.hidden = true;
  try {
    const payload = {
      base_url: document.querySelector("#base-url").value.trim(),
      api_mode: document.querySelector("#api-mode").value,
      auth_type: authType.value,
      username: document.querySelector("#username").value.trim(),
      token: tokenInput.value,
    };
    const response = await fetch("/api/sprints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Could not load sprints.");
    }
    availableSprints = data.sprints || [];
    if (!availableSprints.length) {
      sprintPickerStatus.textContent = "No active or upcoming sprints were found.";
      sprintPickerStatus.hidden = false;
      sprintOptions.hidden = true;
      return;
    }
    renderSprintOptions(sprintInput.value);
  } catch (error) {
    formError.textContent = error.message || "Could not load sprints.";
    formError.hidden = false;
  } finally {
    sprintsLoading = false;
    refreshSprintsButton.textContent = "Refresh";
  }
}

refreshSprintsButton.addEventListener("click", () => {
  availableSprints = [];
  loadSprints();
});
sprintInput.addEventListener("focus", () => {
  sprintInput.select();
  if (availableSprints.length) {
    renderSprintOptions(sprintInput.value);
  } else {
    loadSprints();
  }
});
sprintInput.addEventListener("input", () => {
  if (availableSprints.length) {
    renderSprintOptions(sprintInput.value);
  } else {
    loadSprints();
  }
});
sprintInput.addEventListener("blur", () => {
  // Delay so a click on a dropdown option registers before we persist the value.
  setTimeout(savePreferences, 150);
});
document.addEventListener("click", (event) => {
  if (sprintOptions.hidden === false && !sprintPicker.contains(event.target)) {
    sprintOptions.hidden = true;
  }
});

gitlabToggleButton.addEventListener("click", () => {
  const opening = gitlabPanel.hidden;
  gitlabPanel.hidden = !opening;
  gitlabToggleButton.setAttribute("aria-expanded", String(opening));
});
document.addEventListener("click", (event) => {
  if (
    !gitlabPanel.hidden &&
    !gitlabPanel.contains(event.target) &&
    event.target !== gitlabToggleButton &&
    !gitlabToggleButton.contains(event.target)
  ) {
    gitlabPanel.hidden = true;
    gitlabToggleButton.setAttribute("aria-expanded", "false");
  }
});

setWeek(0);
loadConfiguration();
loadGitlabConfiguration();
