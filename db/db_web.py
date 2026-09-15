#!/usr/bin/env python3
"""
db_web.py - a modern browser-based UI for the DOsewatch DB connection
manager, built on top of db_manager.py.

Setup (one time):
    pip install fastapi uvicorn pymysql

Run:
    python db_web.py
or double-click run_db_web.bat

This starts a local web server on http://127.0.0.1:8765 and opens it in your
default browser automatically. Everything stays on your machine - nothing
here talks to the internet except your browser talking to localhost.

Network note:
- This DB lives on GE's internal network, so this only works when it runs
  from a machine that actually has network access to it (e.g. your laptop
  on VPN/corporate network) - not from an external cloud sandbox.

Ask Claude tab - how it actually works:
- This has no separate paid Anthropic API key. Instead it shells out to the
  Claude Code CLI (`claude -p "..."`) already installed on this machine,
  which uses your existing Claude login - so the "Ask Claude" tab gets a
  real answer synchronously, in the same click, with no back-and-forth
  through any chat window.
- On each question: this script searches the schema for tables/columns
  matching your question's keywords, looks up real foreign keys (and, if
  none are declared, guesses relationships from column naming), then asks
  Claude Code to write one SQL query given that context. The SQL then runs
  immediately against the real database and the results come straight back
  to the tab, alongside the SQL and a short explanation.
- Fallback: if Claude Code isn't on PATH, times out, or its output can't be
  parsed, this instead writes ask_pending.json (question + matched schema +
  relationships) and tells you to ask Claude in your Cowork/Claude Desktop
  chat to check it - that chat writes ask_response.json back, and the tab's
  "Check for Claude's answer" button loads it.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

import db_manager as dbm

BASE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765  # preferred - overwritten with a free port at startup if taken
ASK_PENDING_FILE = BASE_DIR / "ask_pending.json"
ASK_RESPONSE_FILE = BASE_DIR / "ask_response.json"
CLAUDE_CODE_TIMEOUT = 90

STOPWORDS = {
    "find", "me", "the", "a", "an", "table", "tables", "this", "that", "these",
    "those", "from", "with", "related", "relate", "relates", "relationship",
    "relationships", "between", "show", "list", "give", "please", "would",
    "could", "can", "you", "and", "for", "to", "of", "is", "are", "in", "on",
    "by", "all", "any", "some", "get", "want", "need", "know", "which", "who",
    "whom", "whose", "what", "having", "have", "has", "had", "does", "did",
    "do", "will", "shall", "should", "there", "their", "them", "specific",
    "particular", "each", "every",
}

MAX_MATCHED_TABLES = 25  # cap how many tables go into the handoff file

app = FastAPI(title="DOsewatch DB Manager")


class ConnectionIn(BaseModel):
    name: str
    host: str
    port: int = 3306
    user: str
    password: str = ""
    database: str = ""


class QueryIn(BaseModel):
    name: str
    database: Optional[str] = None
    sql: str


class DropTableIn(BaseModel):
    database: Optional[str] = None
    table: str


class DropColumnIn(BaseModel):
    database: Optional[str] = None
    table: str
    column: str


class DeleteRowIn(BaseModel):
    database: Optional[str] = None
    table: str
    row: dict


class AskIn(BaseModel):
    name: str
    database: Optional[str] = None
    question: str


def extract_keywords(question, limit=8):
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question.lower())
    seen = []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.append(w)
        if len(seen) >= limit:
            break
    return seen


def rank_table_matches(keyword, matches):
    """matches: list of {table, column, type} from search_schema for one
    keyword. Returns table names ordered so an exact table-name match (e.g.
    keyword 'patient' -> table 'patient') beats a whole-word match (keyword
    as an underscore-delimited segment, e.g. 'ct_patient') which beats any
    other substring match (e.g. keyword 'study' matching 'tracking_ct_study_
    alert' just because it contains the letters). Without this, a common
    word like 'study' can match 100+ tables in a study-centric schema and
    bury the tables that actually matter."""
    keyword_l = keyword.lower()
    exact, whole_word, other = [], [], []
    seen = set()
    for m in matches:
        t = m["table"]
        if t in seen:
            continue
        seen.add(t)
        t_l = t.lower()
        if t_l == keyword_l:
            exact.append(t)
        elif keyword_l in t_l.split("_"):
            whole_word.append(t)
        else:
            other.append(t)
    return exact + whole_word + other


def find_claude_cli():
    """Locate the Claude Code CLI executable. Deliberately prefers the
    .cmd shim over the bare 'claude'/.ps1 shims npm also installs - some
    shells (PowerShell) resolve a bare 'claude' to claude.ps1 first, which
    then fails outright if script execution is disabled by policy. .cmd
    files aren't subject to that policy at all, so always target them
    explicitly. Also checks common Windows npm global-install locations
    directly, since shutil.which() only sees PATH as inherited by *this*
    Python process - if Claude Code was installed (or PATH updated) after
    this server was last started, that change won't be visible here even
    though a fresh terminal would see it fine."""
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "npm" / "claude.cmd",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    found = shutil.which("claude.cmd") or shutil.which("claude")
    if found:
        return found

    candidates += [
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def call_claude_code(prompt, timeout=CLAUDE_CODE_TIMEOUT, cwd=None):
    """Invoke the Claude Code CLI in one-shot print mode. Returns (text, error)."""
    claude_bin = find_claude_cli()
    if not claude_bin:
        return None, (
            "Claude Code CLI ('claude') not found. If you installed it after this "
            "server was last started, PATH changes won't reach an already-running "
            "process - fully close this server (and its terminal window) and "
            "double-click run_db_web.bat again to relaunch it fresh."
        )
    try:
        # Pass the prompt via stdin, not as a CLI argument: the schema
        # context embedded in it can be tens of KB, and invoking a .cmd
        # shim goes through cmd.exe, which caps command lines at ~8191
        # characters (WinError 206, "filename or extension is too long",
        # is Windows' way of saying the assembled command line overflowed
        # that limit). Claude Code's -p/--print mode reads the prompt from
        # stdin when no prompt argument is given, so this sidesteps it.
        proc = subprocess.run(
            [claude_bin, "-p"],
            input=prompt,
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return None, f"Claude Code took longer than {timeout}s to respond."
    except Exception as e:
        return None, f"Could not run Claude Code ({claude_bin}): {e}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "Claude Code exited with an error.").strip()
    return proc.stdout.strip(), None


def extract_json_object(text):
    """Claude Code sometimes wraps JSON in prose or markdown fences despite
    instructions not to - pull out the first {...} block and parse it."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def build_sql_prompt(question, database, matched_schema, relationships):
    schema_text = json.dumps(matched_schema, indent=2)
    rels_text = json.dumps(relationships, indent=2)
    return (
        f"You are a SQL assistant for a MariaDB database named '{database}'.\n"
        f"Question: {question}\n\n"
        f"Relevant tables and columns (JSON, table -> list of "
        f"{{column, type, nullable, key}}):\n{schema_text}\n\n"
        f"Known table relationships (JSON list of {{table, column, "
        f"references_table, references_column}} for confirmed foreign keys, "
        f"or {{table, column, likely_references_table}} for naming-based "
        f"guesses):\n{rels_text}\n\n"
        "Write ONE single read-only SELECT SQL query (MariaDB/MySQL syntax) "
        "that best answers the question, using only the tables/columns "
        "listed above. Never write INSERT/UPDATE/DELETE/DROP/ALTER.\n"
        "Respond with ONLY a JSON object, no markdown fences, no extra "
        'commentary, in exactly this shape: {"sql": "...", "explanation": "..."}'
    )


def guess_relationships(schema_subset):
    """schema_subset: dict table -> list of column dicts (from fetch_schema).
    Flags column names that look like foreign keys referencing another table
    in the subset by naming convention (e.g. PATIENT_ID likely refers to a
    PATIENT table). This is a fallback heuristic for schemas that don't use
    real FOREIGN KEY constraints - not a guarantee."""
    guesses = []
    table_names_lower = {t.lower(): t for t in schema_subset}
    for table, cols in schema_subset.items():
        for c in cols:
            col_lower = c["column"].lower()
            for other_lower, other_actual in table_names_lower.items():
                if other_actual == table:
                    continue
                singular = other_lower[:-1] if other_lower.endswith("s") else other_lower
                if col_lower in (f"{other_lower}_id", f"{singular}_id", f"{other_lower}id", f"{singular}id"):
                    guesses.append({
                        "table": table,
                        "column": c["column"],
                        "likely_references_table": other_actual,
                    })
    return guesses


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/connections")
def list_connections():
    store = dbm.load_store()
    return [
        {"name": name, "host": c["host"], "port": c["port"], "user": c["user"], "database": c.get("database", "")}
        for name, c in store.items()
    ]


@app.post("/api/connections")
def add_connection(conn: ConnectionIn):
    dbm.add_connection(conn.name, conn.host, conn.port, conn.user, conn.password, conn.database)
    return {"ok": True}


@app.delete("/api/connections/{name}")
def remove_connection(name: str):
    ok = dbm.remove_connection(name)
    if not ok:
        raise HTTPException(404, f"No connection named '{name}'")
    return {"ok": True}


@app.get("/api/test/{name}")
def test_connection(name: str):
    ok, message = dbm.test_connection(name)
    return {"ok": ok, "message": message}


@app.get("/api/databases/{name}")
def databases(name: str):
    dbs, err = dbm.fetch_databases(name)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "databases": dbs}


