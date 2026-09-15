(() => {
  "use strict";

  const state = {
    chains: [],
    runs: [],
    config: null,
    editingChain: null, // { id|null, name, steps: [...] } while the modal is open
    openLogs: new Set(), // step-log element ids currently expanded, survives the 4s poll re-render
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  async function api(method, path, body, extraHeaders) {
    const response = await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(extraHeaders || {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      const detail = (payload && payload.detail) || `Request failed (${response.status})`;
      throw new Error(detail);
    }
    return payload;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
  }

  // ---------------------------------------------------------------- Nav

  function switchView(view) {
    $$(".nav-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
    $$(".view").forEach((section) => section.classList.toggle("hidden", section.id !== `view-${view}`));
    $("#view-title").textContent = { chains: "Chains", runs: "Runs", settings: "Settings" }[view];
    $("#new-chain-btn").classList.toggle("hidden", view !== "chains");
  }

  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));

  // ---------------------------------------------------------------- Config / status

  async function loadConfig() {
    state.config = await api("GET", "/api/config");
    const c = state.config;
    $("#jenkins-dot").classList.toggle("live", c.jenkins_configured);
    $("#jenkins-status-text").textContent = c.jenkins_configured ? `Jenkins: ${c.jenkins_user}` : "Jenkins: anonymous (no creds set)";
    $("#gitlab-dot").classList.toggle("live", c.gitlab_configured);
    $("#gitlab-status-text").textContent = c.gitlab_configured ? "GitLab connected" : "GitLab not configured";

    $("#cfg-jenkins-user").value = c.jenkins_user || "";
    $("#cfg-poll-seconds").value = c.poll_seconds;
    $("#cfg-max-retries").value = c.max_retries;
    $("#cfg-retry-delay").value = c.retry_delay_seconds;
  }

  $("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const statusEl = $("#settings-status");
    statusEl.textContent = "Saving...";
    statusEl.className = "hint";
    try {
      await api("PUT", "/api/config", {
        jenkins_user: $("#cfg-jenkins-user").value.trim(),
        jenkins_token: $("#cfg-jenkins-token").value,
        gitlab_token: $("#cfg-gitlab-token").value,
        poll_seconds: Number($("#cfg-poll-seconds").value),
        max_retries: Number($("#cfg-max-retries").value),
        retry_delay_seconds: Number($("#cfg-retry-delay").value),
      }, { "X-Jenkins-Chain-Action": "save-config" });
      $("#cfg-jenkins-token").value = "";
      $("#cfg-gitlab-token").value = "";
      statusEl.textContent = "Saved.";
      statusEl.className = "hint ok";
      await loadConfig();
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.className = "hint error";
    }
  });

  // ---------------------------------------------------------------- Chains list

  async function loadChains() {
    const data = await api("GET", "/api/chains");
    state.chains = data.items || [];
    renderChains();
  }

  function renderChains() {
    const root = $("#chains-list");
    if (!state.chains.length) {
      root.innerHTML = `<div class="empty-state">No chains yet. Click "New chain" to link your first Jenkins jobs together.</div>`;
      return;
    }
    root.innerHTML = state.chains.map((chain) => `
      <div class="card" data-chain-id="${chain.id}">
        <h3>${escapeHtml(chain.name)}</h3>
        <div class="card-meta">${chain.steps.length} step${chain.steps.length === 1 ? "" : "s"}</div>
        <div class="step-chip-list">
          ${chain.steps.map((step, index) => `
            <div class="step-chip">
              <span class="step-order">${index + 1}</span>
              <span class="step-chip-name">${escapeHtml(step.name)}</span>
              ${step.wait_for_existing_build ? `<span class="mr-badge badge-blue">reuse running</span>` : ""}
              ${step.retry_enabled === false ? `<span class="mr-badge badge-amber">no retry</span>` : ""}
              ${step.gitlab ? `<span class="mr-badge">auto-merge</span>` : ""}
            </div>
          `).join("") || `<div class="hint">No steps.</div>`}
        </div>
        <div class="card-actions">
          <button class="button button-small run-chain-btn">Run</button>
          <button class="button button-secondary button-small edit-chain-btn">Edit</button>
          <button class="button button-secondary button-small delete-chain-btn">Delete</button>
        </div>
      </div>
    `).join("");

    $$(".run-chain-btn", root).forEach((btn) => btn.addEventListener("click", (e) => {
      const id = e.target.closest("[data-chain-id]").dataset.chainId;
      openRunModal(state.chains.find((c) => c.id === id));
    }));
    $$(".edit-chain-btn", root).forEach((btn) => btn.addEventListener("click", (e) => {
      const id = e.target.closest("[data-chain-id]").dataset.chainId;
      openChainEditor(state.chains.find((c) => c.id === id));
    }));
    $$(".delete-chain-btn", root).forEach((btn) => btn.addEventListener("click", async (e) => {
      const id = e.target.closest("[data-chain-id]").dataset.chainId;
      const chain = state.chains.find((c) => c.id === id);
      if (!confirm(`Delete chain "${chain.name}"? This does not affect past runs.`)) return;
      await api("DELETE", `/api/chains/${id}`);
      await loadChains();
    }));
  }

  $("#new-chain-btn").addEventListener("click", () => openChainEditor(null));

  // ---------------------------------------------------------------- Chain editor modal

  function openChainEditor(chain) {
    state.editingChain = chain
      ? {
          id: chain.id, name: chain.name,
          steps: chain.steps.map((s) => ({
            ...s,
            _mrUrl: s.gitlab ? s.gitlab.mr_url : "",
            _mrProjectHint: s.gitlab ? s.gitlab.project_path : "",
          })),
        }
      : { id: null, name: "", steps: [] };
    renderModal(chainEditorHtml());
    wireChainEditor();
  }

  function chainEditorHtml() {
    const chain = state.editingChain;
    return `
      <div class="modal-head">
        <h2>${chain.id ? "Edit chain" : "New chain"}</h2>
        <button type="button" id="modal-close">&times;</button>
      </div>
      <label style="display:block;font-size:12px;color:var(--muted);font-weight:600;margin-bottom:14px;">
        Chain name
        <input type="text" id="chain-name-input" value="${escapeHtml(chain.name)}" placeholder="e.g. DoseWatch 2026.2.0 release" style="display:block;width:100%;margin-top:5px;padding:9px 11px;border:1px solid #dde3e1;border-radius:8px;" />
      </label>
      <div id="step-editor-list"></div>
      <button type="button" class="button button-secondary button-small" id="add-step-btn">+ Add Jenkins job</button>
      <div class="form-actions" style="margin-top:20px;">
        <button type="button" class="button" id="save-chain-btn">Save chain</button>
        <span class="hint" id="chain-editor-status"></span>
      </div>
    `;
  }

  function stepEditorHtml(step, index, total) {
    return `
      <div class="step-editor" data-index="${index}">
        <div class="step-editor-head">
          <strong>Step ${index + 1}</strong>
          <span class="spacer"></span>
          <button type="button" class="icon-btn move-up-btn" ${index === 0 ? "disabled" : ""} title="Move up">&uarr;</button>
          <button type="button" class="icon-btn move-down-btn" ${index === total - 1 ? "disabled" : ""} title="Move down">&darr;</button>
          <button type="button" class="icon-btn remove-step-btn" title="Remove">&times;</button>
        </div>
        <label>Label
          <input type="text" class="step-name-input" value="${escapeHtml(step.name || "")}" placeholder="e.g. dosewatch-all" />
        </label>
        <label>Jenkins job URL
          <input type="text" class="step-url-input" value="${escapeHtml(step.jenkins_job_url || "")}" placeholder="https://jenkins-dose.apps.ge-healthcare.net/job/.../job/release%252F2026.2.0/" />
        </label>
        <div class="check-row">
          <button type="button" class="button button-secondary button-small check-job-btn">Check job</button>
          <span class="hint check-job-result"></span>
        </div>
        <div class="search-row">
          <input type="text" class="step-job-search-input" placeholder="or search Jenkins jobs by name..." />
          <button type="button" class="button button-secondary button-small search-job-btn">Search</button>
        </div>
        <div class="search-results job-search-results hidden"></div>

        <label class="inline-checkbox">
          <input type="checkbox" class="step-wait-existing-input" ${step.wait_for_existing_build ? "checked" : ""} />
          <span>If this job is already running, wait for it instead of triggering a new build</span>
        </label>

        <div class="retry-box">
          <label class="inline-checkbox">
            <input type="checkbox" class="step-retry-enabled-input" ${step.retry_enabled === false ? "" : "checked"} />
            <span>Retry automatically on failure</span>
          </label>
          <div class="grid-2">
            <label>Max retries (blank = default)
              <input type="number" class="step-max-retries-input" min="0" max="20" placeholder="default" value="${step.max_retries ?? ""}" ${step.retry_enabled === false ? "disabled" : ""} />
            </label>
            <label>Retry delay, seconds (blank = default)
              <input type="number" class="step-retry-delay-input" min="5" max="3600" placeholder="default" value="${step.retry_delay_seconds ?? ""}" ${step.retry_enabled === false ? "disabled" : ""} />
            </label>
          </div>
        </div>

        <label>GitLab merge request URL (optional - auto-merge before the next step)
          <input type="text" class="step-mr-input" value="${escapeHtml(step._mrUrl || "")}" placeholder="https://gitlab.apps.ge-healthcare.net/<group>/<project>/-/merge_requests/123" />
        </label>
        <div class="check-row">
          <button type="button" class="button button-secondary button-small check-mr-btn">Check MR</button>
          <span class="hint check-mr-result"></span>
        </div>
        <div class="search-row">
          <input type="text" class="step-mr-project-input" value="${escapeHtml(step._mrProjectHint || "")}" placeholder="GitLab project path, e.g. pia_restricted/Installer/DoseWatch-Deliverables" />
        </div>
        <div class="search-row">
          <input type="text" class="step-mr-search-input" placeholder="search open MRs by title/branch..." />
          <button type="button" class="button button-secondary button-small search-mr-btn">Search</button>
        </div>
        <div class="search-results mr-search-results hidden"></div>

        <div class="retry-box">
          <label class="inline-checkbox">
            <input type="checkbox" class="step-verify-commit-input" ${step.verify_commit_before_merge === false ? "" : "checked"} />
            <span>Verify the exact tested commit before merging (fails safely instead of merging untested code if the branch moved since the build)</span>
          </label>
          <label class="inline-checkbox">
            <input type="checkbox" class="step-wait-target-input" ${step.wait_for_target_branch_update === false ? "" : "checked"} />
            <span>Wait for the target branch to reflect the merge before continuing to the next step</span>
          </label>
        </div>
      </div>
    `;
  }

  function renderStepEditors() {
    const chain = state.editingChain;
    $("#step-editor-list").innerHTML = chain.steps.map((step, index) => stepEditorHtml(step, index, chain.steps.length)).join("")
      || `<div class="hint" style="margin-bottom:12px;">No steps yet. Add the first Jenkins job below.</div>`;
    wireStepRows();
  }

  function parseOptionalInt(value) {
    const trimmed = String(value ?? "").trim();
    return trimmed === "" ? null : Number(trimmed);
  }

  function syncStepFromInputs(row) {
    const index = Number(row.dataset.index);
    const step = state.editingChain.steps[index];
    step.name = $(".step-name-input", row).value;
    step.jenkins_job_url = $(".step-url-input", row).value;
    step._mrUrl = $(".step-mr-input", row).value;
    step._mrProjectHint = $(".step-mr-project-input", row).value;
    step.wait_for_existing_build = $(".step-wait-existing-input", row).checked;
    step.retry_enabled = $(".step-retry-enabled-input", row).checked;
    step.max_retries = parseOptionalInt($(".step-max-retries-input", row).value);
    step.retry_delay_seconds = parseOptionalInt($(".step-retry-delay-input", row).value);
    step.verify_commit_before_merge = $(".step-verify-commit-input", row).checked;
    step.wait_for_target_branch_update = $(".step-wait-target-input", row).checked;
  }

  function wireStepRows() {
    $$(".step-editor").forEach((row) => {
      $$("input", row).forEach((input) => input.addEventListener("input", () => syncStepFromInputs(row)));

      $(".move-up-btn", row).addEventListener("click", () => {
        syncStepFromInputs(row);
        const index = Number(row.dataset.index);
        const steps = state.editingChain.steps;
        [steps[index - 1], steps[index]] = [steps[index], steps[index - 1]];
        renderStepEditors();
      });
      $(".move-down-btn", row).addEventListener("click", () => {
        syncStepFromInputs(row);
        const index = Number(row.dataset.index);
        const steps = state.editingChain.steps;
        [steps[index + 1], steps[index]] = [steps[index], steps[index + 1]];
        renderStepEditors();
      });
      $(".remove-step-btn", row).addEventListener("click", () => {
        const index = Number(row.dataset.index);
        state.editingChain.steps.splice(index, 1);
        renderStepEditors();
      });
      $(".step-retry-enabled-input", row).addEventListener("change", (e) => {
        $(".step-max-retries-input", row).disabled = !e.target.checked;
        $(".step-retry-delay-input", row).disabled = !e.target.checked;
      });
      $(".check-job-btn", row).addEventListener("click", async () => {
        syncStepFromInputs(row);
        const index = Number(row.dataset.index);
        const resultEl = $(".check-job-result", row);
        resultEl.className = "hint";
        resultEl.textContent = "Checking...";
        try {
          const info = await api("POST", "/api/jenkins/check", { job_url: state.editingChain.steps[index].jenkins_job_url });
          resultEl.className = "hint ok";
          resultEl.textContent = `OK: ${info.name}${info.last_build_number ? ` (last build #${info.last_build_number})` : ""}`;
        } catch (err) {
          resultEl.className = "hint error";
          resultEl.textContent = err.message;
        }
      });
      $(".check-mr-btn", row).addEventListener("click", async () => {
        syncStepFromInputs(row);
        const index = Number(row.dataset.index);
        const resultEl = $(".check-mr-result", row);
        const mrUrl = state.editingChain.steps[index]._mrUrl;
        if (!mrUrl.trim()) {
          resultEl.className = "hint error";
          resultEl.textContent = "Paste a merge request URL first.";
          return;
        }
        resultEl.className = "hint";
        resultEl.textContent = "Checking...";
        try {
          const mr = await api("POST", "/api/gitlab/check-mr", { mr_url: mrUrl });
          resultEl.className = "hint ok";
          resultEl.textContent = `${mr.title} [${mr.state}] ${mr.source_branch} -> ${mr.target_branch}`;
        } catch (err) {
          resultEl.className = "hint error";
          resultEl.textContent = err.message;
        }
      });
      $(".search-job-btn", row).addEventListener("click", async () => {
        const query = $(".step-job-search-input", row).value.trim();
        const resultsEl = $(".job-search-results", row);
        if (!query) {
          resultsEl.classList.add("hidden");
          return;
        }
        resultsEl.classList.remove("hidden");
        resultsEl.innerHTML = `<div class="search-result-empty">Searching...</div>`;
        try {
          const { items } = await api("POST", "/api/jenkins/search-jobs", { query });
          resultsEl.innerHTML = items.length
            ? items.map((item) => `<button type="button" class="search-result-item" data-url="${escapeHtml(item.url)}">${escapeHtml(item.name)}</button>`).join("")
            : `<div class="search-result-empty">No matching jobs.</div>`;
          $$(".search-result-item", resultsEl).forEach((btn) => btn.addEventListener("click", () => {
            $(".step-url-input", row).value = btn.dataset.url;
            syncStepFromInputs(row);
            resultsEl.classList.add("hidden");
          }));
        } catch (err) {
          resultsEl.innerHTML = `<div class="search-result-empty">${escapeHtml(err.message)}</div>`;
        }
      });
      $(".search-mr-btn", row).addEventListener("click", async () => {
        syncStepFromInputs(row);
        const index = Number(row.dataset.index);
        const projectPath = state.editingChain.steps[index]._mrProjectHint.trim();
        const query = $(".step-mr-search-input", row).value.trim();
        const resultsEl = $(".mr-search-results", row);
        if (!projectPath) {
          resultsEl.classList.remove("hidden");
          resultsEl.innerHTML = `<div class="search-result-empty">Enter the GitLab project path above first.</div>`;
          return;
        }
        resultsEl.classList.remove("hidden");
        resultsEl.innerHTML = `<div class="search-result-empty">Searching...</div>`;
        try {
          const { items } = await api("POST", "/api/gitlab/search-mrs", { project_path: projectPath, query });
          resultsEl.innerHTML = items.length
            ? items.map((mr) => `<button type="button" class="search-result-item" data-url="${escapeHtml(mr.web_url)}">!${mr.iid} ${escapeHtml(mr.title)} <span class="search-result-sub">${escapeHtml(mr.source_branch)} &rarr; ${escapeHtml(mr.target_branch)}</span></button>`).join("")
            : `<div class="search-result-empty">No matching open MRs.</div>`;
          $$(".search-result-item", resultsEl).forEach((btn) => btn.addEventListener("click", () => {
            $(".step-mr-input", row).value = btn.dataset.url;
            syncStepFromInputs(row);
            resultsEl.classList.add("hidden");
          }));
        } catch (err) {
          resultsEl.innerHTML = `<div class="search-result-empty">${escapeHtml(err.message)}</div>`;
        }
      });
    });
  }

  function wireChainEditor() {
    $("#modal-close").addEventListener("click", closeModal);
    $("#add-step-btn").addEventListener("click", () => {
      state.editingChain.steps.push({
        name: "", jenkins_job_url: "", _mrUrl: "", _mrProjectHint: "",
        retry_enabled: true, max_retries: null, retry_delay_seconds: null,
        wait_for_existing_build: false,
        verify_commit_before_merge: true, wait_for_target_branch_update: true,
      });
      renderStepEditors();
    });
    $("#save-chain-btn").addEventListener("click", saveChain);
    renderStepEditors();
  }

  async function saveChain() {
    const chain = state.editingChain;
    const statusEl = $("#chain-editor-status");
    chain.name = $("#chain-name-input").value.trim();
    if (!chain.name) {
      statusEl.className = "hint error";
      statusEl.textContent = "Give the chain a name.";
      return;
    }
    if (!chain.steps.length) {
      statusEl.className = "hint error";
      statusEl.textContent = "Add at least one Jenkins job.";
      return;
    }
    const payload = {
      name: chain.name,
      steps: chain.steps.map((step) => ({
        name: step.name.trim() || "Untitled step",
        jenkins_job_url: step.jenkins_job_url.trim(),
        gitlab_mr_url: (step._mrUrl || "").trim(),
        build_params: {},
        retry_enabled: step.retry_enabled !== false,
        max_retries: step.max_retries ?? null,
        retry_delay_seconds: step.retry_delay_seconds ?? null,
        wait_for_existing_build: !!step.wait_for_existing_build,
        verify_commit_before_merge: step.verify_commit_before_merge !== false,
        wait_for_target_branch_update: step.wait_for_target_branch_update !== false,
      })),
    };
    for (const step of payload.steps) {
      if (!step.jenkins_job_url) {
        statusEl.className = "hint error";
        statusEl.textContent = `"${step.name}" is missing a Jenkins job URL.`;
        return;
      }
    }
    statusEl.className = "hint";
    statusEl.textContent = "Saving...";
    try {
      if (chain.id) {
        await api("PUT", `/api/chains/${chain.id}`, payload);
      } else {
        await api("POST", "/api/chains", payload);
      }
      closeModal();
      await loadChains();
    } catch (err) {
      statusEl.className = "hint error";
      statusEl.textContent = err.message;
    }
  }

  // ---------------------------------------------------------------- Run modal

  function openRunModal(chain) {
    const hasMerge = chain.steps.some((s) => s.gitlab);
    renderModal(`
      <div class="modal-head">
        <h2>Run "${escapeHtml(chain.name)}"</h2>
        <button type="button" id="modal-close">&times;</button>
      </div>
      <p class="hint">This will trigger <strong>${escapeHtml(chain.steps[0]?.name || "step 1")}</strong> now, poll it to completion, then continue through all ${chain.steps.length} step(s) in order.</p>
      ${hasMerge ? `
        <div class="checkbox-row">
          <input type="checkbox" id="auto-merge-checkbox" />
          <label for="auto-merge-checkbox">
            Allow this run to automatically merge the linked GitLab merge request(s) once their Jenkins job succeeds.
            If unchecked, the run pauses at each merge step for you to confirm in the Runs tab.
          </label>
        </div>
      ` : ""}
      <div class="form-actions">
        <button type="button" class="button" id="start-run-btn">Start run</button>
        <span class="hint" id="run-modal-status"></span>
      </div>
    `);
    $("#modal-close").addEventListener("click", closeModal);
    $("#start-run-btn").addEventListener("click", async () => {
      const autoMerge = hasMerge ? $("#auto-merge-checkbox").checked : false;
      const statusEl = $("#run-modal-status");
      statusEl.textContent = "Starting...";
      try {
        await api("POST", `/api/chains/${chain.id}/run`, { auto_merge_confirmed: autoMerge });
        closeModal();
        switchView("runs");
        await loadRuns();
      } catch (err) {
        statusEl.className = "hint error";
        statusEl.textContent = err.message;
      }
    });
  }

  // ---------------------------------------------------------------- Runs

  const STATUS_LABEL = {
    idle: "Idle", queued: "Queued in Jenkins", running: "Building",
    success: "Succeeded", failed: "Failed", merging: "Merging MR",
    merged: "Merged", merge_failed: "Merge failed",
    waiting_merge_confirm: "Waiting on merge confirmation", skipped: "Skipped",
  };
  const RUN_STATUS_LABEL = {
    running: "Running", completed: "Completed", cancelled: "Cancelled",
    paused_failed: "Paused - failed", paused_for_merge: "Paused - needs merge confirm",
  };

  async function loadRuns() {
    const data = await api("GET", "/api/runs");
    state.runs = data.items || [];
    const activeCount = state.runs.filter((r) => r.status === "running" || r.status === "paused_failed" || r.status === "paused_for_merge").length;
    const countEl = $("#active-run-count");
    countEl.textContent = activeCount;
    countEl.classList.toggle("hidden", activeCount === 0);
    renderRuns();
  }

  function stepUrl(step) {
    if (step.build_url) return `${step.build_url}console`;
    if (step.queue_url) return step.queue_url;
    return null;
  }

  function formatLogTime(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (_) {
      return iso;
    }
  }

  function runCardHtml(run) {
    const stepsHtml = run.steps.map((step) => {
      const link = stepUrl(step);
      const log = step.log || [];
      const logId = `log-${run.id}-${step.step_id}`;
      return `
        <div class="step-row">
          <span class="step-dot ${step.status}"></span>
          <div>
            <div class="step-name">${escapeHtml(step.name)}</div>
            <div class="step-sub">
              ${STATUS_LABEL[step.status] || step.status}
              ${step.build_number ? ` &middot; build #${step.build_number}` : ""}
              ${step.git_branch ? ` &middot; ${escapeHtml(step.git_branch)}${step.git_commit ? ` @ ${escapeHtml(step.git_commit.slice(0, 10))}` : ""}` : ""}
              ${step.attempt > 1 ? ` &middot; attempt ${step.attempt}` : ""}
              ${step.retry_enabled === false ? ` &middot; no auto-retry` : ""}
              ${step.wait_for_existing_build ? ` &middot; reuses a running build` : ""}
              ${link ? ` &middot; <a class="step-link" href="${link}" target="_blank" rel="noopener">console</a>` : ""}
              ${step.gitlab ? ` &middot; <a class="step-link" href="${escapeHtml(step.gitlab.mr_url)}" target="_blank" rel="noopener">MR !${step.gitlab.mr_iid}</a>` : ""}
              ${log.length ? ` &middot; <button type="button" class="step-link log-toggle-btn" data-target="${logId}">view log (${log.length})</button>` : ""}
            </div>
            ${step.error ? `<div class="step-error">${escapeHtml(step.error)}</div>` : ""}
            ${log.length ? `
              <div class="step-log ${state.openLogs.has(logId) ? "" : "hidden"}" id="${logId}">
                ${log.map((entry) => `
                  <div class="step-log-row">
                    <span class="step-log-time">${formatLogTime(entry.at)}</span>
                    <span class="step-log-msg">${escapeHtml(entry.message)}</span>
                  </div>
                `).join("")}
              </div>
            ` : ""}
          </div>
          <div></div>
        </div>
      `;
    }).join("");

    let actions = "";
    if (run.status === "paused_failed") {
      const step = run.steps[run.current_step_index];
      const label = step && step.status === "merge_failed" ? "Skip merge" : "Skip step";
      actions = `
        <button class="button button-small retrigger-btn">Retrigger</button>
        <button class="button button-secondary button-small skip-btn">${label}</button>
      `;
    } else if (run.status === "paused_for_merge") {
      actions = `
        <button class="button button-amber button-small merge-continue-btn">Merge &amp; continue</button>
        <button class="button button-secondary button-small skip-btn">Skip merge</button>
      `;
    } else if (run.status === "running") {
      actions = `<button class="button button-danger button-small cancel-btn">Cancel</button>`;
    }

    return `
      <div class="run-card" data-run-id="${run.id}">
        <div class="run-card-head">
          <div>
            <h3>${escapeHtml(run.chain_name)}</h3>
            <div class="card-meta">Started ${timeAgo(run.started_at)} &middot; step ${Math.min(run.current_step_index + 1, run.steps.length)} of ${run.steps.length}</div>
          </div>
          <div style="display:flex; align-items:center; gap:10px;">
            <span class="badge badge-${run.status}">${RUN_STATUS_LABEL[run.status] || run.status}</span>
          </div>
        </div>
        <div class="step-list" style="margin-top:10px;">${stepsHtml}</div>
        <div class="card-actions">${actions}</div>
      </div>
    `;
  }

  function renderRuns() {
    const root = $("#runs-list");
    if (!state.runs.length) {
      root.innerHTML = `<div class="empty-state">No runs yet. Start one from the Chains tab.</div>`;
      return;
    }
    root.innerHTML = state.runs.map(runCardHtml).join("");

    $$(".log-toggle-btn", root).forEach((btn) => btn.addEventListener("click", (e) => {
      const id = e.currentTarget.dataset.target;
      const el = document.getElementById(id);
      if (!el) return;
      if (el.classList.contains("hidden")) {
        el.classList.remove("hidden");
        state.openLogs.add(id);
      } else {
        el.classList.add("hidden");
        state.openLogs.delete(id);
      }
    }));
    $$(".retrigger-btn", root).forEach((btn) => btn.addEventListener("click", async (e) => {
      const id = e.target.closest("[data-run-id]").dataset.runId;
      await api("POST", `/api/runs/${id}/retrigger`);
      await loadRuns();
    }));
    $$(".merge-continue-btn", root).forEach((btn) => btn.addEventListener("click", async (e) => {
      const id = e.target.closest("[data-run-id]").dataset.runId;
      if (!confirm("Merge the linked merge request now and continue the chain?")) return;
      try {
        await api("POST", `/api/runs/${id}/confirm-merge`, undefined, { "X-Jenkins-Chain-Action": "confirm-merge" });
      } catch (err) {
        alert(err.message);
      }
      await loadRuns();
    }));
    $$(".skip-btn", root).forEach((btn) => btn.addEventListener("click", async (e) => {
      const id = e.target.closest("[data-run-id]").dataset.runId;
      if (!confirm("Skip this step and continue to the next one in the chain?")) return;
      await api("POST", `/api/runs/${id}/skip-merge`);
      await loadRuns();
    }));
    $$(".cancel-btn", root).forEach((btn) => btn.addEventListener("click", async (e) => {
      const id = e.target.closest("[data-run-id]").dataset.runId;
      if (!confirm("Cancel this run?")) return;
      await api("POST", `/api/runs/${id}/cancel`);
      await loadRuns();
    }));
  }

  // ---------------------------------------------------------------- Modal helper

  function renderModal(innerHtml) {
    $("#modal-root").innerHTML = `<div class="modal-backdrop" id="modal-backdrop"><div class="modal">${innerHtml}</div></div>`;
    $("#modal-backdrop").addEventListener("click", (e) => {
      if (e.target.id === "modal-backdrop") closeModal();
    });
  }

  function closeModal() {
    $("#modal-root").innerHTML = "";
    state.editingChain = null;
  }

  // ---------------------------------------------------------------- Boot

  async function refreshAll() {
    $("#refresh-time").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    await Promise.all([loadConfig(), loadChains(), loadRuns()]);
  }

  refreshAll().catch((err) => console.error(err));
  setInterval(() => loadRuns().catch((err) => console.error(err)), 4000);
})();
