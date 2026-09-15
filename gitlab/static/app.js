const state = {
  data: null,
  config: null,
  view: "overview",
  filters: { attention: "all", project: "", pipeline: "", q: "" },
  projectQuery: "",
  pendingRefresh: false,
  drawerKey: "",
  report: { authors: [], authorsLoaded: false, data: null },
  review: { defaultBranch: "", data: null },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function relativeTime(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Unknown";
  const seconds = Math.max(0, (Date.now() - date.valueOf()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return `${days}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function dueLabel(value) {
  if (!value) return "No due date";
  const date = new Date(`${value}T23:59:59`);
  const days = Math.ceil((date.valueOf() - Date.now()) / 86400000);
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  return `Due in ${days}d`;
}

function shortProject(path) {
  const parts = String(path || "").split("/");
  return parts.length > 2 ? parts.slice(-2).join(" / ") : path;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) {
    const error = new Error(payload.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showToast(message, error = false) {
  const toast = document.createElement("div");
  toast.className = `toast${error ? " error" : ""}`;
  toast.textContent = message;
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function showNotice(message, success = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.className = `notice${success ? " success" : ""}`;
  window.setTimeout(() => notice.classList.add("hidden"), 7000);
}

function setView(name) {
  if (!$("#view-" + name)) return;
  state.view = name;
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  $("#sidebar").classList.remove("open");
  if (name === "merge-requests") renderMergeRequests();
  if (name === "my-mrs") renderMyMergeRequests();
  if (name === "reports") ensureAuthors();
  if (name === "code-review") initCodeReview();
  if (name === "projects") renderProjects();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goToMergeRequests(attention = "all", pipeline = "") {
  state.filters.attention = attention;
  state.filters.pipeline = pipeline;
  $("#pipeline-filter").value = pipeline;
  syncFilterTabs();
  setView("merge-requests");
}

function syncFilterTabs() {
  $$("#mr-filter-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.attention === state.filters.attention);
  });
}

function renderAll() {
  renderShell();
  renderMetrics();
  renderPriority();
  renderPulse();
  renderTaskPreview();
  renderProjectPreview();
  renderMergeRequests();
  renderMyMergeRequests();
  initCodeReview();
  renderActionCenter();
  renderProjects();
  renderSettings();
}

function renderShell() {
  const { data, config } = state;
  const user = data.current_user || {};
  const firstName = (user.name || user.username || "there").split(/\s+/)[0];
  $("#first-name").textContent = firstName;
  $("#day-period").textContent = new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening";
  $("#group-path").textContent = (data.group?.path || config.group_path).replaceAll("/", "  /  ");
  $("#open-group").href = data.group?.web_url || config.group_url;
  $("#user-avatar").textContent = user.initials || "YO";
  $("#nav-mr-count").textContent = data.summary.open_merge_requests;
  $("#nav-task-dot").classList.toggle("hidden", !(data.summary.needs_my_approval || data.summary.my_open_issues));
  $("#refresh-time").textContent = `Updated ${relativeTime(data.refreshed_at)}`;
  $("#connection-dot").className = `status-dot ${data.mode}`;
  $("#connection-text").textContent = data.mode === "demo" ? "Preview data" : "GitLab connected";
  $("#demo-banner").classList.toggle("hidden", data.mode !== "demo");

  const warning = (data.warnings || []).find((item) => !item.toLowerCase().includes("demo data"));
  if (warning) showNotice(warning);
  populateProjectFilter();
}

function renderMetrics() {
  $$('[data-metric]').forEach((element) => {
    element.textContent = state.data.summary[element.dataset.metric] ?? 0;
    element.classList.remove("skeleton-text");
  });
  const s = state.data.summary;
  $("#filter-all-count").textContent = s.open_merge_requests;
  $("#filter-approval-count").textContent = s.needs_my_approval;
  $("#filter-waiting-count").textContent = s.waiting_for_reply;
  $("#filter-assigned-count").textContent = s.assigned_to_me;
  $("#filter-stale-count").textContent = s.stale;
}

function priorityRank(mr) {
  const rank = { needs_approval: 0, assigned: 1, waiting_reply: 2, stale: 3, draft: 4, healthy: 5 };
  return rank[mr.attention] ?? 9;
}

function renderPriority() {
  const items = [...state.data.merge_requests]
    .filter((mr) => !["healthy", "draft"].includes(mr.attention) || mr.pipeline_status === "failed")
    .sort((a, b) => priorityRank(a) - priorityRank(b))
    .slice(0, 5);
  const root = $("#priority-list");
  if (!items.length) {
    root.innerHTML = emptyState("Your queue is clear", "Nothing currently needs your attention.");
    return;
  }
  root.innerHTML = items.map((mr, index) => `
    <div class="priority-item" data-mr-key="${mr.project_id}:${mr.iid}">
      <span class="avatar ${index % 3 === 1 ? "coral" : index % 3 === 2 ? "violet" : ""}">${escapeHtml(mr.author.initials)}</span>
      <div class="item-copy">
        <span class="item-title">${escapeHtml(mr.title)}</span>
        <span class="item-meta"><b>!${mr.iid}</b><span>·</span><span class="item-project">${escapeHtml(shortProject(mr.project_path))}</span><span>·</span><span>${relativeTime(mr.updated_at)}</span></span>
      </div>
      <span class="status-badge ${mr.pipeline_status === "failed" ? "stale" : mr.attention}">${mr.pipeline_status === "failed" ? "Pipeline failed" : escapeHtml(mr.attention_label)}</span>
    </div>`).join("");
  bindMrOpeners(root);
}

function renderPulse() {
  const mrs = state.data.merge_requests;
  const stale = mrs.filter((mr) => mr.attention === "stale").length;
  const failed = mrs.filter((mr) => mr.pipeline_status === "failed").length;
  const unhealthyIds = new Set(mrs.filter((mr) => mr.attention === "stale" || mr.pipeline_status === "failed").map((mr) => mr.id));
  const healthy = Math.max(0, mrs.length - unhealthyIds.size);
  const total = Math.max(1, mrs.length);
  const healthyEnd = Math.round((healthy / total) * 100);
  const staleEnd = Math.min(100, healthyEnd + Math.round((stale / total) * 100));
  $("#health-donut").style.background = `conic-gradient(var(--mint) 0 ${healthyEnd}%, var(--amber) ${healthyEnd}% ${staleEnd}%, var(--red) ${staleEnd}% 100%)`;
  $("#healthy-count").textContent = healthy;
  $("#legend-healthy").textContent = healthy;
  $("#legend-stale").textContent = stale;
  $("#legend-failed").textContent = failed;
  $("#health-score").textContent = `${Math.round((healthy / total) * 100)}% healthy`;
  $("#pulse-total").textContent = `${mrs.length} MRs`;
}

function renderTaskPreview() {
  const root = $("#task-preview");
  const tasks = state.data.tasks.slice(0, 3);
  root.innerHTML = tasks.length ? tasks.map((task) => `
    <a class="task-preview-item" href="${escapeHtml(task.web_url)}" target="_blank" rel="noreferrer">
      <span class="check-box"></span><span><strong>${escapeHtml(task.title)}</strong><small>#${task.iid} · ${escapeHtml(shortProject(task.project_path))} · ${dueLabel(task.due_date)}</small></span>
    </a>`).join("") : emptyState("No assigned issues", "Your personal GitLab issue queue is clear.");
}

function renderProjectPreview() {
  const root = $("#project-preview");
  const projects = [...state.data.projects].sort((a, b) => b.open_mr_count - a.open_mr_count).slice(0, 4);
  const max = Math.max(1, ...projects.map((project) => project.open_mr_count));
  root.innerHTML = projects.length ? projects.map((project) => `
    <div class="project-preview-row"><span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(shortProject(project.namespace))}</small></span><span><small>${project.open_mr_count} open</small><span class="mini-bar"><i style="width:${Math.max(8, project.open_mr_count / max * 100)}%"></i></span></span></div>`).join("") : emptyState("No projects found", "The configured group returned no active projects.");
}

function populateProjectFilter() {
  const select = $("#project-filter");
  const current = state.filters.project;
  const options = [...state.data.projects].sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));
  select.innerHTML = `<option value="">All project folders</option>` + options.map((project) => `<option value="${escapeHtml(project.path_with_namespace)}">${escapeHtml(project.path_with_namespace)}</option>`).join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function filteredMergeRequests() {
  const q = state.filters.q.trim().toLowerCase();
  return state.data.merge_requests.filter((mr) => {
    const matchesAttention = state.filters.attention === "all" || mr.attention === state.filters.attention;
    const matchesProject = !state.filters.project || mr.project_path === state.filters.project;
    const matchesPipeline = !state.filters.pipeline || mr.pipeline_status === state.filters.pipeline;
    const haystack = `${mr.title} ${mr.project_path} ${mr.author.name} ${mr.iid}`.toLowerCase();
    return matchesAttention && matchesProject && matchesPipeline && (!q || haystack.includes(q));
  });
}

function renderMergeRequests() {
  if (!state.data) return;
  syncFilterTabs();
  const items = filteredMergeRequests();
  $("#mr-result-count").textContent = `${items.length} merge request${items.length === 1 ? "" : "s"}`;
  const root = $("#mr-table");
  if (!items.length) {
    root.innerHTML = emptyState("No merge requests match", "Try removing a filter or searching another project folder.");
    return;
  }
  root.innerHTML = items.map((mr) => {
    const canApprove = mr.attention === "needs_approval" && !mr.draft && !mr.approved_by_me;
    return `
      <div class="mr-row" data-mr-key="${mr.project_id}:${mr.iid}">
        <div class="mr-main"><span class="avatar">${escapeHtml(mr.author.initials)}</span><div><h3>${escapeHtml(mr.title)}</h3><p><b>!${mr.iid}</b> · ${escapeHtml(shortProject(mr.project_path))} · ${escapeHtml(mr.author.name)}</p></div></div>
        <div class="mr-branch"><code>${escapeHtml(mr.source_branch || "source")}</code><span>→ ${escapeHtml(mr.target_branch || "target")}</span></div>
        <span><span class="status-badge ${mr.attention}">${escapeHtml(mr.attention_label)}</span></span>
        <span class="pipeline ${escapeHtml(mr.pipeline_status)}"><i></i>${escapeHtml(mr.pipeline_status)}</span>
        <div class="mr-action">${canApprove ? `<button class="button approve-button" data-approve="${mr.project_id}:${mr.iid}">Approve</button>` : `<span class="mr-age">${relativeTime(mr.updated_at)}</span>`}</div>
      </div>`;
  }).join("");
  bindMrOpeners(root);
  $$('[data-approve]', root).forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    const [projectId, iid] = button.dataset.approve.split(":").map(Number);
    approveMr(projectId, iid, button);
  }));
}

function renderActionCenter() {
  const buckets = [
    { title: "Review requested", items: state.data.merge_requests.filter((mr) => mr.attention === "needs_approval"), kind: "mr" },
    { title: "Waiting & assigned", items: state.data.merge_requests.filter((mr) => ["waiting_reply", "assigned", "stale"].includes(mr.attention)), kind: "mr" },
    { title: "Assigned issues", items: state.data.tasks, kind: "issue" },
  ];
  $("#action-columns").innerHTML = buckets.map((bucket) => `
    <article class="panel action-column"><div class="column-heading"><h2>${bucket.title}</h2><span>${bucket.items.length}</span></div>
      ${bucket.items.length ? bucket.items.map((item) => bucket.kind === "mr" ? actionMrCard(item) : actionIssueCard(item)).join("") : `<div class="empty-state compact"><strong>All clear</strong><p>Nothing in this queue.</p></div>`}
    </article>`).join("");
  bindMrOpeners($("#action-columns"));
}

function actionMrCard(mr) {
  return `<div class="action-card" data-mr-key="${mr.project_id}:${mr.iid}"><h3>${escapeHtml(mr.title)}</h3><p>!${mr.iid} · ${escapeHtml(shortProject(mr.project_path))}</p><div class="card-foot"><span class="status-badge ${mr.attention}">${escapeHtml(mr.attention_label)}</span><span class="mr-age">${relativeTime(mr.updated_at)}</span></div></div>`;
}

function renderMyMergeRequests() {
  if (!state.data) return;
  const mine = state.data.my_merge_requests || {};
  const merged = mine.merged || [];
  const inReview = mine.in_review || [];
  const closed = mine.closed || [];
  const navCount = $("#nav-my-mr-count");
  if (navCount) navCount.textContent = merged.length + inReview.length + closed.length;

  const columns = [
    { title: "Merged", items: merged, meta: { badgeClass: "merged", badgeText: "Merged", dateLabel: "Merged", dateKey: "merged_at" } },
    { title: "In review", items: inReview, meta: {} },
    { title: "Closed", items: closed, meta: { badgeClass: "closed", badgeText: "Closed", dateLabel: "Closed", dateKey: "closed_at" } },
  ];
  const root = $("#my-mr-columns");
  if (!root) return;
  root.innerHTML = columns.map((column) => `
    <article class="panel action-column">
      <div class="column-heading"><h2>${column.title}</h2><span>${column.items.length}</span></div>
      ${column.items.length
        ? column.items.map((mr) => myMrCard(mr, column.meta)).join("")
        : `<div class="empty-state compact"><strong>Nothing here</strong><p>No ${column.title.toLowerCase()} merge requests by you.</p></div>`}
    </article>`).join("");
}

function myMrCard(mr, meta) {
  const badge = meta.badgeClass
    ? `<span class="status-badge ${meta.badgeClass}">${escapeHtml(meta.badgeText)}</span>`
    : `<span class="status-badge ${mr.attention || "healthy"}">${escapeHtml(mr.attention_label || "Open")}</span>`;
  const date = meta.dateKey ? mr[meta.dateKey] : mr.updated_at;
  const foot = meta.dateLabel ? `${meta.dateLabel} ${relativeTime(date)}` : relativeTime(date);
  return `<a class="action-card my-mr-card" href="${escapeHtml(mr.web_url)}" target="_blank" rel="noreferrer">
    <h3>${escapeHtml(mr.title)}</h3>
    <p>!${mr.iid} · ${escapeHtml(shortProject(mr.project_path))}</p>
    <div class="card-foot">${badge}<span class="mr-age">${escapeHtml(foot)}</span></div>
  </a>`;
}

async function ensureAuthors() {
  if (state.report.authorsLoaded) return;
  state.report.authorsLoaded = true;
  try {
    const payload = await api("/api/authors");
    state.report.authors = payload.items || [];
    const list = $("#report-authors");
    if (list) {
      list.innerHTML = state.report.authors
        .map((author) => `<option value="${escapeHtml(author.name)}">${escapeHtml(author.username)}</option>`)
        .join("");
    }
  } catch (error) {
    state.report.authorsLoaded = false;
    showToast(`Could not load the contributor list: ${error.message}`, true);
  }
}

async function generateReport(event) {
  if (event) event.preventDefault();
  const author = $("#report-author").value.trim();
  if (!author) {
    showToast("Enter a name or username first.", true);
    return;
  }
  const states = ["opened", "merged", "closed"].filter((value) => $(`#report-state-${value}`).checked);
  if (!states.length) {
    showToast("Select at least one merge-request state.", true);
    return;
  }
  const button = $("#report-generate");
  button.disabled = true;
  button.textContent = "Generating…";
  $("#report-summary").classList.add("hidden");
  $("#report-groups").innerHTML = `<div class="empty-state compact"><div class="loading-ring"></div><p>Building report…</p></div>`;
  try {
    const report = await api(
      `/api/reports/merge-requests?author=${encodeURIComponent(author)}&states=${encodeURIComponent(states.join(","))}`
    );
    state.report.data = report;
    renderReport(report);
  } catch (error) {
    state.report.data = null;
    $("#report-copy").disabled = true;
    $("#report-summary").classList.add("hidden");
    $("#report-groups").innerHTML = emptyState("Report could not be generated", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate report";
  }
}

function renderReport(report) {
  const totals = report.totals || {};
  const stateBits = [
    totals.opened ? `${totals.opened} open` : "",
    totals.merged ? `${totals.merged} merged` : "",
    totals.closed ? `${totals.closed} closed` : "",
  ].filter(Boolean).join(" · ");
  const summary = $("#report-summary");
  summary.innerHTML = `
    <div class="summary-chip"><strong>${escapeHtml(report.author.name || report.author.username)}</strong><span>Contributor</span></div>
    <div class="summary-chip"><strong>${totals.merge_requests || 0}</strong><span>Merge requests</span></div>
    <div class="summary-chip"><strong>${totals.sd_ids || 0}</strong><span>SD work items</span></div>
    <div class="summary-chip"><strong>${totals.unlinked || 0}</strong><span>Without SD id</span></div>
    ${stateBits ? `<div class="summary-chip summary-chip-wide"><strong>${escapeHtml(stateBits)}</strong><span>By state</span></div>` : ""}`;
  summary.classList.remove("hidden");

  const groups = report.groups || [];
  $("#report-copy").disabled = !groups.length;
  const root = $("#report-groups");
  if (!groups.length) {
    root.innerHTML = emptyState("No merge requests found", "This contributor has no merge requests in the selected states.");
    return;
  }
  root.innerHTML = groups.map((group) => {
    const heading = group.sd_id || "No SD id";
    const meta = [
      group.opened ? `${group.opened} open` : "",
      group.merged ? `${group.merged} merged` : "",
      group.closed ? `${group.closed} closed` : "",
    ].filter(Boolean).join(" · ");
    return `
      <section class="report-group">
        <div class="report-group-head">
          <h2>${escapeHtml(heading)}</h2>
          <span class="report-group-meta">${group.count} MR${group.count === 1 ? "" : "s"}${meta ? ` · ${escapeHtml(meta)}` : ""}</span>
        </div>
        <div class="report-mr-list">${group.merge_requests.map(reportMrRow).join("")}</div>
      </section>`;
  }).join("");
}

function reportMrRow(mr) {
  const stamp = mr.state === "merged" ? mr.merged_at : mr.state === "closed" ? mr.closed_at : mr.updated_at;
  const stateLabel = mr.state === "opened" ? "open" : mr.state;
  return `<a class="report-mr" href="${escapeHtml(mr.web_url)}" target="_blank" rel="noreferrer">
    <span class="status-badge ${escapeHtml(mr.state)}">${escapeHtml(stateLabel)}</span>
    <span class="report-mr-title">${escapeHtml(mr.title)}</span>
    <span class="report-mr-meta">!${mr.iid} · ${escapeHtml(shortProject(mr.project_path))} · ${escapeHtml(relativeTime(stamp))}</span>
  </a>`;
}

function buildReportText(report) {
  const totals = report.totals || {};
  const lines = [
    `SD report — ${report.author.name || report.author.username}`,
    `${totals.merge_requests || 0} merge requests across ${totals.sd_ids || 0} SD work items ` +
      `(${totals.opened || 0} open, ${totals.merged || 0} merged, ${totals.closed || 0} closed)`,
    "",
  ];
  (report.groups || []).forEach((group) => {
    lines.push(`${group.sd_id || "No SD id"}  (${group.count})`);
    group.merge_requests.forEach((mr) => {
      lines.push(`  - [${mr.state}] !${mr.iid} ${mr.title} — ${mr.project_path}`);
      if (mr.web_url) lines.push(`    ${mr.web_url}`);
    });
    lines.push("");
  });
  return lines.join("\n").trim();
}

async function copyReport() {
  if (!state.report.data) return;
  const text = buildReportText(state.report.data);
  try {
    await navigator.clipboard.writeText(text);
    showToast("Report copied to clipboard.");
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    try { document.execCommand("copy"); showToast("Report copied to clipboard."); }
    catch { showToast("Copy failed — select the report manually.", true); }
    area.remove();
  }
}

async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(successMessage);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    try { document.execCommand("copy"); showToast(successMessage); }
    catch { showToast("Copy failed — select the text manually.", true); }
    area.remove();
  }
}

function initCodeReview() {
  const select = $("#review-project");
  if (!select || !state.data) return;
  if (select.dataset.filled !== "1") {
    const projects = [...state.data.projects].sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));
    select.innerHTML = `<option value="">Select a project…</option>` +
      projects.map((project) => `<option value="${project.id}">${escapeHtml(project.path_with_namespace)}</option>`).join("");
    select.dataset.filled = "1";
  }
  if (!state.review.configLoaded) {
    state.review.configLoaded = true;
    api("/api/code-review/config")
      .then((cfg) => { $("#review-root").value = cfg.root || ""; })
      .catch(() => { state.review.configLoaded = false; });
  }
}

