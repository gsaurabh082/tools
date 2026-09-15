const baseUrlInput = document.querySelector("#base-url");
const apiModeSelect = document.querySelector("#api-mode");
const authTypeSelect = document.querySelector("#auth-type");
const usernameField = document.querySelector("#username-field");
const usernameInput = document.querySelector("#username");
const tokenInput = document.querySelector("#token");
const toggleTokenButton = document.querySelector("#toggle-token");
const savedTokenStatus = document.querySelector("#saved-token-status");

const sprintInput = document.querySelector("#sprint");
const refreshSprintsButton = document.querySelector("#refresh-sprints");
const sprintPicker = document.querySelector("#sprint-picker");
const sprintOptions = document.querySelector("#sprint-options");
const sprintPickerStatus = document.querySelector("#sprint-picker-status");

const rosterInput = document.querySelector("#roster-input");
const rosterAddButton = document.querySelector("#roster-add-button");
const rosterChips = document.querySelector("#roster-chips");

const loadRosterButton = document.querySelector("#load-roster-button");
const standupError = document.querySelector("#standup-error");

const standupEmpty = document.querySelector("#standup-empty");
const standupLoading = document.querySelector("#standup-loading");
const standupBody = document.querySelector("#standup-body");
const standupCaption = document.querySelector("#standup-caption");
const standupDateInput = document.querySelector("#standup-date");
const standupPeople = document.querySelector("#standup-people");
const statusFilterBar = document.querySelector("#status-filter-bar");
const statusFilterOptions = document.querySelector("#status-filter-options");
const statusFilterAllButton = document.querySelector("#status-filter-all");
const statusFilterNoneButton = document.querySelector("#status-filter-none");

const generateButton = document.querySelector("#generate-standup-report");
const copyTextButton = document.querySelector("#copy-standup-text");
const standupStatus = document.querySelector("#standup-status");
const standupDownload = document.querySelector("#standup-download");
const standupDownloadLink = document.querySelector("#standup-download-link");
const standupDownloadCsvLink = document.querySelector("#standup-download-csv-link");

const personTemplate = document.querySelector("#standup-person-template");
const ticketRowTemplate = document.querySelector("#standup-ticket-row-template");
const chipTemplate = document.querySelector("#roster-chip-template");

let availableSprints = [];
let rosterMembers = [];
let currentSprint = "";
let savedCredentialAvailable = false;
let selectedStatuses = new Set();
let sprintsLoading = false;

const STORAGE_PREFIX = "jira-standup-";
function savePref(key, value) {
  localStorage.setItem(STORAGE_PREFIX + key, value);
}
function loadPref(key) {
  return localStorage.getItem(STORAGE_PREFIX + key);
}
function saveRosterPref() {
  localStorage.setItem(STORAGE_PREFIX + "roster", JSON.stringify(rosterMembers));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function todayIso() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function showResultState(name) {
  standupEmpty.hidden = name !== "empty";
  standupLoading.hidden = name !== "loading";
  standupBody.hidden = name !== "body";
}

function updateAuthFields() {
  const selected = authTypeSelect.value;
  const serverMode = apiModeSelect.value;
  usernameField.hidden = selected === "bearer" || (selected === "auto" && serverMode === "server");
}

function renderRosterChips() {
  rosterChips.innerHTML = "";
  rosterMembers.forEach((member) => {
    const fragment = chipTemplate.content.cloneNode(true);
    const chip = fragment.querySelector("[data-chip]");
    chip.querySelector("[data-chip-label]").textContent = member;
    chip.querySelector(".roster-chip-remove").addEventListener("click", () => {
      rosterMembers = rosterMembers.filter((item) => item !== member);
      renderRosterChips();
      saveRosterPref();
    });
    rosterChips.appendChild(chip);
  });
}

function addRosterMember() {
  const value = rosterInput.value.trim();
  if (!value || rosterMembers.includes(value)) {
    rosterInput.value = "";
    return;
  }
  rosterMembers.push(value);
  rosterInput.value = "";
  renderRosterChips();
  saveRosterPref();
}

async function loadConfig() {
  // Restore whatever was last used on this page before falling back to server defaults,
  // so the page opens in its previous state until the user changes something.
  const storedBaseUrl = loadPref("base-url");
  const storedApiMode = loadPref("api-mode");
  const storedAuthType = loadPref("auth-type");
  const storedUsername = loadPref("username");
  const storedSprint = loadPref("sprint");
  const storedRoster = loadPref("roster");
  if (storedBaseUrl) baseUrlInput.value = storedBaseUrl;
  if (storedApiMode) apiModeSelect.value = storedApiMode;
  if (storedAuthType) authTypeSelect.value = storedAuthType;
  if (storedUsername) usernameInput.value = storedUsername;
  if (storedSprint) sprintInput.value = storedSprint;
  if (storedRoster) {
    try {
      const parsed = JSON.parse(storedRoster);
      if (Array.isArray(parsed)) rosterMembers = parsed;
    } catch {
      // Ignore malformed stored roster and fall back to config defaults below.
    }
  }
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    if (!baseUrlInput.value) baseUrlInput.value = config.base_url || "";
    if (!storedApiMode && config.api_mode) apiModeSelect.value = config.api_mode;
    if (!rosterMembers.length && Array.isArray(config.team_members)) {
      rosterMembers = [...config.team_members];
    }
    renderRosterChips();
    const saved = config.saved_credentials || {};
    if (saved.available) {
      savedCredentialAvailable = true;
      if (!baseUrlInput.value) baseUrlInput.value = saved.base_url || "";
      if (!usernameInput.value) usernameInput.value = saved.username || "";
      if (!storedAuthType && saved.auth_type) authTypeSelect.value = saved.auth_type;
      tokenInput.placeholder = "Saved token will be used — enter only to replace it";
      savedTokenStatus.textContent = "A token is securely saved for this Windows account.";
      savedTokenStatus.hidden = false;
    }
    updateAuthFields();
  } catch {
    renderRosterChips();
    standupError.textContent = "The local report service is not ready. Refresh this page.";
    standupError.hidden = false;
  }
}

