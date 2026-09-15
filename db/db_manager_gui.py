#!/usr/bin/env python3
"""
db_manager_gui.py - a small desktop window for managing DOsewatch DB
connections and running queries, built on top of db_manager.py.

Run it with:
    python db_manager_gui.py
or double-click run_db_gui.bat.

Requires: pip install pymysql
(Tkinter ships with standard Python installs on Windows, nothing extra needed
for the UI itself.)
"""
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import db_manager as dbm


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DOsewatch DB Connection Manager")
        self.geometry("900x600")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.connections_tab = ConnectionsTab(notebook, self)
        self.explore_tab = ExploreTab(notebook, self)
        self.query_tab = QueryTab(notebook, self)

        notebook.add(self.connections_tab, text="Connections")
        notebook.add(self.explore_tab, text="Explore")
        notebook.add(self.query_tab, text="Query")

        self.refresh_all()

    def refresh_all(self):
        names = list(dbm.load_store().keys())
        self.connections_tab.refresh(names)
        self.explore_tab.refresh(names)
        self.query_tab.refresh(names)


def run_in_background(fn, on_done):
    """Runs fn() in a thread, then calls on_done(result, error) on the main
    thread so the UI never freezes while waiting on the network."""
    def worker():
        try:
            result = fn()
            error = None
        except Exception as e:
            result = None
            error = str(e)
        app.after(0, lambda: on_done(result, error))

    threading.Thread(target=worker, daemon=True).start()


class ConnectionsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        right = ttk.Frame(self)
        right.pack(side="left", fill="y", padx=8, pady=8)

        ttk.Label(left, text="Saved connections:").pack(anchor="w")
        self.listbox = tk.Listbox(left)
        self.listbox.pack(fill="both", expand=True)

        ttk.Button(right, text="Add connection...", command=self.open_add_dialog).pack(fill="x", pady=2)
        ttk.Button(right, text="Test selected", command=self.test_selected).pack(fill="x", pady=2)
        ttk.Button(right, text="Remove selected", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(right, text="Refresh", command=lambda: self.app.refresh_all()).pack(fill="x", pady=2)

        self.status = ttk.Label(right, text="", wraplength=220)
        self.status.pack(fill="x", pady=8)

    def refresh(self, names):
        self.listbox.delete(0, tk.END)
        store = dbm.load_store()
        for name in names:
            c = store[name]
            db = c.get("database") or "(none set)"
            self.listbox.insert(tk.END, f"{name}  -  {c['user']}@{c['host']}:{c['port']}/{db}")
        self._names = names

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("No selection", "Select a connection first.")
            return None
        return self._names[sel[0]]

    def open_add_dialog(self):
        AddConnectionDialog(self, on_saved=self.app.refresh_all)

    def test_selected(self):
        name = self._selected_name()
        if not name:
            return
        self.status.config(text=f"Testing '{name}'...")
        run_in_background(lambda: dbm.test_connection(name), self._on_test_done)

    def _on_test_done(self, result, error):
        if error:
            self.status.config(text=f"Error: {error}")
            return
        ok, message = result
        self.status.config(text=message if ok else f"Failed: {message}")

    def remove_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno("Remove connection", f"Remove '{name}'?"):
            dbm.remove_connection(name)
            self.app.refresh_all()


class AddConnectionDialog(tk.Toplevel):
    def __init__(self, parent, on_saved):
        super().__init__(parent)
        self.title("Add connection")
        self.on_saved = on_saved
        self.resizable(False, False)

        fields = [
            ("Host", "host"),
            ("Port", "port"),
            ("Username", "user"),
            ("Password (blank = default)", "password"),
            ("Database (optional - can pick later)", "database"),
        ]
        self.vars = {}
        for i, (label, key) in enumerate(fields):
            ttk.Label(self, text=label + ":").grid(row=i, column=0, sticky="w", padx=8, pady=4)
            show = "*" if key == "password" else ""
            entry = ttk.Entry(self, show=show, width=35)
            entry.grid(row=i, column=1, padx=8, pady=4)
            if key == "port":
                entry.insert(0, "3306")
            if key == "user":
                entry.insert(0, "root")
            self.vars[key] = entry

        ttk.Label(self, text="(connection name is set to the host automatically)").grid(
            row=len(fields), column=0, columnspan=2, padx=8, pady=(0, 4), sticky="w"
        )

        btns = ttk.Frame(self)
        btns.grid(row=len(fields) + 1, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def save(self):
        host = self.vars["host"].get().strip()
        name = host  # connection name always matches the hostname
        port = self.vars["port"].get().strip() or "3306"
        user = self.vars["user"].get().strip() or "root"
        password = self.vars["password"].get()
        database = self.vars["database"].get().strip()

        if not host:
            messagebox.showerror("Missing info", "Host is required.")
            return
        try:
            port = int(port)
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.")
            return

        dbm.add_connection(name, host, port, user, password, database)
        messagebox.showinfo("Saved", f"Connection '{name}' saved.")
        self.on_saved()
        self.destroy()


class ExploreTab(ttk.Frame):
    """Browse databases on a server and export table/column schema."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="Connection:").grid(row=0, column=0, sticky="w")
        self.conn_combo = ttk.Combobox(top, state="readonly", width=25)
        self.conn_combo.grid(row=0, column=1, padx=4)

        ttk.Label(top, text="Database (optional):").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.db_entry = ttk.Entry(top, width=20)
        self.db_entry.grid(row=0, column=3, padx=4)

        ttk.Button(top, text="Show databases", command=self.show_databases).grid(row=0, column=4, padx=6)
        ttk.Button(top, text="Export schema", command=self.export_schema).grid(row=0, column=5, padx=6)

        self.output = tk.Text(self, wrap="word")
        self.output.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh(self, names):
        self.conn_combo["values"] = names
        if names and not self.conn_combo.get():
            self.conn_combo.current(0)

    def _selected_name(self):
        name = self.conn_combo.get()
        if not name:
            messagebox.showinfo("No connection", "Choose a connection first.")
            return None
        return name

    def show_databases(self):
        name = self._selected_name()
        if not name:
            return
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, "Loading...\n")
        run_in_background(lambda: dbm.fetch_databases(name), self._on_databases_done)

    def _on_databases_done(self, result, error):
        self.output.delete("1.0", tk.END)
        if error:
            self.output.insert(tk.END, f"Error: {error}\n")
            return
        dbs, err = result
        if err:
            self.output.insert(tk.END, f"Connection failed: {err}\n")
            return
        self.output.insert(tk.END, "Databases on this server:\n")
        for d in dbs:
            self.output.insert(tk.END, f" - {d}\n")

    def export_schema(self):
        name = self._selected_name()
        if not name:
            return
        database = self.db_entry.get().strip() or None
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, "Loading...\n")
        run_in_background(lambda: dbm.fetch_schema(name, database), self._on_schema_done)

    def _on_schema_done(self, result, error):
        self.output.delete("1.0", tk.END)
        if error:
            self.output.insert(tk.END, f"Error: {error}\n")
            return
        schema, actual_db, err = result
        if err == "NO_DATABASE_SELECTED":
            self.output.insert(tk.END, "No database selected - type one in the 'Database' box above, then try again.\n")
            return
        if err:
            self.output.insert(tk.END, f"Connection failed: {err}\n")
            return
        if not schema:
            self.output.insert(tk.END, f"No tables found in '{actual_db}'.\n")
            return
        out_path = dbm.BASE_DIR / f"schema_{self.conn_combo.get()}_{actual_db}.json"
        out_path.write_text(json.dumps(schema, indent=2))
        self.output.insert(tk.END, f"Wrote schema for {len(schema)} table(s) in '{actual_db}' to:\n{out_path}\n\n")
        for table, cols in schema.items():
            self.output.insert(tk.END, f"{table}\n")
            for c in cols:
                self.output.insert(tk.END, f"    {c['column']} ({c['type']})\n")


class QueryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._last_rows = None

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="Connection:").grid(row=0, column=0, sticky="w")
        self.conn_combo = ttk.Combobox(top, state="readonly", width=25)
        self.conn_combo.grid(row=0, column=1, padx=4)

        ttk.Label(top, text="Database (optional):").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.db_entry = ttk.Entry(top, width=20)
        self.db_entry.grid(row=0, column=3, padx=4)

        ttk.Button(top, text="Run query", command=self.run_query).grid(row=0, column=4, padx=6)
        ttk.Button(top, text="Save results as CSV...", command=self.save_csv).grid(row=0, column=5, padx=6)

        self.sql_text = tk.Text(self, height=6, wrap="word")
        self.sql_text.pack(fill="x", padx=8)
        self.sql_text.insert("1.0", "SELECT * FROM table_name LIMIT 1000")

        self.status = ttk.Label(self, text="")
        self.status.pack(fill="x", padx=8, anchor="w")

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(table_frame, show="headings")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

    def refresh(self, names):
        self.conn_combo["values"] = names
        if names and not self.conn_combo.get():
            self.conn_combo.current(0)

    def run_query(self):
        name = self.conn_combo.get()
        if not name:
            messagebox.showinfo("No connection", "Choose a connection first.")
            return
        sql = self.sql_text.get("1.0", tk.END).strip()
        if not sql:
            messagebox.showinfo("No query", "Type a SQL query first.")
            return
        database = self.db_entry.get().strip() or None
        self.status.config(text="Running...")
        run_in_background(lambda: dbm.run_query(name, sql, database), self._on_query_done)

    def _on_query_done(self, result, error):
        if error:
            self.status.config(text=f"Error: {error}")
            return
        rows, err = result
        if err:
            self.status.config(text=f"Query failed: {err}")
            return
        self._last_rows = rows
        self.tree.delete(*self.tree.get_children())
        if not rows:
            self.tree["columns"] = []
            self.status.config(text="Query returned no rows.")
            return
        columns = list(rows[0].keys())
        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=120)
        for row in rows:
            self.tree.insert("", tk.END, values=[row[c] for c in columns])
        self.status.config(text=f"{len(rows)} row(s).")

    def save_csv(self):
        if not self._last_rows:
            messagebox.showinfo("No results", "Run a query first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        n = dbm.write_csv(self._last_rows, path)
        messagebox.showinfo("Saved", f"Wrote {n} row(s) to {path}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