async function saveReviewRoot() {
  const root = $("#review-root").value.trim();
  if (!root) { showToast("Enter the folder where your projects are checked out.", true); return; }
  const button = $("#review-root-save");
  button.disabled = true;
  try {
    await api("/api/code-review/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root }),
    });
    showToast("Local projects folder saved.");
    if (state.review.data) generateBranchReview();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderCheckoutStatus(data) {
  const el = $("#review-checkout-status");
  const checkout = data.checkout || {};
  if (checkout.found) {
    el.className = "review-checkout-status found";
    el.innerHTML = `✓ Local checkout: <code>${escapeHtml(checkout.path)}</code> — Claude will review with full repository context.`;
    return;
  }
  el.className = "review-checkout-status missing";
  el.innerHTML = `⚠ No local checkout found under <code>${escapeHtml(checkout.root || "the configured folder")}</code>. ` +
    `Claude will review from the diff only. Point it at this project's folder:` +
    `<span class="checkout-set"><input type="text" id="review-project-path" placeholder="Full path to this project's folder" autocomplete="off">` +
    `<button class="button button-small" type="button" id="review-path-save">Use this folder</button></span>`;
  const saveBtn = $("#review-path-save", el);
  if (saveBtn) saveBtn.addEventListener("click", saveProjectCheckout);
}

async function saveProjectCheckout() {
  const data = state.review.data;
  const input = $("#review-project-path");
  const folder = input ? input.value.trim() : "";
  if (!data || !folder) { showToast("Enter the folder path first.", true); return; }
  const button = $("#review-path-save");
  if (button) button.disabled = true;
  try {
    await api(`/api/projects/${data.project_id}/code-review-path`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    data.checkout = { root: data.checkout?.root, path: folder, found: true, source: "saved" };
    renderCheckoutStatus(data);
    showToast("Folder saved — it will be used for this project's reviews.");
  } catch (error) {
    if (button) button.disabled = false;
    showToast(error.message, true);
  }
}

async function loadReviewBranches() {
  const projectId = $("#review-project").value;
  const list = $("#review-branches");
  list.innerHTML = "";
  $("#review-source").value = "";
  $("#review-target").value = "";
  state.review.defaultBranch = "";
  if (!projectId) return;
  try {
    const data = await api(`/api/projects/${projectId}/branches`);
    state.review.defaultBranch = data.default_branch || "";
    $("#review-target").placeholder = data.default_branch
      ? `Target branch (default: ${data.default_branch})`
      : "Target branch (default)";
    list.innerHTML = (data.items || [])
      .map((branch) => {
        const when = branch.commit?.committed_date ? relativeTime(branch.commit.committed_date) : "";
        const label = [branch.default ? "default" : "", when].filter(Boolean).join(" · ");
        return `<option value="${escapeHtml(branch.name)}">${escapeHtml(label)}</option>`;
      })
      .join("");
  } catch (error) {
    showToast(`Could not load branches: ${error.message}`, true);
  }
}

async function generateBranchReview(event) {
  if (event) event.preventDefault();
  const projectId = $("#review-project").value;
  const source = $("#review-source").value.trim();
  if (!projectId) { showToast("Select a project first.", true); return; }
  if (!source) { showToast("Enter the branch to review.", true); return; }
  const target = $("#review-target").value.trim();
  const button = $("#review-load");
  button.disabled = true;
  button.textContent = "Loading…";
  $("#review-summary").classList.add("hidden");
  $("#review-actions").classList.add("hidden");
  $("#review-note").classList.add("hidden");
  $("#review-results").classList.add("hidden");
  $("#review-placeholder").classList.remove("hidden");
  $("#review-placeholder").innerHTML = `<div class="empty-state compact"><div class="loading-ring"></div><p>Fetching branch details and diff…</p></div>`;
  try {
    const params = new URLSearchParams({ source });
    if (target) params.set("target", target);
    const data = await api(`/api/projects/${projectId}/branch-review?${params.toString()}`);
    renderBranchReview(data);
  } catch (error) {
    state.review.data = null;
    $("#review-results").classList.add("hidden");
    $("#review-placeholder").classList.remove("hidden");
    $("#review-placeholder").innerHTML = emptyState("Could not load the diff", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Load diff";
  }
}

function renderBranchReview(data) {
  state.review.data = data;
  state.review.reviewText = "";
  state.review.claudeComments = [];
  const stats = data.stats || {};
  const summary = $("#review-summary");
  summary.innerHTML = `
    <div class="summary-chip summary-chip-wide"><strong>${escapeHtml(data.source_branch)}</strong><span>→ ${escapeHtml(data.target_branch)}</span></div>
    ${data.merge_request ? `<div class="summary-chip"><strong>!${data.merge_request.iid}</strong><span>${escapeHtml(data.merge_request.state || "MR")}</span></div>` : ""}
    <div class="summary-chip"><strong>${stats.files || 0}</strong><span>Files changed</span></div>
    <div class="summary-chip"><strong class="diff-add">+${stats.additions || 0}</strong><span>Additions</span></div>
    <div class="summary-chip"><strong class="diff-del">-${stats.deletions || 0}</strong><span>Deletions</span></div>
    <div class="summary-chip"><strong>${stats.commits || 0}</strong><span>Commits</span></div>
    ${(data.sd_ids || []).length ? `<div class="summary-chip"><strong>${escapeHtml(data.sd_ids.join(", "))}</strong><span>Work items</span></div>` : ""}`;
  summary.classList.remove("hidden");
  $("#review-actions").classList.remove("hidden");
  $("#review-open-compare").href = data.compare_url || "#";
  $("#review-open-compare").classList.toggle("hidden", !data.compare_url);
  $("#review-placeholder").classList.add("hidden");
  $("#review-results").classList.remove("hidden");
  renderCheckoutStatus(data);

  // Claude section reset
  $("#review-result").innerHTML = `<div class="empty-state compact"><p>Select “Review with Claude” above to generate an automated review.</p></div>`;
  $("#claude-badge").textContent = "not run";
  $("#collapse-claude").open = false;

  const note = $("#review-note");
  if (data.prompt_truncated) {
    note.textContent = "Heads up: the diff is large, so the copied prompt includes only part of it. The remaining files are listed inside the prompt for manual review.";
    note.classList.remove("hidden");
  } else {
    note.classList.add("hidden");
  }

  renderReadinessSection(data.readiness);
  renderDiffSection(data);
  buildCommentsList();
}

function renderReadinessSection(readiness) {
  const badge = $("#readiness-badge");
  const root = $("#readiness-content");
  if (!readiness) {
    badge.textContent = "no MR";
    badge.className = "collapse-badge muted";
    root.innerHTML = emptyState("No open merge request for this branch",
      "Reviewer readiness checks (title, description, testing, flags) run against a merge request. Create an MR for this branch to see them.");
    return;
  }
  const s = readiness.summary || {};
  const blockers = s.blocking_count || 0;
  badge.textContent = blockers ? `${blockers} blocker${blockers === 1 ? "" : "s"}` : s.warning_count ? `${s.warning_count} to check` : "ready";
  badge.className = `collapse-badge ${blockers ? "bad" : s.warning_count ? "warn" : "good"}`;
  root.innerHTML = `
    <div class="readiness-summary ${s.status === "ready" ? "ready" : "attention"}">
      <span class="readiness-icon">${s.status === "ready" ? "✓" : "!"}</span>
      <span><strong>${s.status === "ready" ? "Ready for review" : "Needs reviewer attention"}</strong>
      <small>${blockers} blocker${blockers === 1 ? "" : "s"}${s.warning_count ? ` · ${s.warning_count} to check` : ""} · ${s.passing_count || 0} passing</small></span>
    </div>
    <div class="review-check-list">
      ${(readiness.checks || []).map((check) => `
        <div class="review-check ${escapeHtml(check.status)}">
          <span class="review-check-icon">${check.status === "pass" ? "✓" : check.status === "blocker" ? "!" : check.status === "warning" ? "▲" : "i"}</span>
          <span><strong>${escapeHtml(check.label)}</strong><small>${escapeHtml(check.detail)}</small></span>
        </div>`).join("")}
    </div>`;
}

function renderDiffSection(data) {
  const body = $("#review-body");
  const files = data.files || [];
  $("#diff-badge").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
  if (!files.length) {
    body.innerHTML = emptyState("No differences", "This branch has no changes against the target branch.");
    return;
  }
  const commitsHtml = (data.commits || []).length ? `
    <section class="review-commits">
      <h3>Commits (${data.commits.length})</h3>
      <ul>${data.commits.map((commit) => `<li><code>${escapeHtml(commit.short_id || "")}</code> <span>${escapeHtml(commit.title || "")}</span><small>${escapeHtml(commit.author_name || "")}</small></li>`).join("")}</ul>
    </section>` : "";
  body.innerHTML = commitsHtml + files.map((file, index) => `
    <section class="review-file">
      <button type="button" class="review-file-head" data-file-toggle="${index}">
        <span class="review-file-change ${escapeHtml(file.change)}">${escapeHtml(file.change)}</span>
        <span class="review-file-path">${escapeHtml(file.path)}</span>
        <span class="review-file-stat"><span class="diff-add">+${file.added}</span> <span class="diff-del">-${file.removed}</span></span>
      </button>
      <pre class="diff-pre hidden" id="diff-${index}">${renderDiff(file.diff)}</pre>
    </section>`).join("");
  $$('[data-file-toggle]', body).forEach((button) => button.addEventListener("click", () => {
    $(`#diff-${button.dataset.fileToggle}`, body).classList.toggle("hidden");
  }));
}

function collectComments() {
  const data = state.review.data || {};
  const items = [];
  const readiness = data.readiness || {};
  (readiness.suggested_comments || []).forEach((comment) => {
    items.push({ source: `Readiness · ${comment.label}`, tag: "readiness", body: comment.body });
  });
  const verdict = extractVerdict(state.review.reviewText || "");
  if (verdict) items.push({ source: "Claude · Verdict", tag: "verdict", body: verdict });
  (state.review.claudeComments || []).forEach((comment) => {
    const loc = comment.file ? `${comment.file}${comment.line ? ":" + comment.line : ""}` : comment.severity;
    items.push({ source: `Claude · ${escapeHtml(comment.severity)}${comment.file ? " · " + escapeHtml(loc) : ""}`, tag: "claude", body: comment.body || comment.comment });
  });
  return items;
}

function buildCommentsList() {
  const data = state.review.data || {};
  const list = $("#comments-list");
  const target = $("#comments-target");
  const postBtn = $("#comments-post");
  const items = collectComments();
  const mr = data.merge_request;
  if (mr) {
    target.innerHTML = `Comments post to <a href="${escapeHtml(mr.web_url)}" target="_blank" rel="noreferrer">!${mr.iid} — ${escapeHtml(mr.title || "")}</a>.`;
    postBtn.disabled = false;
  } else {
    target.textContent = "No open merge request for this branch — create one to post comments.";
    postBtn.disabled = true;
  }
  if (!items.length) {
    list.innerHTML = `<div class="empty-state compact"><p>No comments yet. Run the Claude review to generate more.</p></div>`;
    return;
  }
  list.innerHTML = items.map((item, index) => `
    <div class="comment-item">
      <label class="comment-check"><input type="checkbox" data-comment="${index}"><span class="comment-source ${item.tag}">${item.source}</span></label>
      <textarea class="comment-body" data-comment-body="${index}" rows="3">${escapeHtml(item.body)}</textarea>
    </div>`).join("");
}

async function postSelectedComments() {
  const data = state.review.data;
  const mr = data && data.merge_request;
  if (!mr) { showToast("No open MR for this branch to post to.", true); return; }
  if (state.data.mode === "demo") { showToast("Connect GitLab before posting comments.", true); return; }
  if (!state.config.write_actions_enabled) { showToast("GitLab write actions are disabled in .env.", true); return; }
  const selected = $$('[data-comment]:checked').map((box) => {
    const index = box.dataset.comment;
    return $(`[data-comment-body="${index}"]`).value.trim();
  }).filter((body) => body.length >= 3);
  if (!selected.length) { showToast("Select at least one comment to post.", true); return; }
  if (!window.confirm(`Post ${selected.length} comment${selected.length === 1 ? "" : "s"} to GitLab MR !${mr.iid}?\n\nThis changes GitLab.`)) return;
  const button = $("#comments-post");
  button.disabled = true;
  button.textContent = "Posting…";
  let posted = 0;
  const failures = [];
  for (const body of selected) {
    try {
      await api(`/api/merge-requests/${data.project_id}/${mr.iid}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-GitLab-Focus-Action": "create-note" },
        body: JSON.stringify({ body }),
      });
      posted += 1;
    } catch (error) {
      failures.push(error.message);
    }
  }
  button.disabled = false;
  button.textContent = "Post selected to GitLab";
  if (failures.length) {
    showToast(`Posted ${posted}, ${failures.length} failed: ${failures[0]}`, true);
  } else {
    showToast(`Posted ${posted} comment${posted === 1 ? "" : "s"} to MR !${mr.iid}.`);
  }
}

function renderDiff(diff) {
  return String(diff || "").split("\n").map((line) => {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "diff-add-line"
      : line.startsWith("-") && !line.startsWith("---") ? "diff-del-line"
      : line.startsWith("@@") ? "diff-hunk-line" : "";
    return `<span class="${cls}">${escapeHtml(line) || "&nbsp;"}</span>`;
  }).join("\n");
}

function copyReviewPrompt() {
  const data = state.review.data;
  if (!data || !data.review_prompt) { showToast("Load a branch diff first.", true); return; }
  copyText(data.review_prompt, "Review prompt copied — paste it into Claude.");
}

function copyReviewDiff() {
  const data = state.review.data;
  if (!data || !(data.files || []).length) { showToast("Load a branch diff first.", true); return; }
  const text = data.files
    .map((file) => `--- ${file.change.toUpperCase()}: ${file.path} (+${file.added}/-${file.removed}) ---\n${file.diff}`)
    .join("\n\n");
  copyText(text, "Raw diff copied to clipboard.");
}

async function runClaudeReview() {
  const data = state.review.data;
  if (!data || !data.review_prompt) { showToast("Load a branch diff first.", true); return; }
  const button = $("#review-run-claude");
  const result = $("#review-result");
  button.disabled = true;
  button.textContent = "Reviewing…";
  $("#collapse-claude").open = true;
  $("#claude-badge").textContent = "running…";
  result.innerHTML = `<div class="empty-state compact"><div class="loading-ring"></div><p>Claude Code is reviewing the diff — this can take a minute or two…</p></div>`;
  $("#collapse-claude").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const response = await api("/api/code-review/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: data.review_prompt,
        project_id: data.project_id,
        project_path: data.project_path,
        slug: data.project_slug,
      }),
    });
    state.review.reviewText = response.review || "";
    state.review.claudeComments = response.comments || [];
    result.innerHTML = renderMarkdown(state.review.reviewText);
    $("#claude-badge").textContent = `${state.review.claudeComments.length} comment${state.review.claudeComments.length === 1 ? "" : "s"}`;
    buildCommentsList();
    showToast(response.checkout_used
      ? "Claude reviewed with full repo context."
      : "Claude finished the review (diff only).");
  } catch (error) {
    $("#claude-badge").textContent = "failed";
    result.innerHTML = emptyState("Review could not be completed", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Review with Claude";
  }
}

function copyReviewResult() {
  if (!state.review.reviewText) { showToast("Run a review first.", true); return; }
  copyText(state.review.reviewText, "Full review copied to clipboard.");
}

function extractVerdict(text) {
  const lines = String(text || "").split("\n");
  let start = lines.findIndex((line) => /^#{1,6}\s*verdict\b/i.test(line.trim()));
  if (start === -1) {
    start = lines.findIndex((line) => /^\**\s*verdict\s*\**\s*:?\s*$/i.test(line.trim()));
  }
  if (start === -1) return "";
  const collected = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^#{1,6}\s/.test(lines[index].trim())) break;
    collected.push(lines[index]);
  }
  return collected.join("\n").trim().replace(/\*\*/g, "").replace(/`/g, "");
}

function copyReviewVerdict() {
  const verdict = extractVerdict(state.review.reviewText || "");
  if (!verdict) {
    if (!state.review.reviewText) { showToast("Run a review first.", true); return; }
    copyText(state.review.reviewText, "No verdict section found — copied the full review instead.");
    return;
  }
  copyText(verdict, "Verdict copied — ready to paste as a comment.");
}

function renderMarkdown(text) {
  const escaped = escapeHtml(text);
  const inline = (value) => value
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "";
  let inList = false;
  let inCode = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of escaped.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (line.trim().startsWith("```")) {
      closeList();
      if (!inCode) { html += "<pre class='md-code'>"; inCode = true; }
      else { html += "</pre>"; inCode = false; }
      continue;
    }
    if (inCode) { html += raw + "\n"; continue; }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
    } else if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(bullet[1])}</li>`;
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      html += `<p>${inline(line)}</p>`;
    }
  }
  if (inCode) html += "</pre>";
  closeList();
  return html;
}

function actionIssueCard(issue) {
  return `<div class="action-card issue-card"><h3>${escapeHtml(issue.title)}</h3><p>#${issue.iid} · ${escapeHtml(shortProject(issue.project_path))}</p><div class="card-foot"><span class="status-badge assigned">${dueLabel(issue.due_date)}</span><a href="${escapeHtml(issue.web_url)}" target="_blank" rel="noreferrer">Open ↗</a></div></div>`;
}

function renderProjects() {
  if (!state.data) return;
  const needle = state.projectQuery.trim().toLowerCase();
  const items = state.data.projects.filter((project) => !needle || `${project.name} ${project.path_with_namespace}`.toLowerCase().includes(needle));
  const namespaces = new Map();
  items.forEach((project) => {
    const key = project.namespace || "Root";
    if (!namespaces.has(key)) namespaces.set(key, []);
    namespaces.get(key).push(project);
  });
  $("#project-summary").innerHTML = `
    <div class="summary-chip"><strong>${items.length}</strong><span>Visible projects</span></div>
    <div class="summary-chip"><strong>${namespaces.size}</strong><span>Project folders</span></div>
    <div class="summary-chip"><strong>${items.reduce((sum, project) => sum + project.open_mr_count, 0)}</strong><span>Open merge requests</span></div>`;
  const root = $("#folder-groups");
  if (!items.length) {
    root.innerHTML = emptyState("No project folders match", "Try a shorter project or subgroup name.");
    return;
  }
  root.innerHTML = [...namespaces.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([folder, projects]) => `
    <section class="folder-section"><div class="folder-heading"><span>▱</span><strong>${escapeHtml(folder)}</strong><small>${projects.length} project${projects.length === 1 ? "" : "s"}</small></div><div class="project-list">
      ${projects.sort((a,b) => a.name.localeCompare(b.name)).map((project) => `<a class="project-card" href="${escapeHtml(project.web_url)}" target="_blank" rel="noreferrer"><div><h3>${escapeHtml(project.name)}</h3><p>${escapeHtml(project.path_with_namespace)}</p></div><div class="project-stats"><span><b>${project.open_mr_count}</b><br>open MRs</span>${project.attention_count ? `<span><b>${project.attention_count}</b><br>attention</span>` : ""}</div></a>`).join("")}
    </div></section>`).join("");
}

function renderSettings() {
  const { config, data } = state;
  const live = data.mode === "live";
  const savedWaiting = state.pendingRefresh && config.token_configured;
  $("#settings-status-title").textContent = live ? "Connected to GitLab" : savedWaiting ? "Token saved — refresh when ready" : "Preview mode is active";
  $("#settings-status-copy").textContent = live
    ? `Signed in as ${data.current_user.name || data.current_user.username}.`
    : savedWaiting
      ? "Your validated token is stored locally. Preview data remains visible until you refresh."
      : "Connect a token here when you are ready to use live group data.";
  $("#config-group-url").textContent = config.group_url;
  $("#config-mode").textContent = live ? "Live GitLab data" : savedWaiting ? "Token ready; refresh required" : "Sample data";
  $("#config-write").textContent = config.write_actions_enabled ? "Enabled" : "Disabled (safe default)";
  $("#config-cache").textContent = `${config.cache_seconds} seconds`;
  $("#config-stale").textContent = `${config.stale_days} days`;
  $("#settings-connect-token").textContent = config.token_configured ? "Replace saved token" : "Connect with token";
  $("#toggle-write-actions").textContent = config.write_actions_enabled ? "Disable GitLab write actions" : "Enable GitLab write actions";
  $("#toggle-write-actions").disabled = !config.token_configured;
  $("#toggle-write-actions").title = config.token_configured ? "" : "Connect a token first";
  $("#connection-refresh-hint").classList.toggle("hidden", !savedWaiting);
}

async function toggleWriteActions() {
  const button = $("#toggle-write-actions");
  const enabled = !state.config.write_actions_enabled;
  if (enabled && !window.confirm(
    "Enable GitLab write actions?\n\nThe dashboard will be allowed to approve merge requests and post review comments using your token. Every action still requires its own confirmation."
  )) return;

  button.disabled = true;
  button.textContent = enabled ? "Enabling…" : "Disabling…";
  try {
    await api("/api/config/write-actions", {
      method: "PUT",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-GitLab-Focus-Action": "write-actions",
      },
      body: JSON.stringify({ enabled }),
    });
    state.config = await api("/api/config");
    renderSettings();
    showToast(`Approval actions ${enabled ? "enabled" : "disabled"}.`);
  } catch (error) {
    renderSettings();
    showToast(error.message, true);
  }
}

function openTokenModal() {
  $("#token-group-name").textContent = state.config?.group_path || "pia_restricted";
  $("#token-error").classList.add("hidden");
  $("#token-error").textContent = "";
  $("#gitlab-token").type = "password";
  $("#toggle-token").textContent = "Show";
  $("#token-modal-backdrop").classList.remove("hidden");
  $("#token-modal").classList.remove("hidden");
  window.setTimeout(() => $("#gitlab-token").focus(), 50);
}

function closeTokenModal() {
  $("#gitlab-token").value = "";
  $("#gitlab-token").type = "password";
  $("#toggle-token").textContent = "Show";
  $("#token-modal-backdrop").classList.add("hidden");
  $("#token-modal").classList.add("hidden");
}

async function saveToken(event) {
  event.preventDefault();
  const input = $("#gitlab-token");
  const submit = $("#token-submit");
  const errorRoot = $("#token-error");
  const token = input.value.trim();
  errorRoot.classList.add("hidden");
  if (token.length < 8 || /\s/.test(token)) {
    errorRoot.textContent = "Enter a valid token without spaces.";
    errorRoot.classList.remove("hidden");
    return;
  }

  submit.disabled = true;
  submit.textContent = "Validating…";
  try {
    const result = await api("/api/connection/token", {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-GitLab-Focus-Action": "connect",
      },
      body: JSON.stringify({ token }),
    });
    state.config = await api("/api/config");
    state.pendingRefresh = true;
    closeTokenModal();
    renderSettings();
    $("#refresh-button").classList.add("refresh-needed");
    showToast(`Token validated for ${result.user.name || result.user.username}. Refresh when you’re ready.`);
  } catch (error) {
    errorRoot.textContent = error.status === 404
      ? "The dashboard server is an older version. Close its console window, restart start_dashboard.bat, and try again."
      : error.message;
    errorRoot.classList.remove("hidden");
    input.focus();
    input.select();
  } finally {
    submit.disabled = false;
    submit.textContent = "Validate & save token";
  }
}

function emptyState(title, copy) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(copy)}</p></div>`;
}

function findMr(key) {
  const [projectId, iid] = String(key).split(":").map(Number);
  return state.data.merge_requests.find((mr) => mr.project_id === projectId && mr.iid === iid);
}

function bindMrOpeners(root) {
  $$('[data-mr-key]', root).forEach((element) => element.addEventListener("click", (event) => {
    if (event.target.closest("button, a")) return;
    openDrawer(findMr(element.dataset.mrKey));
  }));
}

function renderReviewerReadiness(readiness) {
  const summary = readiness.summary || {};
  const isReady = summary.status === "ready";
  const changes = readiness.change_summary || {};
  const suggestions = readiness.suggested_comments || [];
  const changeBits = [
    changes.commits != null ? `${escapeHtml(changes.commits)} commit${Number(changes.commits) === 1 ? "" : "s"}` : "",
    changes.changes != null ? `${escapeHtml(changes.changes)} changes` : "",
  ].filter(Boolean);
  return `
    <div class="readiness-summary ${isReady ? "ready" : "attention"}">
      <span class="readiness-icon">${isReady ? "✓" : "!"}</span>
      <span><strong>${isReady ? "Ready for review" : "Needs reviewer attention"}</strong><small>${summary.blocking_count || 0} blocker${summary.blocking_count === 1 ? "" : "s"}${summary.warning_count ? ` · ${summary.warning_count} pending` : ""}</small></span>
    </div>
    <div class="review-check-list">
      ${(readiness.checks || []).map((check) => `
        <div class="review-check ${escapeHtml(check.status)}">
          <span class="review-check-icon">${check.status === "pass" ? "✓" : check.status === "blocker" ? "!" : "i"}</span>
          <span><strong>${escapeHtml(check.label)}</strong><small>${escapeHtml(check.detail)}</small></span>
        </div>`).join("")}
    </div>
    ${changeBits.length ? `<p class="change-summary">Change scope: ${changeBits.join(" · ")}</p>` : ""}
    <div class="review-prompts"><strong>Reviewer prompts</strong><ul>${(readiness.manual_prompts || []).map((prompt) => `<li>${escapeHtml(prompt)}</li>`).join("")}</ul></div>
    ${suggestions.length ? `<div class="suggested-comment"><label for="review-comment-suggestion">Suggested GitLab comment</label><select id="review-comment-suggestion">${suggestions.map((comment, index) => `<option value="${index}">${escapeHtml(comment.label)}</option>`).join("")}</select><label for="review-comment-body">Message — edit before posting</label><textarea id="review-comment-body" rows="4" maxlength="3000">${escapeHtml(suggestions[0].body)}</textarea><button class="button button-secondary" id="post-suggested-comment">Post to GitLab</button><small>Posts only after you confirm the exact edited message.</small></div>` : ""}`;
}

function bindSuggestedCommentAction(root, mr, readiness) {
  const button = $("#post-suggested-comment", root);
  const select = $("#review-comment-suggestion", root);
  const message = $("#review-comment-body", root);
  if (!button || !select || !message) return;
  select.addEventListener("change", () => {
    const suggestion = (readiness.suggested_comments || [])[Number(select.value)];
    if (suggestion) message.value = suggestion.body;
  });
  button.addEventListener("click", async () => {
    const body = message.value.trim();
    if (body.length < 3) {
      showToast("Write a review comment before posting.", true);
      message.focus();
      return;
    }
    if (state.data.mode === "demo") {
      showToast("Connect GitLab before posting review comments.", true);
      return;
    }
    if (!state.config.write_actions_enabled) {
      showToast("GitLab write actions are disabled in .env.", true);
      return;
    }
    if (!window.confirm(`Post this review comment to GitLab?\n\n${body}`)) return;
    button.disabled = true;
    button.textContent = "Posting…";
    try {
      await api(`/api/merge-requests/${mr.project_id}/${mr.iid}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-GitLab-Focus-Action": "create-note" },
        body: JSON.stringify({ body }),
      });
      button.textContent = "Comment posted";
      showToast("Review comment posted to GitLab.");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Post to GitLab";
      showToast(error.message, true);
    }
  });
}

