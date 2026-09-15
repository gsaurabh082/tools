const form = document.querySelector("#assignment-form");
const runButton = document.querySelector("#run-button");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const resultsPanel = document.querySelector("#assignment-results");
const tokenInput = document.querySelector("#token");
const authType = document.querySelector("#auth-type");
const apiMode = document.querySelector("#api-mode");
const usernameField = document.querySelector("#username-field");
const searchInput = document.querySelector("#issue-search");
const placementFilter = document.querySelector("#placement-filter");
const ownerFilter = document.querySelector("#owner-filter");
const ownerSort = document.querySelector("#owner-sort");
let currentIssues = [];
let assignmentJqlModified = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showState(name) {
  emptyState.hidden = name !== "empty";
  loadingState.hidden = name !== "loading";
  resultsPanel.hidden = name !== "results";
}

function updateAuthFields() {
  usernameField.hidden =
    authType.value === "bearer" ||
    (authType.value === "auto" && apiMode.value === "server");
}

function placementTone(value) {
  if (value === "Current Sprint") return "good";
  if (value === "Backlog") return "warn";
  if (value === "Future Sprint") return "neutral";
  return "bad";
}

async function loadConfiguration() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    const saved = config.saved_credentials || {};
    document.querySelector("#base-url").value =
      localStorage.getItem("jira-report-base-url") || config.base_url || saved.base_url || "";
    apiMode.value = localStorage.getItem("jira-report-api-mode") || config.api_mode || "auto";
    authType.value = localStorage.getItem("jira-report-auth-type") || saved.auth_type || "auto";
    document.querySelector("#username").value =
      localStorage.getItem("jira-report-username") || saved.username || "";
    document.querySelector("#current-sprint").value =
      localStorage.getItem("jira-assignment-sprint") || config.sprint || "";
    document.querySelector("#assignment-jql").value = config.assignment_jql || "";
    assignmentJqlModified = false;
    if (saved.available) {
      tokenInput.placeholder = "Saved token will be used — enter only to replace it";
      const status = document.querySelector("#saved-token-status");
      status.textContent = "A token is securely saved for this Windows account.";
      status.hidden = false;
    }
    updateAuthFields();
  } catch {
    formError.textContent = "The local report service is not ready. Refresh this page.";
    formError.hidden = false;
  }
}

function renderMetrics(summary) {
  const metrics = [
    ["Total open", summary.total, "accent"],
    ["Current sprint", summary.current_sprint, "good"],
    ["Backlog", summary.backlog, summary.backlog ? "warn" : ""],
    ["Future sprint", summary.future_sprint, ""],
    ["Other sprint", summary.other_sprint, summary.other_sprint ? "warn" : ""],
    ["Overdue", summary.overdue, summary.overdue ? "bad" : ""],
  ];
  document.querySelector("#metric-grid").innerHTML = metrics.map(([label, value, tone]) =>
    `<div class="metric ${tone}"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`).join("");
}

function renderOwners(owners) {
  const sorted = [...owners].sort((a, b) => {
    if (a.owner === "Unassigned") return 1;
    if (b.owner === "Unassigned") return -1;
    return a.owner.localeCompare(b.owner, undefined, { sensitivity: "base" });
  });
  document.querySelector("#owner-table").innerHTML = sorted.length
    ? sorted.map((owner) => `<tr>
        <td class="owner-name">${escapeHtml(owner.owner)}</td>
        <td>${owner.total}</td><td>${owner.current_sprint}</td><td>${owner.future_sprint}</td>
        <td>${owner.backlog}</td><td>${owner.other_sprint}</td>
        <td>${owner.overdue}</td><td>${owner.no_due_date}</td>
      </tr>`).join("")
    : '<tr><td class="no-rows" colspan="8">No owners or open assignments found.</td></tr>';

  ownerFilter.innerHTML = '<option value="all">All owners</option>' +
    sorted.map((owner) =>
      `<option value="${escapeHtml(owner.owner)}">${escapeHtml(owner.owner)}</option>`).join("");
}