function connectionPayload() {
  return {
    base_url: baseUrlInput.value.trim(),
    api_mode: apiModeSelect.value,
    auth_type: authTypeSelect.value,
    username: usernameInput.value.trim(),
    token: tokenInput.value,
  };
}

function renderSprintOptions(filterText) {
  const needle = filterText.trim().toLowerCase();
  const matches = availableSprints.filter((sprint) => {
    if (!needle) return true;
    return String(sprint.id).includes(needle) || sprint.name.toLowerCase().includes(needle);
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
        savePref("sprint", sprintInput.value);
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
  standupError.hidden = true;
  try {
    const response = await fetch("/api/sprints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(connectionPayload()),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Could not load sprints.");
    }
    availableSprints = data.sprints || [];
    if (!availableSprints.length) {
      sprintPickerStatus.textContent = "No active or upcoming sprints were found.";
      sprintPickerStatus.hidden = false;
      return;
    }
    if (!sprintInput.value) {
      const active = availableSprints.find((sprint) => sprint.state === "Active");
      if (active) {
        sprintInput.value = String(active.id);
        sprintPickerStatus.textContent = `Auto-selected the active sprint: ${active.id} — ${active.name}.`;
        sprintPickerStatus.hidden = false;
        savePref("sprint", sprintInput.value);
      }
    }
    renderSprintOptions(sprintInput.value);
  } catch (error) {
    standupError.textContent = error.message || "Could not load sprints.";
    standupError.hidden = false;
  } finally {
    sprintsLoading = false;
    refreshSprintsButton.textContent = "Refresh";
  }
}

function statusSlug(status) {
  return String(status || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "unknown";
}

function buildTicketRow(ticket) {
  const fragment = ticketRowTemplate.content.cloneNode(true);
  const row = fragment.querySelector("[data-ticket-row]");
  const statusLabel = (ticket.status || "").trim() || "Unknown";
  row.dataset.ticketKey = ticket.key || "";
  row.dataset.ticketStatus = statusLabel;
  const keyCell = row.querySelector("[data-ticket-key]");
  keyCell.textContent = ticket.key || "—";
  row.querySelector("[data-ticket-summary]").textContent = ticket.summary || "";
  const statusCell = row.querySelector("[data-ticket-status]");
  statusCell.textContent = statusLabel;
  statusCell.className = `ticket-status-pill ticket-status-${statusSlug(statusLabel)}`;
  return row;
}

function buildPersonCard(person) {
  const fragment = personTemplate.content.cloneNode(true);
  const card = fragment.querySelector("[data-person]");
  card.dataset.personId = person.id || "";
  card.dataset.personName = person.display_name || person.id || "Unassigned";
  card.querySelector("[data-person-name]").textContent =
    person.display_name || person.id || "Unassigned";
  const tbody = card.querySelector("[data-ticket-body]");
  const issues = person.issues || [];
  if (!issues.length) {
    const emptyRow = document.createElement("tr");
    emptyRow.innerHTML = '<td colspan="3">No tickets in this sprint.</td>';
    tbody.appendChild(emptyRow);
  } else {
    issues.forEach((issue) => tbody.appendChild(buildTicketRow(issue)));
  }
  return card;
}

function applyStatusFilter() {
  standupPeople.querySelectorAll("[data-ticket-row]").forEach((row) => {
    row.hidden = !selectedStatuses.has(row.dataset.ticketStatus || "");
  });
}

function renderStatusFilterOptions(statuses) {
  statusFilterOptions.innerHTML = "";
  if (!statuses.length) {
    statusFilterBar.hidden = true;
    return;
  }
  statusFilterBar.hidden = false;
  statuses.forEach((status) => {
    const label = document.createElement("label");
    label.className = "status-filter-chip";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedStatuses.has(status);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedStatuses.add(status);
      else selectedStatuses.delete(status);
      applyStatusFilter();
    });
    const text = document.createElement("span");
    text.textContent = status;
    label.appendChild(checkbox);
    label.appendChild(text);
    statusFilterOptions.appendChild(label);
  });
}

