#!/usr/bin/env python3
"""
db_manager.py - save and reuse MariaDB/MySQL connection profiles (host, user,
password, database) for the DOsewatch DB project.

There are three ways to use this:
  1. Double-click run_db_web.bat for a modern browser-based UI (recommended).
  2. Double-click run_db_gui.bat for a simple desktop window.
  3. Double-click run_db_manager.bat / run db_manager.sh for a text menu.
  4. Run python db_manager.py <command> directly from a terminal.

Setup (one time):
    pip install pymysql
    (Windows: just "pip install pymysql" - no --break-system-packages needed,
    that flag is Linux-only.)

Command-line use:
    python db_manager.py add --name dosewatch --host 10.x.x.x --port 3306 --user myuser --database mydb
    python db_manager.py list
    python db_manager.py test --name dosewatch
    python db_manager.py databases --name dosewatch
    python db_manager.py schema --name dosewatch --database mydb
    python db_manager.py search --name dosewatch --database mydb --q patient
    python db_manager.py search-data --name dosewatch --database mydb --value 12345
    python db_manager.py search-data --name dosewatch --database mydb --table patient --value smith
    python db_manager.py query --name dosewatch --database mydb --sql "SELECT * FROM table LIMIT 10"
    python db_manager.py query --name dosewatch --sql "SELECT ..." --csv out.csv
    python db_manager.py query --name dosewatch --database mydb --sql-file script.sql
    (--sql-file / a --sql with multiple ;-separated statements both run in
    order over one connection, stopping at the first failing statement)
    python db_manager.py history --limit 20
    python db_manager.py remove --name dosewatch

One saved connection (host/user/password) works for EVERY database on that
server - you only need to add it once. Pass --database (or type it when
prompted) on "schema"/"search"/"query" to pick which database to target; you
don't need a separate saved connection per database.

Storage:
- Connections (including passwords, in plain text, as requested for
  quick auto-login next time) are saved to connections.json next to this
  script. Since that's plain text, don't put anything here you wouldn't
  want visible to anyone with access to this folder.

Network note:
- This DB lives on GE's internal network, so this only works when it runs
  from a machine that actually has network access to it (e.g. your laptop
  on VPN/corporate network) - not from an external cloud sandbox.
"""
import argparse
import csv
import getpass
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STORE_FILE = BASE_DIR / "connections.json"
HISTORY_FILE = BASE_DIR / "query_history.json"
MAX_HISTORY = 200
DEFAULT_PASSWORD = "Stra67000"  # used when the password prompt is left blank


# ---------------------------------------------------------------------------
# Core, reusable logic (no printing) - shared by the CLI, GUI, and web UI.
# ---------------------------------------------------------------------------

def load_store():
    if not STORE_FILE.exists():
        return {}
    return json.loads(STORE_FILE.read_text())


def save_store(store):
    STORE_FILE.write_text(json.dumps(store, indent=2))


def add_connection(name, host, port, user, password, database=""):
    store = load_store()
    store[name] = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password or DEFAULT_PASSWORD,
        "database": database or "",
    }
    save_store(store)


def remove_connection(name):
    store = load_store()
    if name not in store:
        return False
    del store[name]
    save_store(store)
    return True


def get_connection_info(name):
    return load_store().get(name)


# ---------------------------------------------------------------------------
# Query history - every query run through run_query() (CLI, web Query tab, or
# the Ask Claude tab's synchronous SQL execution) is logged here so it can be
# reviewed and reloaded later.
# ---------------------------------------------------------------------------

def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))


def add_history_entry(connection, database, sql, ok, row_count=None, error=None):
    from datetime import datetime, timezone
    history = load_history()
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connection": connection,
        "database": database or "",
        "sql": sql,
        "ok": bool(ok),
        "row_count": row_count,
        "error": error,
    })
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    save_history(history)


def clear_history():
    save_history([])


