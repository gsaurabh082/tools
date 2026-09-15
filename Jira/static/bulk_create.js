const tokenInput = document.querySelector("#token");
const authType = document.querySelector("#auth-type");
const apiMode = document.querySelector("#api-mode");
const usernameField = document.querySelector("#username-field");
const formError = document.querySelector("#form-error");
const parseError = document.querySelector("#parse-error");
const pastePanel = document.querySelector("#paste-panel");
const uploadPanel = document.querySelector("#upload-panel");
const modePasteButton = document.querySelector("#mode-paste");
const modeUploadButton = document.querySelector("#mode-upload");
const previewCard = document.querySelector("#preview-card");
const resultsCard = document.querySelector("#results-card");
const createButton = document.querySelector("#create-button");

let parsedRows = [];
let expandedPreview = [];
let selectedPersons = new Set();
let selectedRowKeys = new Set();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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
    document.querySelector("#project-key").value =
      localStorage.getItem("jira-bulk-project-key") || "";
    document.querySelector("#issue-type").value =
      localStorage.getItem("jira-bulk-issue-type") || "Story";
    document.querySelector("#max-points").value =
      localStorage.getItem("jira-bulk-max-points") || "5";
    document.querySelector("#default-epic").value =
      localStorage.getItem("jira-bulk-default-epic") || "";
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

document.querySelector("#toggle-token").addEventListener("click", (event) => {
  const showing = tokenInput.type === "text";
  tokenInput.type = showing ? "password" : "text";
  event.currentTarget.textContent = showing ? "Show" : "Hide";
});
authType.addEventListener("change", updateAuthFields);
apiMode.addEventListener("change", updateAuthFields);

modePasteButton.addEventListener("click", () => {
  pastePanel.hidden = false;
  uploadPanel.hidden = true;
  modePasteButton.classList.add("active");
  modeUploadButton.classList.remove("active");
});
modeUploadButton.addEventListener("click", () => {
  pastePanel.hidden = true;
  uploadPanel.hidden = false;
  modeUploadButton.classList.add("active");
  modePasteButton.classList.remove("active");
});
modePasteButton.classList.add("active");

document.querySelector("#clear-button").addEventListener("click", () => {
  document.querySelector("#paste-area").value = "";
  document.querySelector("#excel-file").value = "";
  parsedRows = [];
  expandedPreview = [];
  selectedPersons = new Set();
  selectedRowKeys = new Set();
  document.querySelector("#person-filter").innerHTML = "";
  previewCard.hidden = true;
  resultsCard.hidden = true;
  parseError.hidden = true;
});

function uniquePersons(rows) {
  const seen = new Set();
  const ordered = [];
  rows.forEach((row) => {
    const person = row.person.trim();
    if (person && !seen.has(person)) {
      seen.add(person);
      ordered.push(person);
    }
  });
  return ordered.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function renderPersonFilter() {
  const persons = uniquePersons(parsedRows);
  const container = document.querySelector("#person-filter");
  if (!persons.length) {
    container.innerHTML = "";
    return;
  }
  const allChecked = persons.every((person) => selectedPersons.has(person));
  container.innerHTML =
    `<span class="person-filter-label">Create for</span>` +
    `<label class="person-chip"><input type="checkbox" id="person-filter-all" ${allChecked ? "checked" : ""}><span>All (${persons.length})</span></label>` +
    persons.map((person) => `<label class="person-chip">
        <input type="checkbox" class="person-filter-check" value="${escapeHtml(person)}" ${selectedPersons.has(person) ? "checked" : ""}>
        <span>${escapeHtml(person)}</span>
      </label>`).join("");

  document.querySelector("#person-filter-all").addEventListener("change", (event) => {
    if (event.target.checked) persons.forEach((person) => selectedPersons.add(person));
    else selectedPersons.clear();
    renderPersonFilter();
    renderPreview();
  });
  container.querySelectorAll(".person-filter-check").forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      if (event.target.checked) selectedPersons.add(event.target.value);
      else selectedPersons.delete(event.target.value);
      renderPersonFilter();
      renderPreview();
    });
  });
}

// --- Column matching, shared by pasted text and Excel headers ---