@app.get("/api/schema/{name}")
def schema(name: str, database: Optional[str] = None):
    sch, actual_db, err = dbm.fetch_schema(name, database)
    if err == "NO_DATABASE_SELECTED":
        dbs, _ = dbm.fetch_databases(name)
        return {"ok": False, "error": "no_database_selected", "databases": dbs or []}
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "database": actual_db, "schema": sch}


@app.post("/api/schema/{name}/drop_table")
def drop_table_endpoint(name: str, payload: DropTableIn):
    """Permanently drops an entire table - structure and all rows,
    irreversible. Same trust model as drop_column_endpoint: the frontend
    gates this behind a simple double confirmation."""
    ok, err = dbm.drop_table(name, payload.table, payload.database)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True}


@app.post("/api/schema/{name}/drop_column")
def drop_column_endpoint(name: str, payload: DropColumnIn):
    """Permanently drops one column - irreversible. The frontend asks the
    user to double confirm before this is ever called; this endpoint itself
    does not double-check that (it trusts the caller), so don't expose it
    without that confirmation step in front of it."""
    ok, err = dbm.drop_column(name, payload.table, payload.column, payload.database)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True}


@app.post("/api/delete_row/{name}")
def delete_row_endpoint(name: str, payload: DeleteRowIn):
    """Deletes exactly one row matching every column in payload.row (see
    dbm.delete_row) - irreversible. The frontend confirms with the user
    before calling this."""
    affected, err = dbm.delete_row(name, payload.table, payload.row, payload.database)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "affected": affected}