def open_connection(name, use_database=True, database_override=None):
    """Returns (conn, error_message). conn is None if the connection failed."""
    c = get_connection_info(name)
    if c is None:
        return None, f"No connection named '{name}'."
    import pymysql
    kwargs = dict(
        host=c["host"],
        port=int(c["port"]),
        user=c["user"],
        password=c["password"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )
    db = database_override or (c.get("database") if use_database else None)
    if db:
        kwargs["database"] = db
    try:
        return pymysql.connect(**kwargs), None
    except Exception as e:
        return None, str(e)


def test_connection(name):
    """Returns (ok: bool, message: str)."""
    conn, err = open_connection(name)
    if err:
        return False, err
    conn.close()
    return True, "Connection OK."


def fetch_databases(name):
    """Returns (list_of_db_names, error)."""
    conn, err = open_connection(name, use_database=False)
    if err:
        return None, err
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
        return [list(r.values())[0] for r in rows], None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def fetch_schema(name, database=None):
    """Returns (schema_dict, actual_database_name, error). schema_dict maps
    table name -> list of {column, type, nullable, key}. If no database is
    selected/available, error is the literal string 'NO_DATABASE_SELECTED'."""
    conn, err = open_connection(name, database_override=database)
    if err:
        return None, None, err
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            actual_db = cur.fetchone()["db"]
            if not actual_db:
                return None, None, "NO_DATABASE_SELECTED"
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY "
                "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (actual_db,),
            )
            rows = cur.fetchall()
        schema = {}
        for r in rows:
            schema.setdefault(r["TABLE_NAME"], []).append({
                "column": r["COLUMN_NAME"],
                "type": r["DATA_TYPE"],
                "nullable": r["IS_NULLABLE"],
                "key": r["COLUMN_KEY"],
            })
        return schema, actual_db, None
    except Exception as e:
        return None, None, str(e)
    finally:
        conn.close()


def search_schema(name, q, database=None):
    """Search table and column names for a keyword (case-insensitive
    substring match) - e.g. searching 'patient' finds a PATIENTS table and
    any PATIENT_ID column on other tables too. Returns
    (matches, actual_database_name, error) where matches is a list of
    {table, column, type} dicts. If no database is selected/available, error
    is the literal string 'NO_DATABASE_SELECTED'."""
    conn, err = open_connection(name, database_override=database)
    if err:
        return None, None, err
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            actual_db = cur.fetchone()["db"]
            if not actual_db:
                return None, None, "NO_DATABASE_SELECTED"
            like = f"%{q}%"
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND (TABLE_NAME LIKE %s OR COLUMN_NAME LIKE %s) "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (actual_db, like, like),
            )
            rows = cur.fetchall()
        matches = [{"table": r["TABLE_NAME"], "column": r["COLUMN_NAME"], "type": r["DATA_TYPE"]} for r in rows]
        return matches, actual_db, None
    except Exception as e:
        return None, None, str(e)
    finally:
        conn.close()


def fetch_relationships(name, database=None):
    """Returns (relationships, actual_database_name, error). relationships is
    a list of {table, column, references_table, references_column} - the
    declared FOREIGN KEY links between tables (e.g. STUDY.PATIENT_ID ->
    PATIENT.ID). This answers "how are X and Y related" directly from the
    database's own metadata, no guessing needed - as long as real FK
    constraints exist (some legacy schemas only imply relationships by
    naming convention, in which case this comes back empty)."""
    conn, err = open_connection(name, database_override=database)
    if err:
        return None, None, err
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            actual_db = cur.fetchone()["db"]
            if not actual_db:
                return None, None, "NO_DATABASE_SELECTED"
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
                "ORDER BY TABLE_NAME",
                (actual_db,),
            )
            rows = cur.fetchall()
        rels = [
            {
                "table": r["TABLE_NAME"],
                "column": r["COLUMN_NAME"],
                "references_table": r["REFERENCED_TABLE_NAME"],
                "references_column": r["REFERENCED_COLUMN_NAME"],
            }
            for r in rows
        ]
        return rels, actual_db, None
    except Exception as e:
        return None, None, str(e)
    finally:
        conn.close()


def run_query(name, sql, database=None):
    """Returns (rows_list_of_dicts, error). Every call - success or failure -
    is logged to query_history.json (see add_history_entry) so the CLI, the
    web Query tab, and the Ask Claude tab's synchronous SQL execution all get
    a shared, persistent history for free."""
    conn, err = open_connection(name, database_override=database)
    if err:
        add_history_entry(name, database, sql, ok=False, error=err)
        return None, err
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        add_history_entry(name, database, sql, ok=True, row_count=len(rows))
        return rows, None
    except Exception as e:
        add_history_entry(name, database, sql, ok=False, error=str(e))
        return None, str(e)
    finally:
        conn.close()