const COLUMN_KEYWORDS = {
  person: ["person", "owner", "assignee", "name"],
  heading: ["heading", "story title", "title", "summary", "requirement"],
  epic_link: ["epic link", "epic id", "epic", "parent link", "parent"],
  story_points: ["story points", "story point estimate", "points", "sp"],
  description: ["description"],
  acceptance_criteria: ["acceptance criteria", "acceptance criteria (given/when/then)", "ac"],
};

function detectColumns(headerCells) {
  const mapping = {};
  const used = new Set();
  headerCells.forEach((cell, index) => {
    const normalized = String(cell ?? "").trim().toLowerCase();
    if (!normalized) return;
    for (const [field, keywords] of Object.entries(COLUMN_KEYWORDS)) {
      if (used.has(field)) continue;
      if (keywords.some((keyword) => normalized === keyword || normalized.includes(keyword))) {
        mapping[field] = index;
        used.add(field);
        break;
      }
    }
  });
  return mapping;
}

function looksLikeHeaderRow(cells) {
  const joined = cells.map((cell) => String(cell ?? "").trim().toLowerCase()).join(" ");
  return Object.values(COLUMN_KEYWORDS).some((keywords) =>
    keywords.some((keyword) => joined.includes(keyword))
  );
}

function splitLine(line) {
  if (line.includes("\t")) return line.split("\t");
  if (line.includes("|")) return line.split("|");
  return line.split(",");
}

function rowsFromCells(cellRows) {
  if (!cellRows.length) return [];
  let dataRows = cellRows;
  let mapping = { person: 0, heading: 1, epic_link: 2, story_points: 3 };
  if (looksLikeHeaderRow(cellRows[0])) {
    const detected = detectColumns(cellRows[0]);
    if (Object.keys(detected).length) mapping = detected;
    dataRows = cellRows.slice(1);
  }
  const rows = [];
  for (const cells of dataRows) {
    const get = (field) =>
      mapping[field] !== undefined ? String(cells[mapping[field]] ?? "").trim() : "";
    const person = get("person");
    const heading = get("heading");
    if (!person && !heading) continue;
    const pointsRaw = get("story_points");
    const points = Number.parseInt(pointsRaw, 10);
    rows.push({
      person,
      heading,
      epic_link: get("epic_link"),
      story_points: Number.isFinite(points) && points > 0 ? points : 3,
      description: get("description"),
      acceptance_criteria: get("acceptance_criteria"),
    });
  }
  return rows;
}

function parsePastedText(text) {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const cellRows = lines.map(splitLine).map((cells) => cells.map((cell) => cell.trim()));
  return rowsFromCells(cellRows);
}

function parseExcelFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the Excel file."));
    reader.onload = () => {
      try {
        const workbook = XLSX.read(reader.result, { type: "array" });
        const preferredNames = ["Jira Stories", "Bulk Create", "Stories"];
        const sheetName =
          preferredNames.find((name) => workbook.SheetNames.includes(name)) ||
          workbook.SheetNames[0];
        const sheet = workbook.Sheets[sheetName];
        const cellRows = XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: "" });
        resolve(rowsFromCells(cellRows));
      } catch (error) {
        reject(new Error("Could not parse that Excel file: " + error.message));
      }
    };
    reader.readAsArrayBuffer(file);
  });
}

// --- Split preview (mirrors the server's splitting so the preview matches) ---

function splitPoints(total, maxPoints) {
  if (total <= maxPoints) return [total];
  const parts = Math.ceil(total / maxPoints);
  const base = Math.floor(total / parts);
  const remainder = total % parts;
  const chunks = [];
  for (let i = 0; i < parts; i += 1) chunks.push(i < remainder ? base + 1 : base);
  return chunks;
}

function expandRows(entries, maxPoints, defaultEpic) {
  const expanded = [];
  entries.forEach(({ row, index }) => {
    const chunks = splitPoints(row.story_points, maxPoints);
    chunks.forEach((points, partIndex) => {
      expanded.push({
        rowIndex: index,
        person: row.person,
        heading: chunks.length > 1 ? `${row.heading} (Part ${partIndex + 1} of ${chunks.length})` : row.heading,
        epic_link: row.epic_link || defaultEpic,
        story_points: points,
        description: row.description,
        acceptance_criteria: row.acceptance_criteria,
        part: chunks.length > 1 ? partIndex + 1 : 0,
        total_parts: chunks.length,
      });
    });
  });
  return expanded;
}