async function loadReviewerReadiness(mr, drawerKey) {
  const root = $("#reviewer-readiness");
  try {
    const readiness = await api(`/api/merge-requests/${mr.project_id}/${mr.iid}/reviewer-readiness`);
    if (state.drawerKey === drawerKey && root) {
      root.innerHTML = renderReviewerReadiness(readiness);
      bindSuggestedCommentAction(root, mr, readiness);
    }
  } catch (error) {
    if (state.drawerKey === drawerKey && root) {
      root.innerHTML = `<div class="review-readiness-error">Reviewer checks could not be loaded: ${escapeHtml(error.message)}</div>`;
    }
  }
}

function approvalDisabledReason(mr) {
  const user = state.data?.current_user || {};
  const authoredByMe = (user.id && mr.author?.id && user.id === mr.author.id)
    || (user.username && mr.author?.username && user.username === mr.author.username);
  if (authoredByMe) return "You cannot approve your own merge request.";
  if (mr.draft) return "Mark this merge request ready before approving.";
  if (mr.approved_by_me) return "You have already approved this merge request.";
  return "";
}

function openDrawer(mr) {
  if (!mr) return;
  const drawerKey = `${mr.project_id}:${mr.iid}`;
  state.drawerKey = drawerKey;
  const approvalReason = approvalDisabledReason(mr);
  const canApprove = !approvalReason;
  $("#drawer-content").innerHTML = `
    <h2 class="drawer-title">${escapeHtml(mr.title)}</h2>
    <p class="drawer-project">!${mr.iid} · ${escapeHtml(mr.project_path)}</p>
    <div class="drawer-status-row"><span class="status-badge ${mr.attention}">${escapeHtml(mr.attention_label)}</span><span class="pipeline ${escapeHtml(mr.pipeline_status)}"><i></i>${escapeHtml(mr.pipeline_status)} pipeline</span>${mr.has_conflicts ? '<span class="status-badge stale">Has conflicts</span>' : ""}</div>
    <section class="drawer-section reviewer-readiness"><div class="drawer-section-heading"><h3>Reviewer readiness</h3><span>Automated checks</span></div><div id="reviewer-readiness"><div class="reviewer-readiness-loading"><span class="loading-ring"></span><span>Checking merge request details…</span></div></div></section>
    <section class="drawer-section"><h3>People & activity</h3><div class="detail-grid"><div><span>Author</span><strong>${escapeHtml(mr.author.name)}</strong></div><div><span>Last updated</span><strong>${relativeTime(mr.updated_at)}</strong></div><div><span>Reviewers</span><strong>${escapeHtml(mr.reviewers.join(", ") || "None")}</strong></div><div><span>Comments</span><strong>${mr.user_notes_count || 0}</strong></div></div></section>
    <section class="drawer-section"><h3>Branches</h3><div class="detail-grid"><div><span>Source</span><strong>${escapeHtml(mr.source_branch || "—")}</strong></div><div><span>Target</span><strong>${escapeHtml(mr.target_branch || "—")}</strong></div><div><span>Approvals left</span><strong>${mr.approvals_left ?? "—"}</strong></div><div><span>Draft</span><strong>${mr.draft ? "Yes" : "No"}</strong></div></div></section>
    <section class="drawer-section"><h3>Labels</h3><div class="label-list">${mr.labels.length ? mr.labels.map((label) => `<span class="label">${escapeHtml(label)}</span>`).join("") : '<span class="mr-age">No labels</span>'}</div></section>
    <section class="drawer-section drawer-actions"><a class="button button-secondary" href="${escapeHtml(mr.web_url)}" target="_blank" rel="noreferrer">Open in GitLab ↗</a><button class="button" id="drawer-approve" ${canApprove ? "" : `disabled title="${escapeHtml(approvalReason)}"`}>Approve merge request</button></section>`;
  $("#detail-drawer").classList.add("open");
  $("#detail-drawer").setAttribute("aria-hidden", "false");
  $("#drawer-backdrop").classList.remove("hidden");
  if (canApprove) $("#drawer-approve").addEventListener("click", () => approveMr(mr.project_id, mr.iid, $("#drawer-approve")));
  loadReviewerReadiness(mr, drawerKey);
}