def search_data(name, value, database=None, table=None, limit=1000, offset=0):
    """Grep a value across actual table DATA (not just names) - HeidiSQL/
    DBeaver call this 'Find text on server'. Returns
    (results, actual_database_name, error) where results is a list of
    {table, rows, row_count, has_more}. If `table` is given, only that table
    is scanned (with `limit`/`offset` paging through it - has_more tells the
    caller whether calling again with offset+limit would return more rows).
    If `table` is omitted, EVERY table in the database is scanned (no cap) -
    for a large database, narrow with `table` for a full, paged view of one
    table instead of a first page of everything.

    If `value` is blank, this instead just previews rows with no filter at
    all - handy for "show me what's in this table" without having to know
    something to search for first."""
    conn, err = open_connection(name, database_override=database)
    if err:
        return None, None, err
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db")
            actual_db = cur.fetchone()["db"]
            if not actual_db:
                return None, None, "NO_DATABASE_SELECTED"

            if table:
                tables = [table]
            else:
                cur.execute(
                    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                    (actual_db,),
                )
                tables = [r["TABLE_NAME"] for r in cur.fetchall()]  # every table, no cap

            # Fetch one extra row so we can tell if there's a next page
            # without a separate COUNT(*) query.
            fetch_limit = int(limit) + 1

            results = []
            for t in tables:
                if value:
                    cur.execute(
                        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                        (actual_db, t),
                    )
                    cols = [r["COLUMN_NAME"] for r in cur.fetchall()]
                    if not cols:
                        continue
                    conditions = " OR ".join(f"CAST(`{c}` AS CHAR(4000)) LIKE %s" for c in cols)
                    sql = f"SELECT * FROM `{t}` WHERE {conditions} LIMIT {fetch_limit} OFFSET {int(offset)}"
                    params = [f"%{value}%"] * len(cols)
                else:
                    # No value given - just preview the table's data as-is.
                    sql = f"SELECT * FROM `{t}` LIMIT {fetch_limit} OFFSET {int(offset)}"
                    params = []
                try:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                except Exception:
                    continue  # skip tables that error (permissions, odd types, etc.)
                if rows:
                    has_more = len(rows) > int(limit)
                    rows = rows[: int(limit)]
                    results.append({"table": t, "rows": rows, "row_count": len(rows), "has_more": has_more})
        return results, actual_db, None
    except Exception as e:
        return None, None, str(e)
    finally:
        conn.close()


def split_sql_statements(sql):
    """Split a script of one or more ;-terminated SQL statements into a list
    of individual statements, without splitting on semicolons that appear
    inside '...' / "..." / `...` literals or -- / # / /* */ comments. Good
    enough for pasted or file-loaded .sql scripts; not a full SQL parser."""
    statements = []
    current = []
    in_single = in_double = in_backtick = False
    in_line_comment = in_block_comment = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            current.append(ch)
            if ch == "*" and nxt == "/":
                current.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if in_single:
            current.append(ch)
            if ch == "'":
                if nxt == "'":
                    current.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            current.append(ch)
            if ch == '"':
                if nxt == '"':
                    current.append(nxt)
                    i += 2
                    continue
                in_double = False
            i += 1
            continue
        if in_backtick:
            current.append(ch)
            if ch == "`":
                in_backtick = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            current.append(ch)
        elif ch == '"':
            in_double = True
            current.append(ch)
        elif ch == "`":
            in_backtick = True
            current.append(ch)
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            current.append(ch)
        elif ch == "#":
            in_line_comment = True
            current.append(ch)
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            current.append(ch)
        elif ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        else:
            current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def run_query_multi(name, sql, database=None):
    """Runs one or more ;-separated SQL statements against a saved connection,
    in order, over a single connection - the point of "run multiple line
    query" support. Returns (results, error): `error` is only set for a
    connection-level failure (couldn't connect at all); a per-statement
    failure instead gets recorded in that statement's own result dict and
    execution stops there (later statements are not run, since they may
    depend on it). Each statement - success or failure - is logged to query
    history exactly like run_query(). results is a list of dicts:
    {sql, ok, rows, row_count, error}."""
    statements = split_sql_statements(sql)
    if not statements:
        return [], "No SQL statements found."

    conn, err = open_connection(name, database_override=database)
    if err:
        add_history_entry(name, database, sql, ok=False, error=err)
        return None, err

    results = []
    try:
        for stmt in statements:
            entry = {"sql": stmt}
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                    if cur.description:
                        rows = cur.fetchall()
                        entry["rows"] = rows
                        entry["row_count"] = len(rows)
                    else:
                        entry["rows"] = None
                        entry["row_count"] = cur.rowcount
                conn.commit()
                entry["ok"] = True
                add_history_entry(name, database, stmt, ok=True, row_count=entry["row_count"])
                results.append(entry)
            except Exception as e:
                entry["ok"] = False
                entry["error"] = str(e)
                entry["rows"] = None
                entry["row_count"] = None
                add_history_entry(name, database, stmt, ok=False, error=str(e))
                results.append(entry)
                break  # stop the script at the first failing statement
    finally:
        conn.close()
    return results, None


