#!/usr/bin/env python3
"""
db_schema_search.py

A HeidiSQL-style schema browser and search tool for DoseWatch (or any) database.
DoseWatch's MySQL database is typically named "serphydose".

HeidiSQL gives you four things this script reproduces:
  1. Auto view: connect and see a simple numbered list of every table right away,
     no flag needed (same tree HeidiSQL shows in its left-hand panel)
  2. "Table filter" box: search for tables by name (--search-table) — also returns a
     simple numbered list
  3. Click-to-view: type a table's number from that list and its full column schema
     (name, type, nullable, primary key) is printed, then you're back at the list to
     pick another — just like clicking a table in HeidiSQL's tree. Add --no-interactive
     to just print the list and skip the prompt (e.g. for scripting).
  4. A column-name search: find every table that has a column matching a pattern
     (handy for DoseWatch tables like PatientID, StudyInstanceUID, DoseValue, etc.)
  5. "Find text on server": grep a value across every text/numeric column in every table

Works with MySQL (DoseWatch's "serphydose" DB), SQL Server, PostgreSQL, and SQLite via
SQLAlchemy — point it at your DoseWatch server once you have connection details, or
run it against a local SQLite file for testing right now.

--------------------------------------------------------------------------------------
INSTALL (run once)
--------------------------------------------------------------------------------------
pip install sqlalchemy --break-system-packages
# plus a driver for your DB:
pip install pyodbc --break-system-packages        # SQL Server (typical for DoseWatch)
pip install pymysql --break-system-packages        # MySQL / MariaDB
pip install psycopg2-binary --break-system-packages  # PostgreSQL
# SQLite needs no driver.

--------------------------------------------------------------------------------------
CONNECTION STRINGS (examples)
--------------------------------------------------------------------------------------
MySQL (DoseWatch): mysql+pymysql://USER:PASS@HOST:3306/serphydose
SQL Server       : mssql+pyodbc://USER:PASS@HOST:1433/DoseWatch?driver=ODBC+Driver+17+for+SQL+Server
PostgreSQL       : postgresql+psycopg2://USER:PASS@HOST:5432/dosewatch
SQLite           : sqlite:///path/to/file.db

--------------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------------
# 0. Auto view: just pass the connection string, no flags — lists every table as a
#    simple numbered list, then lets you enter a number to view that table's schema
python db_schema_search.py "mysql+pymysql://USER:PASS@HOST/serphydose"

# 1. Search for tables by name (like HeidiSQL's "Table filter" box), then pick a
#    number from the results to view its schema
python db_schema_search.py "mysql+pymysql://USER:PASS@HOST/serphydose" --search-table dose

# 1b. Same search, but just print the list without prompting (for scripts)
python db_schema_search.py "mysql+pymysql://USER:PASS@HOST/serphydose" --search-table dose --no-interactive

# 2. Find every table/column whose NAME matches a pattern (metadata search)
python db_schema_search.py "sqlite:///dosewatch.db" --search-column dose

# 3. Find every row/column whose VALUE contains a string (data grep, like HeidiSQL's
#    "Find text on server")
python db_schema_search.py "sqlite:///dosewatch.db" --search-value "12345" --limit 20

Add --schema NAME to restrict to one schema/database and --table NAME to restrict
--search-value to one table for faster searches on large databases.
"""

import argparse
import sys


def get_inspector(conn_str):
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError:
        print("Missing dependency. Run: pip install sqlalchemy --break-system-packages")
        sys.exit(1)
    engine = create_engine(conn_str)
    return engine, inspect(engine)


def collect_tables(insp, schema=None, pattern=None):
    """Return a sorted list of (schema, table) tuples, optionally filtered by name pattern."""
    schemas = [schema] if schema else insp.get_schema_names()
    found = []
    pattern_l = pattern.lower() if pattern else None
    for sch in schemas:
        try:
            tables = insp.get_table_names(schema=sch)
        except Exception:
            continue
        for t in sorted(tables):
            if pattern_l and pattern_l not in t.lower():
                continue
            found.append((sch, t))
    return found


def print_table_list(found):
    print(f"\n{len(found)} table(s):")
    for i, (sch, t) in enumerate(found, 1):
        label = f"{sch}.{t}" if sch else t
        print(f"  [{i}] {label}")


def show_table_schema(insp, sch, t):
    label = f"{sch}.{t}" if sch else t
    print(f"\n=== {label} ===")
    for col in insp.get_columns(t, schema=sch):
        pk = " [PK]" if col.get("primary_key") else ""
        nullable = "NULL" if col.get("nullable", True) else "NOT NULL"
        print(f"  - {col['name']:<30} {str(col['type']):<20} {nullable}{pk}")