function initStatusFilter() {
  const statuses = [];
  const seen = new Set();
  standupPeople.querySelectorAll("[data-ticket-row]").forEach((row) => {
    const status = row.dataset.ticketStatus || "";
    if (status && !seen.has(status)) {
      seen.add(status);
      statuses.push(status);
    }
  });
  statuses.sort((a, b) => a.localeCompare(b));
  selectedStatuses = new Set(statuses);
  renderStatusFilterOptions(statuses);
  applyStatusFilter();
}

statusFilterAllButton.addEventListener("click", () => {
  statusFilterOptions.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.checked = true;
    selectedStatuses.add(box.nextElementSibling.textContent);
  });
  applyStatusFilter();
});
statusFilterNoneButton.addEventListener("click", () => {
  statusFilterOptions.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.checked = false;
  });
  selectedStatuses.clear();
  applyStatusFilter();
});

async function loadRosterAndTickets() {
  standupError.hidden = true;
  if (!sprintInput.value.trim()) {
    standupError.textContent = "Pick a sprint first (use Find sprints).";
    standupError.hidden = false;
    return;
  }
  loadRosterButton.disabled = true;
  loadRosterButton.classList.add("loading");
  showResultState("loading");
  try {
    const response = await fetch("/api/standup-roster", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...connectionPayload(),
        sprint: sprintInput.value.trim(),
        team_members: rosterMembers,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Could not load the sprint roster.");
    }
    currentSprint = data.sprint;
    standupPeople.innerHTML = "";
    (data.roster || []).forEach((person) => standupPeople.appendChild(buildPersonCard(person)));
    standupCaption.textContent = `Sprint ${currentSprint} · ${(data.roster || []).length} teammate(s)`;
    initStatusFilter();
    showResultState("body");
    standupDownload.hidden = true;
    standupStatus.hidden = true;
    await loadSavedStandup(standupDateInput.value || todayIso());
  } catch (error) {
    showResultState("empty");
    standupError.textContent = error.message || "Could not load the sprint roster.";
    standupError.hidden = false;
  } finally {
    loadRosterButton.disabled = false;
    loadRosterButton.classList.remove("loading");
  }
}

async function loadSavedStandup(dateValue) {
  try {
    const response = await fetch(`/api/standup?standup_date=${encodeURIComponent(dateValue)}`);
    if (!response.ok) return;
    const data = await response.json();
    const entries = Array.isArray(data.entries) ? data.entries : [];
    if (!entries.length || String(data.sprint || "") !== String(currentSprint || "")) return;
    entries.forEach((entry) => {
      const card = Array.from(standupPeople.querySelectorAll("[data-person]")).find(
        (candidate) =>
          (entry.person_id && candidate.dataset.personId === entry.person_id) ||
          candidate.dataset.personName === entry.person
      );
      if (!card) return;
      card.querySelector(".standup-blockers").value = entry.blockers || "";
      card.querySelector(".standup-notes").value = entry.notes || "";
      (entry.tickets || []).forEach((ticket) => {
        const row = Array.from(card.querySelectorAll("[data-ticket-row]")).find(
          (candidate) => candidate.dataset.ticketKey === ticket.key
        );
        if (row) {
          const field = row.querySelector(".standup-ticket-update");
          if (field) field.value = ticket.update || "";
        }
      });
    });
    standupStatus.textContent = `Loaded ${entries.length} saved update(s) for ${dateValue}.`;
    standupStatus.hidden = false;
  } catch {
    // No saved standup for this date/sprint yet — that's fine.
  }
}

