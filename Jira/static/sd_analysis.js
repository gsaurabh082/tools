const form = document.querySelector("#sd-analysis-form");
const runButton = document.querySelector("#run-button");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const resultsPanel = document.querySelector("#sd-results");
const tokenInput = document.querySelector("#token");
const authType = document.querySelector("#auth-type");
const apiMode = document.querySelector("#api-mode");
const usernameField = document.querySelector("#username-field");
const searchInput = document.querySelector("#row-search");
const ageFilter = document.querySelector("#age-filter");
let currentRows = [];

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

function renderMetrics(data) {
  const older = data.rows.filter((row) => row["Older than 1 year"] === "Yes").length;
  const metrics = [
    ["Rows requested", data.requested_count, "accent"],
    ["Found in Jira", data.found_count, "good"],
    ["Older than 1 year", older, older ? "warn" : ""],
  ];
  document.querySelector("#metric-grid").innerHTML = metrics.map(([label, value, tone]) =>
    `<div class="metric ${tone}"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`).join("");
}

function renderDownloads(downloads) {
  const labels = {
    sd_analysis_csv: "SD analysis CSV",
    sd_analysis_json: "JSON",
  };
  document.querySelector("#download-actions").innerHTML = Object.entries(labels)
    .map(([key, label]) =>
      `<a class="download-link" href="${escapeHtml(downloads[key])}" download>${label}</a>`)
    .join("");
}

function renderRows() {
  const query = searchInput.value.trim().toLowerCase();
  const ageChoice = ageFilter.value;
  const filtered = currentRows.filter((row) => {
    const text = `${row.SD} ${row.Summary} ${row["Engineering Comment"]}`.toLowerCase();
    const isOld = row["Older than 1 year"] === "Yes";
    return text.includes(query) &&
      (ageChoice === "all" || (ageChoice === "old" ? isOld : !isOld));
  });

  document.querySelector("#sd-table").innerHTML = filtered.length
    ? filtered.map((row) => `<tr>
        <td class="owner-name">${escapeHtml(row.SD)}</td>
        <td>${escapeHtml(row.Summary)}</td>
        <td><span class="status-pill">${escapeHtml(row.Status)}</span></td>
        <td>${escapeHtml(row.Created)}</td>
        <td><span class="score-pill ${row["Older than 1 year"] === "Yes" ? "warn" : "good"}">${escapeHtml(row.Age)}</span></td>
        <td>${escapeHtml(row["Affected Version(s)"])}</td>
        <td>${escapeHtml(row["Fix Version(s)"])}</td>
        <td class="issue-summary" style="white-space: pre-wrap;">${escapeHtml(row["Engineering Comment"])}</td>
      </tr>`).join("")
    : '<tr><td class="no-rows" colspan="8">No rows match this filter.</td></tr>';
  document.querySelector("#row-count").textContent =
    `Showing ${filtered.length} of ${currentRows.length} rows`;
}

function renderReport(data) {
  document.querySelector("#report-caption").textContent =
    `${data.report_date} · ${data.jira_base_url} · ${data.found_count} of ${data.requested_count} tickets found`;
  renderMetrics(data);
  currentRows = data.rows;
  renderRows();
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
    save_token: document.querySelector("#save-token").checked,
    tickets: document.querySelector("#tickets").value,
  };
  localStorage.setItem("jira-report-base-url", payload.base_url);
  localStorage.setItem("jira-report-api-mode", payload.api_mode);
  localStorage.setItem("jira-report-auth-type", payload.auth_type);
  localStorage.setItem("jira-report-username", payload.username);

  try {
    const response = await fetch("/api/sd-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "The SD analysis could not be completed.");
    }
    tokenInput.value = "";
    renderReport(data);
  } catch (error) {
    showState("empty");
    formError.textContent = error.message || "The SD analysis could not be completed.";
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
searchInput.addEventListener("input", renderRows);
ageFilter.addEventListener("change", renderRows);
loadConfiguration();
