(() => {
  const els = {
    heroUser: document.getElementById("hero-default-user"),
    form: document.getElementById("host-form"),
    hostId: document.getElementById("host-id"),
    alias: document.getElementById("alias"),
    ip: document.getElementById("ip"),
    username: document.getElementById("username"),
    password: document.getElementById("password"),
    togglePassword: document.getElementById("toggle-password"),
    keepPasswordRow: document.getElementById("keep-password-row"),
    keepPassword: document.getElementById("keep-password"),
    passwordHint: document.getElementById("password-hint"),
    formEyebrow: document.getElementById("form-eyebrow"),
    formTitle: document.getElementById("form-title"),
    saveButton: document.getElementById("save-button"),
    cancelEdit: document.getElementById("cancel-edit"),
    formError: document.getElementById("form-error"),
    listError: document.getElementById("list-error"),
    hostCount: document.getElementById("host-count"),
    emptyState: document.getElementById("empty-state"),
    hostsTable: document.getElementById("hosts-table"),
    hostsBody: document.getElementById("hosts-body"),
  };

  let defaultUsername = ".\\vmadmin";
  let editingId = null;

  function showError(el, message) {
    if (!message) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = message;
  }

  async function apiFetch(url, options) {
    const response = await fetch(url, options);
    let body = null;
    try {
      body = await response.json();
    } catch (_err) {
      body = null;
    }
    if (!response.ok) {
      const detail = (body && body.detail) || `Request failed (${response.status}).`;
      throw new Error(detail);
    }
    return body;
  }

  function resetForm() {
    editingId = null;
    els.hostId.value = "";
    els.form.reset();
    els.username.value = "";
    els.username.placeholder = defaultUsername;
    els.keepPasswordRow.hidden = true;
    els.keepPassword.checked = true;
    els.password.placeholder = "Leave blank to use the default password";
    els.passwordHint.textContent = "Blank = default password (LocalAdm1n!). Type one to save it for this host.";
    els.formEyebrow.textContent = "ADD HOST";
    els.formTitle.textContent = "Save a new host";
    els.saveButton.querySelector(".button-label").textContent = "Save host";
    els.cancelEdit.hidden = true;
    showError(els.formError, "");
  }

  function startEdit(host) {
    editingId = host.id;
    els.hostId.value = host.id;
    els.alias.value = host.alias;
    els.ip.value = host.ip;
    els.username.value = host.username === defaultUsername ? "" : host.username;
    els.username.placeholder = defaultUsername;
    els.password.value = "";
    els.password.placeholder = host.custom_password ? "Leave blank to keep the saved password" : "Leave blank to use the default password";
    els.keepPasswordRow.hidden = false;
    els.keepPassword.checked = true;
    els.passwordHint.textContent = "Leave blank to keep what's saved. Type a new one to replace it.";
    els.formEyebrow.textContent = "EDIT HOST";
    els.formTitle.textContent = `Edit ${host.alias}`;
    els.saveButton.querySelector(".button-label").textContent = "Save changes";
    els.cancelEdit.hidden = false;
    showError(els.formError, "");
    els.alias.focus();
  }

  function statusBadge(text, kind) {
    const span = document.createElement("span");
    span.className = `status-pill${kind ? " " + kind : ""}`;
    span.textContent = text;
    return span;
  }

  function renderHosts(hosts) {
    els.hostsBody.innerHTML = "";
    els.hostCount.textContent = hosts.length === 1 ? "1 host saved" : `${hosts.length} hosts saved`;
    els.emptyState.hidden = hosts.length > 0;
    els.hostsTable.hidden = hosts.length === 0;

    for (const host of hosts) {
      const row = document.createElement("tr");

      const aliasCell = document.createElement("td");
      aliasCell.textContent = host.alias;
      aliasCell.className = "owner-name";
      row.appendChild(aliasCell);

      const ipCell = document.createElement("td");
      ipCell.textContent = host.ip;
      row.appendChild(ipCell);

      const userCell = document.createElement("td");
      userCell.textContent = host.username;
      row.appendChild(userCell);

      const passCell = document.createElement("td");
      passCell.className = "status-cell";
      passCell.appendChild(statusBadge(host.custom_password ? "Custom" : "Default", host.custom_password ? "good" : ""));
      row.appendChild(passCell);

      const statusCell = document.createElement("td");
      statusCell.className = "status-cell";
      statusCell.dataset.role = "status";
      statusCell.appendChild(statusBadge("Not tested", ""));
      row.appendChild(statusCell);

      const actionsCell = document.createElement("td");
      actionsCell.className = "actions-cell";

      const connectBtn = document.createElement("button");
      connectBtn.type = "button";
      connectBtn.className = "row-button row-button-primary";
      connectBtn.textContent = "Connect";
      connectBtn.addEventListener("click", () => connectHost(host, connectBtn, statusCell));
      actionsCell.appendChild(connectBtn);

      const testBtn = document.createElement("button");
      testBtn.type = "button";
      testBtn.className = "row-button";
      testBtn.textContent = "Test";
      testBtn.addEventListener("click", () => testHost(host, testBtn, statusCell));
      actionsCell.appendChild(testBtn);

      const restartBtn = document.createElement("button");
      restartBtn.type = "button";
      restartBtn.className = "row-button row-button-warning";
      restartBtn.textContent = "Restart";
      restartBtn.addEventListener("click", () => restartHost(host, restartBtn, statusCell));
      actionsCell.appendChild(restartBtn);

      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "row-button";
      editBtn.textContent = "Edit";
      editBtn.addEventListener("click", () => startEdit(host));
      actionsCell.appendChild(editBtn);

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "row-button row-button-danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", () => deleteHost(host));
      actionsCell.appendChild(deleteBtn);

      row.appendChild(actionsCell);
      els.hostsBody.appendChild(row);
    }
  }

  async function loadHosts() {
    showError(els.listError, "");
    try {
      const data = await apiFetch("/api/hosts");
      renderHosts(data.hosts || []);
    } catch (err) {
      showError(els.listError, err.message);
    }
  }

  async function testHost(host, button, statusCell) {
    button.disabled = true;
    statusCell.innerHTML = "";
    statusCell.appendChild(statusBadge("Testing…", ""));
    try {
      const result = await apiFetch(`/api/hosts/${host.id}/test`, { method: "POST" });
      statusCell.innerHTML = "";
      statusCell.appendChild(
        statusBadge(result.reachable ? "Reachable" : "Unreachable", result.reachable ? "good" : "bad")
      );
    } catch (err) {
      statusCell.innerHTML = "";
      statusCell.appendChild(statusBadge("Test failed", "bad"));
    } finally {
      button.disabled = false;
    }
  }

  async function connectHost(host, button, statusCell) {
    button.disabled = true;
    showError(els.listError, "");
    try {
      await apiFetch(`/api/hosts/${host.id}/connect`, { method: "POST" });
      statusCell.innerHTML = "";
      statusCell.appendChild(statusBadge("Launched", "good"));
    } catch (err) {
      showError(els.listError, `${host.alias}: ${err.message}`);
      statusCell.innerHTML = "";
      statusCell.appendChild(statusBadge("Failed", "bad"));
    } finally {
      button.disabled = false;
    }
  }

  async function restartHost(host, button, statusCell) {
    if (!window.confirm(`Restart "${host.alias}" (${host.ip}) now? Any unsaved work on that VM will be lost.`)) {
      return;
    }
    button.disabled = true;
    showError(els.listError, "");
    statusCell.innerHTML = "";
    statusCell.appendChild(statusBadge("Restarting…", ""));
    try {
      await apiFetch(`/api/hosts/${host.id}/restart`, { method: "POST" });
      statusCell.innerHTML = "";
      statusCell.appendChild(statusBadge("Restart sent", "good"));
    } catch (err) {
      showError(els.listError, `${host.alias}: ${err.message}`);
      statusCell.innerHTML = "";
      statusCell.appendChild(statusBadge("Restart failed", "bad"));
    } finally {
      button.disabled = false;
    }
  }

  async function deleteHost(host) {
    if (!window.confirm(`Delete "${host.alias}" (${host.ip})?`)) return;
    showError(els.listError, "");
    try {
      await apiFetch(`/api/hosts/${host.id}`, { method: "DELETE" });
      if (editingId === host.id) resetForm();
      await loadHosts();
    } catch (err) {
      showError(els.listError, err.message);
    }
  }

  els.togglePassword.addEventListener("click", () => {
    const showing = els.password.type === "text";
    els.password.type = showing ? "password" : "text";
    els.togglePassword.textContent = showing ? "Show" : "Hide";
  });

  els.password.addEventListener("input", () => {
    if (!els.keepPasswordRow.hidden && els.password.value) {
      els.keepPassword.checked = false;
    }
  });

  els.cancelEdit.addEventListener("click", resetForm);

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(els.formError, "");
    els.saveButton.disabled = true;
    els.saveButton.classList.add("loading");

    const payload = {
      alias: els.alias.value.trim(),
      ip: els.ip.value.trim(),
      username: els.username.value.trim(),
      password: els.password.value,
    };

    try {
      if (editingId) {
        payload.keep_password = els.keepPassword.checked;
        await apiFetch(`/api/hosts/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await apiFetch("/api/hosts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      resetForm();
      await loadHosts();
    } catch (err) {
      showError(els.formError, err.message);
    } finally {
      els.saveButton.disabled = false;
      els.saveButton.classList.remove("loading");
    }
  });

  (async function init() {
    try {
      const info = await apiFetch("/api/defaults");
      defaultUsername = info.username || defaultUsername;
      els.heroUser.textContent = defaultUsername;
      els.username.placeholder = defaultUsername;
    } catch (_err) {
      // Non-fatal: keep the built-in default.
    }
    await loadHosts();
  })();
})();