function personFilteredEntries() {
  return parsedRows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => selectedPersons.size === 0 || selectedPersons.has(row.person));
}

// Entries that will actually be created: person filter AND the per-row checkbox.
function selectedEntries() {
  return personFilteredEntries().filter(({ index }) => selectedRowKeys.has(index));
}

function renderPreview() {
  const maxPoints = Number.parseInt(document.querySelector("#max-points").value, 10) || 5;
  const defaultEpic = document.querySelector("#default-epic").value.trim();

  // Every row for the currently selected person(s), shown with a checkbox so
  // individual stories can be included/excluded without hiding them.
  const visibleEntries = personFilteredEntries();
  const displayRows = expandRows(visibleEntries, maxPoints, defaultEpic);
  // Only the checked rows: this is what actually gets created.
  const selected = selectedEntries();
  expandedPreview = expandRows(selected, maxPoints, defaultEpic);

  document.querySelector("#preview-table").innerHTML = displayRows.length
    ? displayRows.map((row) => {
        const checked = selectedRowKeys.has(row.rowIndex);
        return `<tr class="${checked ? "" : "row-unselected"}">
        <td><input type="checkbox" class="row-select" data-row-index="${row.rowIndex}" ${checked ? "checked" : ""}></td>
        <td class="owner-name">${escapeHtml(row.person) || "—"}</td>
        <td>${escapeHtml(row.heading)}</td>
        <td><input type="text" class="epic-edit" data-row-index="${row.rowIndex}"
              value="${escapeHtml(row.epic_link)}" placeholder="e.g. SD-1000 (none yet)"></td>
        <td>${row.story_points}</td>
        <td>${row.total_parts > 1 ? `Part ${row.part} of ${row.total_parts}` : "—"}</td>
      </tr>`;
      }).join("")
    : '<tr><td class="no-rows" colspan="6">No rows match the selected person filter.</td></tr>';

  document.querySelectorAll(".epic-edit").forEach((input) => {
    input.addEventListener("change", (event) => {
      const idx = Number.parseInt(event.target.dataset.rowIndex, 10);
      if (Number.isFinite(idx) && parsedRows[idx]) {
        parsedRows[idx].epic_link = event.target.value.trim();
      }
      renderPreview();
    });
  });

  document.querySelectorAll(".row-select").forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      const idx = Number.parseInt(event.target.dataset.rowIndex, 10);
      if (!Number.isFinite(idx)) return;
      if (event.target.checked) selectedRowKeys.add(idx);
      else selectedRowKeys.delete(idx);
      renderPreview();
    });
  });

  const selectAllCheckbox = document.querySelector("#preview-select-all");
  if (selectAllCheckbox) {
    selectAllCheckbox.checked =
      visibleEntries.length > 0 &&
      visibleEntries.every(({ index }) => selectedRowKeys.has(index));
  }

  const splitCount = selected.filter(({ row }) => splitPoints(row.story_points, maxPoints).length > 1).length;
  document.querySelector("#preview-summary").textContent =
    `${selected.length} of ${parsedRows.length} input row(s) selected → ${expandedPreview.length} Jira issue(s)` +
    (splitCount ? ` · ${splitCount} row(s) split above ${maxPoints} points` : "");
  previewCard.hidden = parsedRows.length === 0;
  resultsCard.hidden = true;
}

document.querySelector("#preview-select-all").addEventListener("change", (event) => {
  const checked = event.target.checked;
  personFilteredEntries().forEach(({ index }) => {
    if (checked) selectedRowKeys.add(index);
    else selectedRowKeys.delete(index);
  });
  renderPreview();
});

document.querySelector("#parse-button").addEventListener("click", async () => {
  parseError.hidden = true;
  try {
    if (uploadPanel.hidden) {
      parsedRows = parsePastedText(document.querySelector("#paste-area").value);
    } else {
      const file = document.querySelector("#excel-file").files[0];
      if (!file) throw new Error("Choose an Excel file first.");
      parsedRows = await parseExcelFile(file);
    }
    if (!parsedRows.length) throw new Error("No rows found. Check the format and try again.");
    selectedPersons = new Set(uniquePersons(parsedRows));
    renderPersonFilter();
    renderPreview();
  } catch (error) {
    parsedRows = [];
    selectedPersons = new Set();
    document.querySelector("#person-filter").innerHTML = "";
    previewCard.hidden = true;
    parseError.textContent = error.message || "Could not parse the input.";
    parseError.hidden = false;
  }
});