@app.get("/api/search/{name}")
def search(name: str, q: str = "", database: Optional[str] = None):
    matches, actual_db, err = dbm.search_schema(name, q, database)
    if err == "NO_DATABASE_SELECTED":
        dbs, _ = dbm.fetch_databases(name)
        return {"ok": False, "error": "no_database_selected", "databases": dbs or []}
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "database": actual_db, "matches": matches}


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@app.post("/api/query")
def query(q: QueryIn):
    rows, err = dbm.run_query(q.name, q.sql, q.database)
    if err:
        return {"ok": False, "error": err}
    columns = list(rows[0].keys()) if rows else []
    safe_rows = [{k: _json_safe(v) for k, v in row.items()} for row in rows]
    return {"ok": True, "columns": columns, "rows": safe_rows}


@app.post("/api/query_multi")
def query_multi(q: QueryIn):
    """Like /api/query, but runs every ;-separated statement in q.sql in
    order over one connection (a single statement works fine too - the
    Query tab always calls this one now). Stops at the first failing
    statement; results after that point simply aren't included."""
    results, err = dbm.run_query_multi(q.name, q.sql, q.database)
    if err:
        return {"ok": False, "error": err}
    safe_results = []
    for r in results:
        rows = r.get("rows")
        columns = list(rows[0].keys()) if rows else []
        safe_rows = [{k: _json_safe(v) for k, v in row.items()} for row in rows] if rows else []
        safe_results.append({
            "sql": r["sql"],
            "ok": r["ok"],
            "error": r.get("error"),
            "row_count": r.get("row_count"),
            "columns": columns,
            "rows": safe_rows,
        })
    return {"ok": True, "results": safe_results}


@app.get("/api/search_value/{name}")
def search_value(name: str, q: str = "", database: Optional[str] = None, table: Optional[str] = None, limit: int = 1000, offset: int = 0):
    """Search actual table DATA for a value - HeidiSQL/DBeaver's 'Find text on
    server'. Distinct from /api/search, which only matches table/column NAMES.
    Unscoped (no `table`) scans every table in the database, no cap - pass
    `table` + increasing `offset` to page through one table's full results."""
    results, actual_db, err = dbm.search_data(name, q, database, table, limit, offset)
    if err == "NO_DATABASE_SELECTED":
        dbs, _ = dbm.fetch_databases(name)
        return {"ok": False, "error": "no_database_selected", "databases": dbs or []}
    if err:
        return {"ok": False, "error": err}
    safe_results = [
        {
            "table": r["table"],
            "rows": [{k: _json_safe(v) for k, v in row.items()} for row in r["rows"]],
            "row_count": r["row_count"],
            "has_more": r["has_more"],
        }
        for r in results
    ]
    return {"ok": True, "database": actual_db, "results": safe_results}


@app.get("/api/history")
def history(limit: int = 50):
    hist = dbm.load_history()
    return {"ok": True, "history": list(reversed(hist))[:limit]}