function collectEntries() {
  return Array.from(standupPeople.querySelectorAll("[data-person]")).map((card) => {
    const tickets = Array.from(card.querySelectorAll("[data-ticket-row]"))
      .filter((row) => row.dataset.ticketKey && !row.hidden)
      .map((row) => ({
        key: row.dataset.ticketKey,
        summary: row.querySelector("[data-ticket-summary]").textContent,
        status: row.dataset.ticketStatus || "",
        update: row.querySelector(".standup-ticket-update").value.trim(),
      }));
    return {
      person_id: card.dataset.personId || "",
      person: card.dataset.personName || "Unassigned",
      tickets,
      blockers: card.querySelector(".standup-blockers").value.trim(),
      notes: card.querySelector(".standup-notes").value.trim(),
    };
  });
}

function buildStandupText() {
  const dateValue = standupDateInput.value || todayIso();
  const entries = collectEntries();
  const lines = [dateValue, ""];
  entries.forEach((entry, index) => {
    lines.push(entry.person || "Unassigned");
    entry.tickets.forEach((ticket) => {
      lines.push(`${ticket.key} ${ticket.status}`.trim());
      lines.push(ticket.update || "—");
    });
    lines.push(`Additional details: ${entry.notes || "—"}`);
    lines.push(`Blocker: ${entry.blockers || "None"}`);
    if (index < entries.length - 1) lines.push("");
  });
  return lines.join("\n");
}

async function copyStandupAsText() {
  standupError.hidden = true;
  const entries = collectEntries();
  if (!entries.length) {
    standupError.textContent = "Load the sprint roster before copying the standup.";
    standupError.hidden = false;
    return;
  }
  const text = buildStandupText();
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    standupStatus.textContent = "Standup copied to clipboard as text.";
    standupStatus.hidden = false;
  } catch {
    standupError.textContent = "Could not copy to the clipboard. Check your browser's clipboard permissions.";
    standupError.hidden = false;
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
  setTimeout(() => savePref("sprint", sprintInput.value.trim()), 150);
});
document.addEventListener("click", (event) => {
  if (sprintOptions.hidden === false && !sprintPicker.contains(event.target)) {
    sprintOptions.hidden = true;
  }
});

baseUrlInput.addEventListener("blur", () => savePref("base-url", baseUrlInput.value.trim()));
usernameInput.addEventListener("blur", () => savePref("username", usernameInput.value.trim()));
apiModeSelect.addEventListener("change", () => savePref("api-mode", apiModeSelect.value));
authTypeSelect.addEventListener("change", () => savePref("auth-type", authTypeSelect.value));

rosterAddButton.addEventListener("click", addRosterMember);
rosterInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addRosterMember();
  }
});

toggleTokenButton.addEventListener("click", (event) => {
  const showing = tokenInput.type === "text";
  tokenInput.type = showing ? "password" : "text";
  event.currentTarget.textContent = showing ? "Show" : "Hide";
});

authTypeSelect.addEventListener("change", updateAuthFields);
apiModeSelect.addEventListener("change", updateAuthFields);

loadRosterButton.addEventListener("click", loadRosterAndTickets);
standupDateInput.addEventListener("change", () => {
  if (standupDateInput.value) loadSavedStandup(standupDateInput.value);
});

generateButton.addEventListener("click", async () => {
  standupError.hidden = true;
  standupDownload.hidden = true;
  const entries = collectEntries();
  if (!entries.length) {
    standupError.textContent = "Load the sprint roster before generating a report.";
    standupError.hidden = false;
    return;
  }
  generateButton.disabled = true;
  generateButton.classList.add("loading");
  try {
    const response = await fetch("/api/standup-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        standup_date: standupDateInput.value || todayIso(),
        sprint: currentSprint,
        entries,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "The report could not be generated.");
    }
    standupStatus.textContent = `Standup saved for ${data.standup_date} (sprint ${data.sprint}).`;
    standupStatus.hidden = false;
    standupDownloadLink.href = data.download;
    standupDownloadCsvLink.href = data.download_csv;
    standupDownload.hidden = false;
    window.open(data.download, "_blank");
  } catch (error) {
    standupError.textContent = error.message || "The report could not be generated.";
    standupError.hidden = false;
  } finally {
    generateButton.disabled = false;
    generateButton.classList.remove("loading");
  }
});

copyTextButton.addEventListener("click", copyStandupAsText);

standupDateInput.value = todayIso();
loadConfig();