def interactive_browse(insp, found):
    """Simple 'click a table to see its schema' loop, driven by the number in [brackets]."""
    if not found:
        return
    while True:
        try:
            choice = input("\nEnter a number to view that table's schema (Enter to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not choice:
            break
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            sch, t = found[int(choice) - 1]
            show_table_schema(insp, sch, t)
            print_table_list(found)
        else:
            print("Invalid selection — enter a number from the list above.")


def list_tables(insp, schema=None, interactive=True):
    found = collect_tables(insp, schema=schema)
    if not found:
        print("No tables found.")
        return
    print_table_list(found)
    if interactive:
        interactive_browse(insp, found)


def search_tables(insp, pattern, schema=None, interactive=True):
    found = collect_tables(insp, schema=schema, pattern=pattern)
    if not found:
        print(f"No tables matching '{pattern}' found.")
        return
    print_table_list(found)
    if interactive:
        interactive_browse(insp, found)


def search_column_names(insp, pattern, schema=None):
    pattern_l = pattern.lower()
    schemas = [schema] if schema else insp.get_schema_names()
    found = 0
    for sch in schemas:
        try:
            tables = insp.get_table_names(schema=sch)
        except Exception:
            continue
        for t in tables:
            for col in insp.get_columns(t, schema=sch):
                if pattern_l in col["name"].lower():
                    print(f"{sch}.{t}.{col['name']}  ({col['type']})")
                    found += 1
    if not found:
        print(f"No columns matching '{pattern}' found.")
    else:
        print(f"\n{found} matching column(s).")


def qualified(sch, table, engine_name):
    if not sch:
        return f'"{table}"' if "postgres" in engine_name else f"[{table}]" if "mssql" in engine_name else table
    if "mssql" in engine_name:
        return f"[{sch}].[{table}]"
    if "postgres" in engine_name:
        return f'"{sch}"."{table}"'
    return f"{sch}.{table}"


def search_values(engine, insp, value, schema=None, table_filter=None, limit=1000):
    from sqlalchemy import text
    engine_name = engine.name  # e.g. 'mssql', 'mysql', 'postgresql', 'sqlite'
    schemas = [schema] if schema else (insp.get_schema_names() or [None])
    total_hits = 0
    with engine.connect() as conn:
        for sch in schemas:
            try:
                tables = insp.get_table_names(schema=sch)
            except Exception:
                continue
            for t in tables:
                if table_filter and table_filter.lower() != t.lower():
                    continue
                try:
                    cols = insp.get_columns(t, schema=sch)
                except Exception:
                    continue
                text_cols = [c["name"] for c in cols]
                if not text_cols:
                    continue
                qtable = qualified(sch, t, engine_name)
                conditions = " OR ".join(
                    f"CAST([{c}] AS VARCHAR(4000)) LIKE :val" if "mssql" in engine_name
                    else f"CAST({c} AS CHAR(4000)) LIKE :val" if "mysql" in engine_name
                    else f"CAST({c} AS TEXT) LIKE :val"
                    for c in text_cols
                )
                sql = f"SELECT * FROM {qtable} WHERE {conditions}"
                try:
                    rows = conn.execute(text(sql), {"val": f"%{value}%"}).fetchmany(limit)
                except Exception as e:
                    # Skip tables that error (e.g. permission, incompatible cast)
                    continue
                if rows:
                    print(f"\n=== Match in {sch or ''}.{t} ({len(rows)} row(s) shown, limit {limit}) ===")
                    for row in rows:
                        print(dict(row._mapping))
                    total_hits += len(rows)
    if not total_hits:
        print(f"No rows containing '{value}' found.")
    else:
        print(f"\nTotal matching rows shown: {total_hits}")


def main():
    parser = argparse.ArgumentParser(description="HeidiSQL-style schema browser and search tool.")
    parser.add_argument("conn_str", help="SQLAlchemy connection string, e.g. mysql+pymysql://user:pass@host/serphydose")
    parser.add_argument("--list-tables", action="store_true", help="List all schemas, tables, and columns (this is also the default when no other option is given)")
    parser.add_argument("--search-table", metavar="PATTERN", help="Search for a TABLE NAME (like HeidiSQL's 'Table filter' box)")
    parser.add_argument("--search-column", metavar="PATTERN", help="Search for a column NAME across all tables")
    parser.add_argument("--search-value", metavar="TEXT", help="Search for a VALUE across all columns/tables (data grep)")
    parser.add_argument("--schema", metavar="NAME", help="Restrict to a single schema/database")
    parser.add_argument("--table", metavar="NAME", help="Restrict --search-value to a single table")
    parser.add_argument("--limit", type=int, default=1000, help="Max rows to print per table for --search-value (default 1000)")
    parser.add_argument("--no-interactive", action="store_true", help="Just print the table list, skip the 'click a number to view schema' prompt")
    args = parser.parse_args()

    engine, insp = get_inspector(args.conn_str)
    interactive = not args.no_interactive

    if args.search_table:
        search_tables(insp, args.search_table, schema=args.schema, interactive=interactive)
    elif args.search_column:
        search_column_names(insp, args.search_column, schema=args.schema)
    elif args.search_value:
        search_values(engine, insp, args.search_value, schema=args.schema, table_filter=args.table, limit=args.limit)
    else:
        # Auto view: list every table by default (same as --list-tables), then let you
        # "click" (enter its number) to drill into that table's column schema
        list_tables(insp, schema=args.schema, interactive=interactive)


if __name__ == "__main__":
    main()
