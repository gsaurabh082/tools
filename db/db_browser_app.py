#!/usr/bin/env python3
"""
db_browser_app.py

A DBeaver-style local web UI for browsing the DoseWatch MySQL database
(default schema name: "serphydose").

This replaces the earlier CLI script with a real point-and-click tool:
  - Left panel: a tree of every table, just like DBeaver's Database Navigator.
    Click a table to expand its columns inline AND open it in a tab on the right.
    Click the root "serphydose" node to open an Overview tab listing all tables.
  - "Expand All" button expands every table's columns in one shot (like DBeaver's
    expand-all toolbar icon), instead of clicking each one.
  - A filter box above the tree narrows the list live as you type (table or column
    name), like DBeaver's navigator filter.
  - Each opened table tab shows its column schema (name/type/nullable/key) and has
    its own "search this table's data" box.
  - A pinned "Search Data" tab greps a value across every table's columns (like
    DBeaver/HeidiSQL's "Find text on server"), with live progress as it scans.

--------------------------------------------------------------------------------------
INSTALL (run once)
--------------------------------------------------------------------------------------
pip install pymysql --break-system-packages

--------------------------------------------------------------------------------------
RUN
--------------------------------------------------------------------------------------
python db_browser_app.py --host HOST --user USER --password PASS --database serphydose

Then it opens http://127.0.0.1:8765 in your browser automatically (add --no-browser
to skip that). Add --port to change the MySQL port (default 3306) and --web-port to
change the local web UI port (default 8765).
"""

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB = None  # set in main(), a MySQLBrowser instance