function renderIssues() {
  const query = searchInput.value.trim().toLowerCase();
  const location = placementFilter.value;
  const owner = ownerFilter.value;
  const ownerDirection = ownerSort.value === "owner-desc" ? -1 : 1;
  const filtered = currentIssues.filter((issue) => {
    const text = `${issue.key} ${issue.summary} ${issue.owner} ${issue.status}`.toLowerCase();
    return text.includes(query) &&
      (location === "all" || issue.placement === location) &&
      (owner === "all" || issue.owner === owner);
  }).sort((a, b) => {
    const ownerComparison = a.owner.localeCompare(
      b.owner,
      undefined,
      { sensitivity: "base" },
    );
    return ownerComparison * ownerDirection ||
      a.placement.localeCompare(b.placement) ||
      a.key.localeCompare(b.key, undefined, { numeric: true });
  });

  document.querySelector("#issue-table").innerHTML = filtered.length
    ? filtered.map((issue) => `<tr>
        <td><a class="issue-key" href="${escapeHtml(issue.url)}" target="_blank" rel="noopener">${escapeHtml(issue.key)}</a>
          <span class="issue-summary">${escapeHtml(issue.summary)}</span></td>
        <td class="owner-name">${escapeHtml(issue.owner)}</td>
        <td>${escapeHtml(issue.issue_type)}</td>
        <td><span class="status-pill">${escapeHtml(issue.status)}</span></td>
        <td><span class="score-pill ${placementTone(issue.placement)}">${escapeHtml(issue.placement)}</span></td>
        <td>${escapeHtml(issue.sprints.join(", ") || "—")}</td>
        <td>${escapeHtml(issue.due_date || "—")}</td>
      </tr>`).join("")
    : '<tr><td class="no-rows" colspan="7">No assigned items match this filter.</td></tr>';
  document.querySelector("#issue-count").textContent =
    `Showing ${filtered.length} of ${currentIssues.length} assigned items`;
}

function renderDownloads(downloads) {
  const labels = {
    assignment_issues_csv: "Assigned items CSV",
    assignment_owners_csv: "Owner allocation CSV",
    assignment_json: "JSON",
  };
  document.querySelector("#download-actions").innerHTML = Object.entries(labels)
    .map(([key, label]) =>
      `<a class="download-link" href="${escapeHtml(downloads[key])}" download>${label}</a>`)
    .join("");
}

function renderReport(data) {
  document.querySelector("#report-caption").textContent =
    `${data.report_date} · Current sprint ${data.current_sprint || "not specified"} · ${data.summary.total} open items`;
  renderMetrics(data.summary);
  renderOwners(data.summary.owners);
  currentIssues = data.issues;
  renderIssues();
  renderDownloads(data.downloads);
  showState("results");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  runButton.disabled = true;
  runButton.classList.add("loading");
  showState("loading");

  const payload = {
    base_url: document.querySelector("#base-url").value.trim(),
    api_mode: apiMode.value,
    auth_type: authType.value,
    username: document.querySelector("#username").value.trim(),
    token: tokenInput.value,
    current_sprint: document.querySelector("#current-sprint").value.trim(),
    jql: assignmentJqlModified
      ? document.querySelector("#assignment-jql").value.trim()
      : "",
    save_token: document.querySelector("#save-token").checked,
  };
  localStorage.setItem("jira-report-base-url", payload.base_url);
  localStorage.setItem("jira-report-api-mode", payload.api_mode);
  localStorage.setItem("jira-report-auth-type", payload.auth_type);
  localStorage.setItem("jira-report-username", payload.username);
  localStorage.setItem("jira-assignment-sprint", payload.current_sprint);

  try {
    const response = await fetch("/api/assignment-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "The assignment report could not be completed.");
    }
    tokenInput.value = "";
    renderReport(data);
  } catch (error) {
    showState("empty");
    formError.textContent = error.message || "The assignment report could not be completed.";
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
});
authType.addEventListener("change", updateAuthFields);
apiMode.addEventListener("change", updateAuthFields);
searchInput.addEventListener("input", renderIssues);
placementFilter.addEventListener("change", renderIssues);
ownerFilter.addEventListener("change", renderIssues);
ownerSort.addEventListener("change", renderIssues);
document.querySelector("#assignment-jql").addEventListener("input", () => {
  assignmentJqlModified = true;
});
loadConfiguration();