def drop_table(name, table, database=None):
    """Drops an entire table (DROP TABLE) - this permanently destroys the
    table's structure AND every row in it, with no undo. Returns
    (ok: bool, error). Logged to query history like everything else."""
    sql = f"DROP TABLE `{table}`"
    conn, err = open_connection(name, database_override=database)
    if err:
        add_history_entry(name, database, sql, ok=False, error=err)
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        add_history_entry(name, database, sql, ok=True, row_count=None)
        return True, None
    except Exception as e:
        add_history_entry(name, database, sql, ok=False, error=str(e))
        return False, str(e)
    finally:
        conn.close()


def drop_column(name, table, column, database=None):
    """Drops one column from a table (ALTER TABLE ... DROP COLUMN) - this
    permanently destroys that column's data with no undo. Returns
    (ok: bool, error). Logged to query history like everything else, so
    there's at least a record of when/what was dropped."""
    sql = f"ALTER TABLE `{table}` DROP COLUMN `{column}`"
    conn, err = open_connection(name, database_override=database)
    if err:
        add_history_entry(name, database, sql, ok=False, error=err)
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        add_history_entry(name, database, sql, ok=True, row_count=None)
        return True, None
    except Exception as e:
        add_history_entry(name, database, sql, ok=False, error=str(e))
        return False, str(e)
    finally:
        conn.close()


def delete_row(name, table, row, database=None):
    """Deletes exactly the one row whose columns all match the given `row`
    dict (as returned by search_data/fetch_schema-style row reads) - this is
    the safest generic way to target "the row I'm looking at in the UI"
    without needing to know a primary key. Adds LIMIT 1 so that even if
    duplicate rows exist, only one is removed. Returns
    (affected_row_count, error)."""
    if not row:
        return None, "No row data given to match against."
    conditions = []
    params = []
    for col, val in row.items():
        if val is None:
            conditions.append(f"`{col}` IS NULL")
        else:
            conditions.append(f"`{col}` = %s")
            params.append(val)
    where = " AND ".join(conditions)
    sql = f"DELETE FROM `{table}` WHERE {where} LIMIT 1"

    conn, err = open_connection(name, database_override=database)
    if err:
        add_history_entry(name, database, sql, ok=False, error=err)
        return None, err
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = cur.rowcount
        conn.commit()
        add_history_entry(name, database, sql, ok=True, row_count=affected)
        return affected, None
    except Exception as e:
        add_history_entry(name, database, sql, ok=False, error=str(e))
        return None, str(e)
    finally:
        conn.close()


def write_csv(rows, path):
    if not rows:
        return 0
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# CLI (thin wrappers around the core logic above - these just print things)
# ---------------------------------------------------------------------------

def cmd_add(args):
    name = args.name or args.host
    password = args.password
    if not password:
        password = getpass.getpass(f"Password for {args.user}@{args.host} [Enter for default]: ")
    add_connection(name, args.host, args.port, args.user, password, args.database)
    print(f"Saved connection '{name}'.")


def cmd_list(args):
    store = load_store()
    if not store:
        print("No connections saved.")
        return
    for name, c in store.items():
        db = c.get("database") or "(none set)"
        print(f"{name}: {c['user']}@{c['host']}:{c['port']}/{db}")