function closeDrawer() {
  state.drawerKey = "";
  $("#detail-drawer").classList.remove("open");
  $("#detail-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.add("hidden");
}

async function approveMr(projectId, iid, button) {
  const mr = findMr(`${projectId}:${iid}`);
  if (!mr) return;
  if (state.data.mode === "demo") {
    showToast("Connect GitLab before approving merge requests.", true);
    return;
  }
  if (!state.config.write_actions_enabled) {
    showToast("GitLab write actions are disabled in .env.", true);
    return;
  }
  if (!window.confirm(`Approve !${iid} — ${mr.title}?\n\nThis changes GitLab.`)) return;
  button.disabled = true;
  button.textContent = "Approving…";
  try {
    await api(`/api/merge-requests/${projectId}/${iid}/approve`, {
      method: "POST",
      headers: { "X-GitLab-Focus-Action": "approve" },
    });
    closeDrawer();
    showToast(`Merge request !${iid} approved.`);
    await refreshData(true);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Approve";
    showToast(error.message, true);
  }
}

async function refreshData(manual = false) {
  const button = $("#refresh-button");
  button.classList.add("refreshing");
  button.disabled = true;
  try {
    [state.data, state.config] = await Promise.all([
      api(manual ? "/api/dashboard?force=true" : "/api/dashboard"),
      api("/api/config"),
    ]);
    state.pendingRefresh = false;
    button.classList.remove("refresh-needed");
    renderAll();
    if (manual) showToast("Dashboard refreshed.");
  } catch (error) {
    $("#connection-dot").className = "status-dot error";
    $("#connection-text").textContent = "Connection error";
    showNotice(error.message);
  } finally {
    button.classList.remove("refreshing");
    button.disabled = false;
  }
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  $$('[data-view-link]').forEach((item) => item.addEventListener("click", () => setView(item.dataset.viewLink)));
  $$(".metric-card").forEach((card) => card.addEventListener("click", () => goToMergeRequests(card.dataset.filter)));
  $$("[data-health-filter]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.healthFilter === "pipeline_failed") goToMergeRequests("all", "failed");
    else goToMergeRequests(button.dataset.healthFilter);
  }));
  $$("#mr-filter-tabs button").forEach((button) => button.addEventListener("click", () => {
    state.filters.attention = button.dataset.attention;
    renderMergeRequests();
  }));
  $("#mr-search").addEventListener("input", (event) => { state.filters.q = event.target.value; renderMergeRequests(); });
  $("#project-filter").addEventListener("change", (event) => { state.filters.project = event.target.value; renderMergeRequests(); });
  $("#pipeline-filter").addEventListener("change", (event) => { state.filters.pipeline = event.target.value; renderMergeRequests(); });
  $("#clear-filters").addEventListener("click", () => {
    state.filters = { attention: "all", project: "", pipeline: "", q: "" };
    $("#mr-search").value = ""; $("#project-filter").value = ""; $("#pipeline-filter").value = "";
    renderMergeRequests();
  });
  $("#project-search").addEventListener("input", (event) => { state.projectQuery = event.target.value; renderProjects(); });
  $("#report-form").addEventListener("submit", generateReport);
  $("#report-copy").addEventListener("click", copyReport);
  $("#review-form").addEventListener("submit", generateBranchReview);
  $("#review-project").addEventListener("change", loadReviewBranches);
  $("#review-run-claude").addEventListener("click", runClaudeReview);
  $("#review-copy-prompt").addEventListener("click", copyReviewPrompt);
  $("#review-copy-diff").addEventListener("click", copyReviewDiff);
  $("#review-copy-result").addEventListener("click", copyReviewResult);
  $("#review-copy-verdict").addEventListener("click", copyReviewVerdict);
  $("#review-root-save").addEventListener("click", saveReviewRoot);
  $("#comments-post").addEventListener("click", postSelectedComments);
  $("#comments-select-all").addEventListener("click", () => {
    const boxes = $$('#comments-list [data-comment]');
    const allChecked = boxes.every((box) => box.checked);
    boxes.forEach((box) => { box.checked = !allChecked; });
    $("#comments-select-all").textContent = allChecked ? "Select all" : "Clear all";
  });
  $("#global-search").addEventListener("input", (event) => {
    state.filters.q = event.target.value;
    $("#mr-search").value = event.target.value;
    if (event.target.value.trim()) setView("merge-requests");
    else if (state.view === "merge-requests") renderMergeRequests();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#global-search").focus(); }
    if (event.key === "Escape") { closeDrawer(); closeTokenModal(); }
  });
  $("#refresh-button").addEventListener("click", () => refreshData(true));
  $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  $$('[data-connect-token]').forEach((button) => button.addEventListener("click", openTokenModal));
  $("#token-form").addEventListener("submit", saveToken);
  $("#token-modal-close").addEventListener("click", closeTokenModal);
  $("#token-cancel").addEventListener("click", closeTokenModal);
  $("#token-modal-backdrop").addEventListener("click", closeTokenModal);
  $("#toggle-token").addEventListener("click", () => {
    const input = $("#gitlab-token");
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    $("#toggle-token").textContent = showing ? "Show" : "Hide";
    input.focus();
  });
  $("#toggle-write-actions").addEventListener("click", toggleWriteActions);
}

async function init() {
  bindEvents();
  try {
    [state.config, state.data] = await Promise.all([api("/api/config"), api("/api/dashboard")]);
    renderAll();
  } catch (error) {
    $("#connection-dot").className = "status-dot error";
    $("#connection-text").textContent = "Connection error";
    showNotice(error.message);
  }
}

init();