class MySQLBrowser:
    def __init__(self, host, port, user, password, database):
        import pymysql
        self.database = database
        self.conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            database=database, cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, autocommit=True,
        )

    def list_tables(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME AS name, TABLE_ROWS AS row_count "
                "FROM information_schema.tables WHERE table_schema=%s "
                "ORDER BY TABLE_NAME",
                (self.database,),
            )
            return cur.fetchall()

    def list_columns(self, table):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type, "
                "IS_NULLABLE AS nullable, COLUMN_KEY AS col_key "
                "FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s "
                "ORDER BY ORDINAL_POSITION",
                (self.database, table),
            )
            return cur.fetchall()

    def list_all_columns(self):
        """One query for every table's columns, grouped by table. Used by Expand All
        and by the tree/column search so we don't fire hundreds of requests."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, "
                "COLUMN_TYPE AS type, IS_NULLABLE AS nullable, COLUMN_KEY AS col_key "
                "FROM information_schema.columns WHERE table_schema=%s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (self.database,),
            )
            rows = cur.fetchall()
        grouped = {}
        for r in rows:
            grouped.setdefault(r["table_name"], []).append({
                "name": r["name"], "type": r["type"],
                "nullable": r["nullable"], "col_key": r["col_key"],
            })
        return grouped

    def search_value(self, table, value, limit=1000):
        cols = self.list_columns(table)
        if not cols:
            return []
        conditions = " OR ".join(
            f"CAST(`{c['name']}` AS CHAR(4000)) LIKE %s" for c in cols
        )
        params = [f"%{value}%"] * len(cols)
        sql = f"SELECT * FROM `{table}` WHERE {conditions} LIMIT {int(limit)}"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._html(INDEX_HTML)
            elif parsed.path == "/api/tables":
                self._json({"database": DB.database, "tables": DB.list_tables()})
            elif parsed.path == "/api/columns":
                table = qs.get("table", [""])[0]
                self._json({"table": table, "columns": DB.list_columns(table)})
            elif parsed.path == "/api/columns_all":
                self._json({"database": DB.database, "columns": DB.list_all_columns()})
            elif parsed.path == "/api/search_value":
                table = qs.get("table", [""])[0]
                value = qs.get("q", [""])[0]
                limit = int(qs.get("limit", ["1000"])[0])
                rows = DB.search_value(table, value, limit)
                self._json({"table": table, "rows": rows})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


INDEX_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DB Browser</title>
<style>
  :root { --border:#d5d9dd; --accent:#1a73c7; --bg-tree:#f3f5f7; --hover:#e8f0fb; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: Segoe UI, Arial, sans-serif; font-size:13px; color:#1c1f24; }
  #app { display:flex; height:100vh; }

  /* --- Left tree panel --- */
  #tree-panel { width:300px; min-width:200px; border-right:1px solid var(--border); background:var(--bg-tree); display:flex; flex-direction:column; }
  #tree-toolbar { display:flex; gap:6px; padding:6px; border-bottom:1px solid var(--border); }
  #tree-filter { flex:1; padding:4px 6px; border:1px solid var(--border); border-radius:3px; font-size:12px; }
  .icon-btn { border:1px solid var(--border); background:#fff; border-radius:3px; padding:3px 7px; cursor:pointer; font-size:12px; }
  .icon-btn:hover { background:var(--hover); }
  #tree { overflow-y:auto; flex:1; padding:4px 0; }
  .node-row { display:flex; align-items:center; padding:3px 6px; cursor:pointer; white-space:nowrap; border-radius:3px; }
  .node-row:hover { background:var(--hover); }
  .node-row.active { background:#d6e6fb; }
  .caret { width:14px; display:inline-block; text-align:center; color:#666; font-size:10px; user-select:none; }
  .node-icon { margin-right:5px; }
  .row-count { margin-left:6px; color:#8a8f96; font-size:11px; }
  .col-list { margin-left:20px; }
  .col-row { display:flex; padding:2px 6px 2px 20px; color:#444; font-size:12px; }
  .col-row .col-key { color:#b8860b; margin-left:4px; font-weight:600; }
  .hidden { display:none !important; }
  .match { background:#fff2a8; }

  /* --- Right tab panel --- */
  #main-panel { flex:1; display:flex; flex-direction:column; min-width:0; }
  #tab-bar { display:flex; border-bottom:1px solid var(--border); background:#fafbfc; overflow-x:auto; }
  .tab { display:flex; align-items:center; gap:6px; padding:8px 12px; border-right:1px solid var(--border); cursor:pointer; white-space:nowrap; }
  .tab.active { background:#fff; border-bottom:2px solid var(--accent); font-weight:600; }
  .tab .close { color:#999; }
  .tab .close:hover { color:#c00; }
  #tab-content { flex:1; overflow:auto; padding:16px; }

  table.grid { border-collapse: collapse; width:100%; font-size:13px; }
  table.grid th, table.grid td { border:1px solid var(--border); padding:5px 8px; text-align:left; }
  table.grid th { background:#f3f5f7; position:sticky; top:0; }
  table.grid tr:hover td { background:#f7fafd; }
  .pk-badge { background:#1a73c7; color:#fff; border-radius:3px; font-size:10px; padding:1px 5px; margin-left:6px; }

  h2 { margin-top:0; }
  input.search-box { padding:5px 8px; border:1px solid var(--border); border-radius:3px; width:280px; font-size:13px; }
  button.action { padding:5px 12px; border:1px solid var(--accent); background:var(--accent); color:#fff; border-radius:3px; cursor:pointer; font-size:13px; }
  button.action:hover { opacity:.9; }
  .muted { color:#8a8f96; font-size:12px; }
  .progress { font-size:12px; color:#666; margin:8px 0; }
</style>
</head>
<body>
<div id="app">
  <div id="tree-panel">
    <div id="tree-toolbar">
      <input id="tree-filter" placeholder="Filter tables / columns...">
      <button class="icon-btn" id="expand-all-btn" title="Expand all">Expand All</button>
      <button class="icon-btn" id="collapse-all-btn" title="Collapse all">Collapse All</button>
    </div>
    <div id="tree"></div>
  </div>
  <div id="main-panel">
    <div id="tab-bar"></div>
    <div id="tab-content"></div>
  </div>
</div>

<script>
const state = {
  database: "",
  tables: [],            // [{name, row_count}]
  columnsCache: {},      // table -> [{name,type,nullable,col_key}]
  expanded: new Set(),
  tabs: [],              // [{id, title, type, table, closable}]
  activeTab: null,
  filterText: "",
};

async function api(path) {
  const res = await fetch(path);
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  (children || []).forEach(c => e.appendChild(c));
  return e;
}

/* ---------------- Tree ---------------- */

function renderTree() {
  const root = document.getElementById("tree");
  root.innerHTML = "";

  const rootRow = el("div", { class: "node-row" + (state.activeTab === "overview" ? " active" : ""), onclick: () => openOverviewTab() }, [
    el("span", { class: "caret", text: "" }),
    el("span", { class: "node-icon", text: "🐍" }),
    el("span", { text: state.database + " (" + state.tables.length + " tables)" }),
  ]);
  root.appendChild(rootRow);

  const filter = state.filterText.trim().toLowerCase();

  state.tables.forEach(t => {
    const cols = state.columnsCache[t.name];
    const colMatches = filter && cols ? cols.filter(c => c.name.toLowerCase().includes(filter)) : [];
    const tableMatches = !filter || t.name.toLowerCase().includes(filter) || colMatches.length > 0;
    if (!tableMatches) return;

    const isExpanded = state.expanded.has(t.name) || colMatches.length > 0;
    const row = el("div", {
      class: "node-row" + (state.activeTab === t.name ? " active" : ""),
      onclick: () => { toggleExpand(t.name); openTableTab(t.name); },
    }, [
      el("span", { class: "caret", text: isExpanded ? "▾" : "▸" }),
      el("span", { class: "node-icon", text: "🗃️" }),
      el("span", { text: t.name, class: filter && t.name.toLowerCase().includes(filter) ? "match" : "" }),
      el("span", { class: "row-count", text: t.row_count != null ? "~" + t.row_count + " rows" : "" }),
    ]);
    root.appendChild(row);

    if (isExpanded) {
      const wrap = el("div", { class: "col-list" });
      if (!cols) {
        wrap.appendChild(el("div", { class: "col-row muted", text: "loading..." }));
        loadColumns(t.name).then(renderTree);
      } else {
        (filter ? colMatches.length ? colMatches : cols : cols).forEach(c => {
          wrap.appendChild(el("div", { class: "col-row" }, [
            el("span", { text: c.name, class: filter && c.name.toLowerCase().includes(filter) ? "match" : "" }),
            el("span", { class: "muted", text: "  " + c.type }),
            c.col_key === "PRI" ? el("span", { class: "col-key", text: "PK" }) : el("span", {}),
          ]));
        });
      }
      root.appendChild(wrap);
    }
  });
}

function toggleExpand(tableName) {
  if (state.expanded.has(tableName)) state.expanded.delete(tableName);
  else state.expanded.add(tableName);
}

async function loadColumns(tableName) {
  if (state.columnsCache[tableName]) return state.columnsCache[tableName];
  const data = await api("/api/columns?table=" + encodeURIComponent(tableName));
  state.columnsCache[tableName] = data.columns;
  return data.columns;
}

document.getElementById("expand-all-btn").addEventListener("click", async () => {
  const data = await api("/api/columns_all");
  Object.entries(data.columns).forEach(([table, cols]) => { state.columnsCache[table] = cols; });
  state.tables.forEach(t => state.expanded.add(t.name));
  renderTree();
});

document.getElementById("collapse-all-btn").addEventListener("click", () => {
  state.expanded.clear();
  renderTree();
});

document.getElementById("tree-filter").addEventListener("input", (e) => {
  state.filterText = e.target.value;
  renderTree();
});

/* ---------------- Tabs ---------------- */

function openOverviewTab() {
  openTab({ id: "overview", title: state.database, type: "overview", closable: false });
}

function openTableTab(tableName) {
  openTab({ id: tableName, title: tableName, type: "table", table: tableName, closable: true });
}

function openSearchTab() {
  openTab({ id: "search", title: "Search Data", type: "search", closable: false });
}

function openTab(tab) {
  if (!state.tabs.find(t => t.id === tab.id)) state.tabs.push(tab);
  state.activeTab = tab.id;
  renderAll();
}

function closeTab(id) {
  state.tabs = state.tabs.filter(t => t.id !== id);
  if (state.activeTab === id) {
    state.activeTab = state.tabs.length ? state.tabs[state.tabs.length - 1].id : null;
  }
  renderAll();
}

function renderTabBar() {
  const bar = document.getElementById("tab-bar");
  bar.innerHTML = "";
  state.tabs.forEach(t => {
    const children = [el("span", { text: t.title })];
    if (t.closable) {
      children.push(el("span", { class: "close", text: "×", onclick: (e) => { e.stopPropagation(); closeTab(t.id); } }));
    }
    bar.appendChild(el("div", {
      class: "tab" + (state.activeTab === t.id ? " active" : ""),
      onclick: () => { state.activeTab = t.id; renderAll(); },
    }, children));
  });
}

async function renderTabContent() {
  const content = document.getElementById("tab-content");
  content.innerHTML = "";
  const tab = state.tabs.find(t => t.id === state.activeTab);
  if (!tab) {
    content.appendChild(el("div", { class: "muted", text: "Click a table on the left, or the database name for an overview." }));
    return;
  }
  if (tab.type === "overview") return renderOverviewContent(content);
  if (tab.type === "table") return renderTableContent(content, tab.table);
  if (tab.type === "search") return renderSearchContent(content);
}

function renderOverviewContent(content) {
  content.appendChild(el("h2", { text: state.database }));
  content.appendChild(el("div", { class: "muted", text: state.tables.length + " tables. Click a row to open its schema." }));
  const filterInput = el("input", { class: "search-box", placeholder: "Filter tables..." });
  content.appendChild(filterInput);
  const btnWrap = el("span", {}, [el("button", { class: "action", text: "Search Data (all tables)", onclick: openSearchTab })]);
  btnWrap.style.marginLeft = "10px";
  content.appendChild(btnWrap);

  const tableEl = el("table", { class: "grid" });
  const renderRows = (filter) => {
    tableEl.innerHTML = "";
    const thead = el("tr", {}, [el("th", { text: "Table" }), el("th", { text: "Approx. rows" })]);
    tableEl.appendChild(el("thead", {}, [thead]));
    const tbody = el("tbody");
    state.tables
      .filter(t => !filter || t.name.toLowerCase().includes(filter.toLowerCase()))
      .forEach(t => {
        const row = el("tr", { onclick: () => openTableTab(t.name) }, [
          el("td", { text: t.name }),
          el("td", { text: t.row_count != null ? t.row_count : "" }),
        ]);
        row.style.cursor = "pointer";
        tbody.appendChild(row);
      });
    tableEl.appendChild(tbody);
  };
  renderRows("");
  filterInput.addEventListener("input", (e) => renderRows(e.target.value));
  content.appendChild(document.createElement("br"));
  content.appendChild(tableEl);
}

async function renderTableContent(content, tableName) {
  content.appendChild(el("h2", { text: tableName }));
  const cols = await loadColumns(tableName);
  const gridWrap = el("table", { class: "grid" });
  gridWrap.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: "Column" }), el("th", { text: "Type" }), el("th", { text: "Nullable" }), el("th", { text: "Key" }),
  ])]));
  const tbody = el("tbody");
  cols.forEach(c => {
    tbody.appendChild(el("tr", {}, [
      el("td", { text: c.name }),
      el("td", { text: c.type }),
      el("td", { text: c.nullable }),
      el("td", { text: c.col_key || "" }),
    ]));
  });
  gridWrap.appendChild(tbody);
  content.appendChild(gridWrap);

  content.appendChild(el("h2", { text: "Search this table's data" }));
  const input = el("input", { class: "search-box", placeholder: "Value to find..." });
  const btn = el("button", { class: "action", text: "Search" });
  const resultsDiv = el("div");
  btn.addEventListener("click", async () => {
    resultsDiv.innerHTML = "Searching...";
    try {
      const data = await api("/api/search_value?table=" + encodeURIComponent(tableName) + "&q=" + encodeURIComponent(input.value));
      renderRowsAsGrid(resultsDiv, data.rows);
    } catch (e) {
      resultsDiv.textContent = "Error: " + e.message;
    }
  });
  content.appendChild(el("div", {}, [input, btn]));
  content.appendChild(resultsDiv);
}

function renderRowsAsGrid(container, rows) {
  container.innerHTML = "";
  if (!rows || !rows.length) { container.appendChild(el("div", { class: "muted", text: "No matches." })); return; }
  const cols = Object.keys(rows[0]);
  const table = el("table", { class: "grid" });
  table.appendChild(el("thead", {}, [el("tr", {}, cols.map(c => el("th", { text: c })))]));
  const tbody = el("tbody");
  rows.forEach(r => tbody.appendChild(el("tr", {}, cols.map(c => el("td", { text: String(r[c]) })))));
  table.appendChild(tbody);
  container.appendChild(table);
}

function renderSearchContent(content) {
  content.appendChild(el("h2", { text: "Search Data (all tables)" }));
  content.appendChild(el("div", { class: "muted", text: "Scans every table's columns for a matching value, one table at a time. Can be slow on large databases." }));
  const input = el("input", { class: "search-box", placeholder: "Value to find..." });
  const btn = el("button", { class: "action", text: "Search all tables" });
  const progress = el("div", { class: "progress" });
  const resultsDiv = el("div");
  content.appendChild(el("div", {}, [input, btn]));
  content.appendChild(progress);
  content.appendChild(resultsDiv);

  btn.addEventListener("click", async () => {
    resultsDiv.innerHTML = "";
    let matched = 0;
    for (let i = 0; i < state.tables.length; i++) {
      const t = state.tables[i].name;
      progress.textContent = `Scanning ${i + 1}/${state.tables.length}: ${t}`;
      try {
        const data = await api("/api/search_value?table=" + encodeURIComponent(t) + "&q=" + encodeURIComponent(input.value) + "&limit=1000");
        if (data.rows && data.rows.length) {
          matched++;
          resultsDiv.appendChild(el("h3", { text: t + " (" + data.rows.length + " match)" }));
          const grid = el("div");
          renderRowsAsGrid(grid, data.rows);
          resultsDiv.appendChild(grid);
        }
      } catch (e) { /* skip tables that error */ }
    }
    progress.textContent = `Done. ${matched} table(s) had matches.`;
  });
}

function renderAll() {
  renderTree();
  renderTabBar();
  renderTabContent();
}

/* ---------------- Boot ---------------- */

async function boot() {
  const data = await api("/api/tables");
  state.database = data.database;
  state.tables = data.tables;
  openOverviewTab();
}

boot();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="DBeaver-style local web UI for a MySQL DoseWatch database.")
    parser.add_argument("--host", required=True, help="MySQL host")
    parser.add_argument("--port", type=int, default=3306, help="MySQL port (default 3306)")
    parser.add_argument("--user", required=True, help="MySQL user")
    parser.add_argument("--password", required=True, help="MySQL password")
    parser.add_argument("--database", default="serphydose", help="Database/schema name (default serphydose)")
    parser.add_argument("--web-port", type=int, default=8765, help="Local web UI port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    args = parser.parse_args()

    global DB
    try:
        DB = MySQLBrowser(args.host, args.port, args.user, args.password, args.database)
    except ImportError:
        print("Missing dependency. Run: pip install pymysql --break-system-packages")
        return

    server = ThreadingHTTPServer(("127.0.0.1", args.web_port), Handler)
    url = f"http://127.0.0.1:{args.web_port}"
    print(f"Serving DB browser at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