def cmd_remove(args):
    if remove_connection(args.name):
        print(f"Removed connection '{args.name}'.")
    else:
        print(f"No connection named '{args.name}'.")


def cmd_test(args):
    ok, message = test_connection(args.name)
    print(message if ok else f"Connection failed: {message}")


def cmd_databases(args):
    dbs, err = fetch_databases(args.name)
    if err:
        print(f"Connection failed: {err}")
        return
    print("Databases on this server:")
    for d in dbs:
        print(" -", d)


def cmd_schema(args):
    database = getattr(args, "database", None)
    schema, actual_db, err = fetch_schema(args.name, database)
    if err == "NO_DATABASE_SELECTED":
        dbs, derr = fetch_databases(args.name)
        print("No database selected for this connection. Available databases:")
        if dbs:
            for d in dbs:
                print(" -", d)
        print("Re-run with --database <name> (or pick one in the menu) to export its schema.")
        return
    if err:
        print(f"Connection failed: {err}")
        return
    if not schema:
        print(f"No tables found in database '{actual_db}'.")
        return
    out_path = BASE_DIR / f"schema_{args.name}_{actual_db}.json"
    out_path.write_text(json.dumps(schema, indent=2))
    print(f"Wrote schema for {len(schema)} table(s) in '{actual_db}' to {out_path}")


def cmd_search(args):
    matches, actual_db, err = search_schema(args.name, args.q, getattr(args, "database", None))
    if err == "NO_DATABASE_SELECTED":
        dbs, _ = fetch_databases(args.name)
        print("No database selected. Available databases:")
        if dbs:
            for d in dbs:
                print(" -", d)
        print("Re-run with --database <name>.")
        return
    if err:
        print(f"Connection failed: {err}")
        return
    if not matches:
        print(f"No tables/columns matching '{args.q}' in '{actual_db}'.")
        return
    print(f"Matches in '{actual_db}':")
    for m in matches:
        print(f" - {m['table']}.{m['column']} ({m['type']})")


def cmd_query(args):
    sql = args.sql
    if getattr(args, "sql_file", None):
        sql = Path(args.sql_file).read_text()
    if not sql:
        print("Provide --sql or --sql-file.")
        return

    statements = split_sql_statements(sql or "")
    if len(statements) <= 1:
        rows, err = run_query(args.name, sql, getattr(args, "database", None))
        if err:
            print(f"Query failed: {err}")
            return
        if args.csv:
            n = write_csv(rows, args.csv)
            print(f"Wrote {n} rows to {args.csv}" if n else "Query returned no rows.")
        else:
            for row in rows:
                print(row)
            print(f"\n{len(rows)} row(s).")
        return

    # Multiple statements - run each in order over one connection, print a
    # summary per statement, and stop at the first failure.
    results, err = run_query_multi(args.name, sql, getattr(args, "database", None))
    if err:
        print(f"Query failed: {err}")
        return
    for i, r in enumerate(results, 1):
        preview = r["sql"][:80].replace("\n", " ")
        if r["ok"]:
            count = r["row_count"] if r["row_count"] is not None else 0
            print(f"[{i}/{len(statements)}] OK ({count} row(s)/affected): {preview}")
            if r["rows"] and not args.csv:
                for row in r["rows"]:
                    print("   ", row)
        else:
            print(f"[{i}/{len(statements)}] FAILED: {preview}\n   -> {r['error']}")
    if args.csv:
        last_with_rows = next((r for r in reversed(results) if r["rows"]), None)
        if last_with_rows:
            n = write_csv(last_with_rows["rows"], args.csv)
            print(f"\nWrote {n} row(s) from the last SELECT to {args.csv}")
        else:
            print("\nNo SELECT results to write to CSV.")


def cmd_search_data(args):
    results, actual_db, err = search_data(
        args.name, args.value, getattr(args, "database", None),
        getattr(args, "table", None), args.limit, args.offset,
    )
    if err == "NO_DATABASE_SELECTED":
        dbs, _ = fetch_databases(args.name)
        print("No database selected. Available databases:")
        if dbs:
            for d in dbs:
                print(" -", d)
        print("Re-run with --database <name>.")
        return
    if err:
        print(f"Connection failed: {err}")
        return
    if not results:
        if args.value:
            print(f"No rows containing '{args.value}' found in '{actual_db}'.")
        else:
            print(f"No rows found in '{actual_db}' (table(s) may be empty).")
        return
    for r in results:
        print(f"\n=== {r['table']} ({r['row_count']} row(s)){' - more available, re-run with --offset ' + str(args.offset + args.limit) if r['has_more'] else ''} ===")
        for row in r["rows"]:
            print(row)