@app.delete("/api/history")
def clear_history_endpoint():
    dbm.clear_history()
    return {"ok": True}


@app.post("/api/ask")
def ask(payload: AskIn):
    try:
        keywords = extract_keywords(payload.question)
        if not keywords:
            return {"ok": False, "error": "Couldn't find any meaningful keywords in that question - try naming the tables/topics you mean."}

        actual_db = payload.database
        matched_tables_ordered = []
        matched_tables_set = set()
        for kw in keywords:
            matches, db_name, err = dbm.search_schema(payload.name, kw, actual_db)
            if err == "NO_DATABASE_SELECTED":
                dbs, _ = dbm.fetch_databases(payload.name)
                return {"ok": False, "error": "no_database_selected", "databases": dbs or []}
            if err:
                return {"ok": False, "error": err}
            actual_db = db_name
            for t in rank_table_matches(kw, matches):
                if t not in matched_tables_set:
                    matched_tables_set.add(t)
                    matched_tables_ordered.append(t)

        truncated = len(matched_tables_ordered) > MAX_MATCHED_TABLES
        matched_tables_ordered = matched_tables_ordered[:MAX_MATCHED_TABLES]
        matched_tables = set(matched_tables_ordered)

        full_schema, _, err = dbm.fetch_schema(payload.name, actual_db)
        if err == "NO_DATABASE_SELECTED":
            dbs, _ = dbm.fetch_databases(payload.name)
            return {"ok": False, "error": "no_database_selected", "databases": dbs or []}
        if err:
            return {"ok": False, "error": err}
        full_schema = full_schema or {}
        matched_schema = {t: full_schema.get(t, []) for t in matched_tables_ordered}

        declared_rels, _, rel_err = dbm.fetch_relationships(payload.name, actual_db)
        declared_rels = declared_rels or []
        relevant_declared = [
            r for r in declared_rels
            if r["table"] in matched_tables or r["references_table"] in matched_tables
        ]
        guessed_rels = guess_relationships(matched_schema)

        base_result = {
            "database": actual_db,
            "matched_tables": matched_tables_ordered,
            "matched_tables_truncated": truncated,
            "declared_relationships": relevant_declared,
            "guessed_relationships": guessed_rels,
        }

        # --- Try the synchronous path: Claude Code CLI, in the same request ---
        prompt = build_sql_prompt(payload.question, actual_db, matched_schema, relevant_declared + guessed_rels)
        claude_text, claude_err = call_claude_code(prompt, cwd=str(BASE_DIR))

        if not claude_err:
            try:
                parsed = extract_json_object(claude_text)
                sql = parsed["sql"]
                explanation = parsed.get("explanation", "")
            except Exception as e:
                claude_err = f"Got a response from Claude Code but couldn't parse it as JSON: {e}"

        if not claude_err:
            rows, query_err = dbm.run_query(payload.name, sql, actual_db)
            if query_err:
                return {
                    **base_result,
                    "ok": True,
                    "mode": "sync",
                    "sql": sql,
                    "explanation": explanation,
                    "query_error": query_err,
                }
            columns = list(rows[0].keys()) if rows else []
            safe_rows = [{k: _json_safe(v) for k, v in row.items()} for row in rows]
            return {
                **base_result,
                "ok": True,
                "mode": "sync",
                "sql": sql,
                "explanation": explanation,
                "columns": columns,
                "rows": safe_rows,
            }

        # --- Fallback: Claude Code unavailable/failed - hand off via file ---
        request_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connection": payload.name,
            "database": actual_db,
            "question": payload.question,
            "keywords": keywords,
            "matched_schema": matched_schema,
            "declared_relationships": relevant_declared,
            "guessed_relationships": guessed_rels,
        }
        ASK_PENDING_FILE.write_text(json.dumps(request_payload, indent=2))
        if ASK_RESPONSE_FILE.exists():
            ASK_RESPONSE_FILE.unlink()

        return {
            **base_result,
            "ok": True,
            "mode": "handoff",
            "claude_code_error": claude_err,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/api/ask/response")
def ask_response():
    try:
        if not ASK_RESPONSE_FILE.exists():
            return {"ok": False, "pending": True}
        data = json.loads(ASK_RESPONSE_FILE.read_text())
        return {"ok": True, **data}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def find_free_port(preferred):
    """Try the preferred port first; if something else already owns it,
    ask the OS for any free port instead so the browser never ends up
    showing that other service's page by mistake."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    PORT = find_free_port(PORT)
    print(f"DOsewatch DB Manager: http://{HOST}:{PORT}")
    threading.Timer(1.0, open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