document.querySelectorAll("#max-points, #default-epic").forEach((el) =>
  el.addEventListener("input", () => {
    if (parsedRows.length) renderPreview();
  })
);

function statusTone(status) {
  return status === "created" ? "good" : "bad";
}

function renderResults(data) {
  const results = data.results || [];
  document.querySelector("#results-table").innerHTML = results.length
    ? results.map((row) => `<tr>
        <td><span class="score-pill ${statusTone(row.status)}">${escapeHtml(row.status)}</span></td>
        <td>${row.issue_key
          ? `<a class="issue-key" href="${escapeHtml(row.issue_url)}" target="_blank" rel="noopener">${escapeHtml(row.issue_key)}</a>`
          : "—"}</td>
        <td>${escapeHtml(row.heading)}</td>
        <td class="owner-name">${escapeHtml(row.person) || "—"}</td>
        <td>${escapeHtml(row.epic_link) || "—"}</td>
        <td>${row.story_points}</td>
        <td>${escapeHtml(row.error) || "—"}</td>
      </tr>`).join("")
    : '<tr><td class="no-rows" colspan="7">No results.</td></tr>';
  document.querySelector("#results-summary").textContent =
    `${data.created_count} created, ${data.failed_count} failed · Project ${data.project_key} · ${data.jira_base_url}`;
  resultsCard.hidden = false;
}

createButton.addEventListener("click", async () => {
  formError.hidden = true;
  if (!expandedPreview.length) return;

  const projectKey = document.querySelector("#project-key").value.trim();
  if (!projectKey) {
    formError.textContent = "Enter a Jira project key first.";
    formError.hidden = false;
    return;
  }
  if (!confirm(`Create ${expandedPreview.length} Jira issue(s) in project ${projectKey}? This cannot be undone from here.`)) {
    return;
  }

  const maxPoints = Number.parseInt(document.querySelector("#max-points").value, 10) || 5;
  const defaultEpic = document.querySelector("#default-epic").value.trim();

  localStorage.setItem("jira-report-base-url", document.querySelector("#base-url").value.trim());
  localStorage.setItem("jira-report-api-mode", apiMode.value);
  localStorage.setItem("jira-report-auth-type", authType.value);
  localStorage.setItem("jira-report-username", document.querySelector("#username").value.trim());
  localStorage.setItem("jira-bulk-project-key", projectKey);
  localStorage.setItem("jira-bulk-issue-type", document.querySelector("#issue-type").value.trim());
  localStorage.setItem("jira-bulk-max-points", String(maxPoints));
  localStorage.setItem("jira-bulk-default-epic", defaultEpic);

  const payload = {
    base_url: document.querySelector("#base-url").value.trim(),
    api_mode: apiMode.value,
    auth_type: authType.value,
    username: document.querySelector("#username").value.trim(),
    token: tokenInput.value,
    save_token: document.querySelector("#save-token").checked,
    project_key: projectKey,
    issue_type: document.querySelector("#issue-type").value.trim() || "Story",
    max_points_per_story: maxPoints,
    stories: selectedEntries().map(({ row }) => ({
      person: row.person,
      heading: row.heading,
      epic_link: row.epic_link || defaultEpic,
      story_points: row.story_points,
      description: row.description,
      acceptance_criteria: row.acceptance_criteria,
    })),
  };

  createButton.disabled = true;
  createButton.classList.add("loading");
  try {
    const response = await fetch("/api/bulk-create", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jira-Friday-Action": "bulk-create-issues" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Bulk creation failed.");
    }
    tokenInput.value = "";
    renderResults(data);
  } catch (error) {
    formError.textContent = error.message || "Bulk creation failed.";
    formError.hidden = false;
  } finally {
    createButton.disabled = false;
    createButton.classList.remove("loading");
  }
});

loadConfiguration();