def cmd_drop_table(args):
    if not args.yes:
        confirm = input(f"Permanently drop table '{args.table}' (structure AND all rows)? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled - nothing was dropped.")
            return
    ok, err = drop_table(args.name, args.table, getattr(args, "database", None))
    print("Dropped." if ok else f"Failed: {err}")


def cmd_drop_column(args):
    if not args.yes:
        confirm = input(f"Permanently drop column '{args.column}' from '{args.table}'? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled - nothing was dropped.")
            return
    ok, err = drop_column(args.name, args.table, args.column, getattr(args, "database", None))
    print("Dropped." if ok else f"Failed: {err}")


def cmd_delete_row(args):
    try:
        row = json.loads(args.row)
    except Exception as e:
        print(f"--row must be a JSON object, e.g. '{{\"id\": 1}}': {e}")
        return
    if not args.yes:
        confirm = input(f"Delete one row from '{args.table}' matching {row}? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled - nothing was deleted.")
            return
    affected, err = delete_row(args.name, args.table, row, getattr(args, "database", None))
    if err:
        print(f"Failed: {err}")
    else:
        print(f"Deleted {affected} row(s).")


def cmd_history(args):
    history = load_history()
    if not history:
        print("No query history yet.")
        return
    for h in history[-args.limit:]:
        status = "ok" if h["ok"] else f"ERROR: {h['error']}"
        db = h["database"] or "(default)"
        print(f"[{h['timestamp']}] {h['connection']}/{db}: {h['sql'][:100]!r} -> {status}")


def interactive_menu():
    while True:
        print("\n=== DB Connection Manager ===")
        print("1. Add connection (asks host/username/password/db, saved for next time)")
        print("2. List saved connections")
        print("3. Test a connection")
        print("4. Show databases on a server (use this if unsure of the db name)")
        print("5. Export table/column schema to a file (schema_<name>_<db>.json)")
        print("6. Run a query")
        print("7. Search table/column names for a keyword (e.g. patient)")
        print("8. Remove a connection")
        print("9. Exit")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            host = input("Host: ").strip()
            while not host:
                host = input("Host (required): ").strip()
            name = host  # connection name always matches the hostname
            port = input("Port [3306]: ").strip() or "3306"
            user = input("Username [root]: ").strip() or "root"
            password = getpass.getpass("Password [Enter for default]: ")
            database = input("Database name (leave blank - you can target any database later): ").strip()
            add_connection(name, host, port, user, password, database)
            print(f"Saved connection '{name}'. It will be reused automatically next time.")
        elif choice == "2":
            cmd_list(None)
        elif choice == "3":
            name = input("Connection name to test: ").strip()
            cmd_test(argparse.Namespace(name=name))
        elif choice == "4":
            name = input("Connection name: ").strip()
            cmd_databases(argparse.Namespace(name=name))
        elif choice == "5":
            name = input("Connection name: ").strip()
            database = input("Database name (leave blank to use the connection's saved default): ").strip() or None
            cmd_schema(argparse.Namespace(name=name, database=database))
        elif choice == "6":
            name = input("Connection name: ").strip()
            database = input("Database name (leave blank to use the connection's saved default): ").strip() or None
            sql = input("SQL query: ").strip()
            csv_path = input("Save to CSV file (leave blank to print to screen): ").strip() or None
            cmd_query(argparse.Namespace(name=name, database=database, sql=sql, csv=csv_path))
        elif choice == "7":
            name = input("Connection name: ").strip()
            database = input("Database name (leave blank to use the connection's saved default): ").strip() or None
            q = input("Keyword to search for (e.g. patient): ").strip()
            cmd_search(argparse.Namespace(name=name, database=database, q=q))
        elif choice == "8":
            name = input("Connection name to remove: ").strip()
            cmd_remove(argparse.Namespace(name=name))
        elif choice == "9":
            break
        else:
            print("Invalid option, try again.")


def main():
    if len(sys.argv) == 1:
        interactive_menu()
        return

    p = argparse.ArgumentParser(description="Manage and query saved DB connections.")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="Save a new connection")
    a.add_argument("--name", required=False, default=None, help="Defaults to --host if omitted")
    a.add_argument("--host", required=True)
    a.add_argument("--port", type=int, default=3306)
    a.add_argument("--user", required=False, default="root")
    a.add_argument("--database", required=False, default="")
    a.add_argument("--password", help="Optional; if omitted you'll be prompted")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="List saved connections")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("remove", help="Remove a saved connection")
    r.add_argument("--name", required=True)
    r.set_defaults(func=cmd_remove)

    t = sub.add_parser("test", help="Test a saved connection")
    t.add_argument("--name", required=True)
    t.set_defaults(func=cmd_test)

    d = sub.add_parser("databases", help="List databases visible on the server")
    d.add_argument("--name", required=True)
    d.set_defaults(func=cmd_databases)

    s = sub.add_parser("schema", help="Export table/column schema to schema_<name>_<database>.json")
    s.add_argument("--name", required=True)
    s.add_argument("--database", required=False, default=None)
    s.set_defaults(func=cmd_schema)

    se = sub.add_parser("search", help="Search table/column names for a keyword")
    se.add_argument("--name", required=True)
    se.add_argument("--database", required=False, default=None)
    se.add_argument("--q", required=True, help="Keyword to search for, e.g. patient")
    se.set_defaults(func=cmd_search)

    q = sub.add_parser("query", help="Run one or more ;-separated SQL statements against a saved connection")
    q.add_argument("--name", required=True)
    q.add_argument("--database", required=False, default=None)
    q.add_argument("--sql", required=False, default=None, help="SQL to run; omit if using --sql-file")
    q.add_argument("--sql-file", required=False, default=None, help="Path to a .sql file to load and run instead of --sql")
    q.add_argument("--csv", help="Optional path to write results as CSV (last SELECT's rows, if multiple statements)")
    q.set_defaults(func=cmd_query)

    sd = sub.add_parser("search-data", help="Search actual table DATA for a value (like HeidiSQL's 'Find text on server'), or preview a table if --value is omitted")
    sd.add_argument("--name", required=True)
    sd.add_argument("--database", required=False, default=None)
    sd.add_argument("--table", required=False, default=None, help="Limit to one table; omit to scan multiple tables")
    sd.add_argument("--value", required=False, default="", help="Value to search for, e.g. 12345. Omit to just preview rows with no filter.")
    sd.add_argument("--limit", type=int, default=1000, help="Max rows to show per table (default 1000)")
    sd.add_argument("--offset", type=int, default=0, help="Skip this many matching rows first, for paging (default 0)")
    sd.set_defaults(func=cmd_search_data)

    h = sub.add_parser("history", help="Show recent query history")
    h.add_argument("--limit", type=int, default=20, help="How many recent entries to show (default 20)")
    h.set_defaults(func=cmd_history)

    dt = sub.add_parser("drop-table", help="Permanently drop an entire table (structure + all rows) - DESTRUCTIVE, asks for confirmation")
    dt.add_argument("--name", required=True)
    dt.add_argument("--database", required=False, default=None)
    dt.add_argument("--table", required=True)
    dt.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    dt.set_defaults(func=cmd_drop_table)

    dc = sub.add_parser("drop-column", help="Permanently drop one column from a table - DESTRUCTIVE, asks for confirmation")
    dc.add_argument("--name", required=True)
    dc.add_argument("--database", required=False, default=None)
    dc.add_argument("--table", required=True)
    dc.add_argument("--column", required=True)
    dc.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    dc.set_defaults(func=cmd_drop_column)

    dr = sub.add_parser("delete-row", help="Delete one row matching a JSON filter - DESTRUCTIVE, asks for confirmation")
    dr.add_argument("--name", required=True)
    dr.add_argument("--database", required=False, default=None)
    dr.add_argument("--table", required=True)
    dr.add_argument("--row", required=True, help='JSON object of column:value the row must match, e.g. \'{"id": 1}\'')
    dr.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    dr.set_defaults(func=cmd_delete_row)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
