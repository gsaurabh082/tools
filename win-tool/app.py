"""Local FastAPI web UI for Port & Process Detective.

Run: uvicorn app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import asdict
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from port_detective import find_port_owner, list_listening_ports, parse_port


app = FastAPI(title="Port & Process Detective", docs_url=None, redoc_url=None)


class ActionRequest(BaseModel):
    port: int
    action: Literal["kill", "suspend", "resume", "restart"]
    pid: Optional[int] = None


def current_owner(port: int):
    try:
        port = parse_port(port)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    owner = find_port_owner(port)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"No accessible process is listening on port {port}.")
    return owner


def run_read_only_command(arguments: list[str]) -> str:
    """Run one fixed, non-mutating Linux command and return useful output."""
    try:
        result = subprocess.run(arguments, text=True, capture_output=True, timeout=8, check=False)
    except FileNotFoundError:
        return f"{arguments[0]} is not installed or is not on PATH."
    except subprocess.TimeoutExpired:
        return "The command took too long and was stopped."
    output = result.stdout.strip() or result.stderr.strip()
    return output or "No output returned."


def developer_shortcut(query: str) -> tuple[str, str]:
    """Translate a small allowlist of developer shorthand into safe commands."""
    shortcut = " ".join(query.lower().split())
    if shortcut == "ports":
        return "Listening ports", run_read_only_command(["ss", "-ltnp"])
    if shortcut == "disk":
        return "Disk space", run_read_only_command(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"])
    if shortcut == "errors":
        return "Recent system errors", run_read_only_command(["journalctl", "--no-pager", "-p", "err", "-n", "30"])
    if shortcut == "docker":
        return "Docker containers", run_read_only_command(
            ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"]
        )
    if shortcut == "ip":
        return "Network addresses", run_read_only_command(["ip", "-brief", "address"])
    if shortcut == "git":
        branch = run_read_only_command(["git", "branch", "--show-current"])
        status = run_read_only_command(["git", "status", "--short"])
        return "Git status", f"Branch: {branch or '(detached HEAD)'}\n\n{status}"
    if shortcut.startswith("process "):
        search = shortcut.removeprefix("process ").strip()
        if not search:
            raise ValueError("Use process followed by a name, for example: process python")
        output = run_read_only_command(["ps", "-eo", "pid,user,pcpu,pmem,comm,args", "--sort=-pcpu"])
        matched = [line for line in output.splitlines() if search in line.lower()]
        return f"Processes matching '{search}'", "\n".join(matched[:25]) or "No matching processes found."
    if shortcut in {"help", "shortcuts"}:
        return "Available shortcuts", "ports\ndisk\nerrors\ndocker\nip\ngit\nprocess <name>"
    raise ValueError("Unknown shortcut. Try: ports, disk, errors, docker, ip, git, or process python.")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return PAGE


@app.get("/api/inspect")
def inspect(port: str):
    try:
        number = parse_port(port)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    owner = find_port_owner(number)
    if owner is None:
        return {"found": False, "message": f"Nothing accessible is listening on port {number}."}
    return {"found": True, "owner": asdict(owner)}


@app.get("/api/ports")
def ports(port: str = "", process: str = ""):
    try:
        port_filter = parse_port(port) if port.strip() else None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    records = list_listening_ports(port_filter=port_filter, process_filter=process)
    return {"records": [asdict(record) for record in records]}


@app.get("/api/quick")
def quick(query: str):
    try:
        title, output = developer_shortcut(query)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"title": title, "output": output}


@app.post("/api/action")
def process_action(request: ActionRequest):
    owner = current_owner(request.port)
    target_pid = owner.pid
    target_process = owner.process
    if request.pid is not None and request.pid != owner.pid:
        match = next((record for record in list_listening_ports(port_filter=request.port) if record.pid == request.pid), None)
        if match is None:
            raise HTTPException(status_code=404, detail="That process no longer owns this port.")
        target_pid = request.pid
        target_process = match.process
    try:
        if request.action == "kill":
            os.kill(target_pid, signal.SIGTERM)
            message = f"Sent SIGTERM to {target_process} (PID {target_pid})."
        elif request.action == "suspend":
            os.kill(target_pid, signal.SIGSTOP)
            message = f"Suspended {target_process} (PID {target_pid})."
        elif request.action == "resume":
            os.kill(target_pid, signal.SIGCONT)
            message = f"Resumed {target_process} (PID {target_pid})."
        else:
            if not owner.service.endswith(".service"):
                raise HTTPException(status_code=400, detail="This process is not associated with a systemd service.")
            result = subprocess.run(["systemctl", "restart", owner.service], text=True, capture_output=True, timeout=15, check=False)
            if result.returncode != 0:
                raise HTTPException(status_code=403, detail=result.stderr.strip() or "systemctl could not restart this service.")
            message = f"Restarted {owner.service}."
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="Permission denied. Run with suitable local privileges.") from error
    except ProcessLookupError as error:
        raise HTTPException(status_code=404, detail="The process already exited.") from error
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="Restart request timed out.") from error
    return {"message": message}


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Port &amp; Process Detective</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; color: #172033; background: #f4f6fa; }
    body { margin: 0; }
    main { max-width: 720px; margin: 0 auto; padding: 42px 20px; }
    h1 { margin: 0; font-size: 1.75rem; }
    .intro, #notice { color: #586174; }
    .search, .card { background: white; border: 1px solid #dce1ea; border-radius: 10px; padding: 18px; margin-top: 18px; }
    .search, .filters { display: flex; gap: 10px; align-items: end; }
    label { display: grid; gap: 6px; font-weight: 650; flex: 1; }
    input { font: inherit; padding: 9px 10px; border: 1px solid #aeb8c9; border-radius: 6px; }
    button { font: inherit; padding: 9px 13px; border: 0; border-radius: 6px; background: #1f63d5; color: white; cursor: pointer; }
    button:hover { background: #174da9; } button:disabled { opacity: .45; cursor: not-allowed; }
    .danger { background: #b4233b; } .danger:hover { background: #8e172b; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; }
    h2 { font-size: 1.15rem; margin: 0 0 5px; } #summary { margin: 0 0 14px; color: #586174; word-break: break-word; }
    dl { display: grid; grid-template-columns: 145px 1fr; gap: 9px 16px; margin: 0; }
    dt { color: #586174; } dd { margin: 0; word-break: break-word; }
    pre { margin: 12px 0 0; padding: 12px; max-height: 320px; overflow: auto; background: #f4f6fa; border-radius: 6px; white-space: pre-wrap; word-break: break-word; font: .82rem ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .table-wrap { overflow-x: auto; margin-top: 12px; border: 1px solid #dce1ea; border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: .9rem; white-space: nowrap; }
    th, td { padding: 9px 10px; text-align: left; border-bottom: 1px solid #edf0f5; } th { background: #f8f9fb; color: #586174; font-size: .78rem; text-transform: uppercase; } tbody tr:last-child td { border-bottom: 0; }
    .secondary { background: #eef1f6; color: #172033; } .secondary:hover { background: #dfe5ef; }
    .hidden { display: none; } .error { color: #b4233b !important; } .success { color: #11744d !important; }
    @media (max-width: 540px) { .search, .filters { display: block; } .search button, .filters button { margin-top: 10px; } dl { grid-template-columns: 1fr; gap: 2px; } dd { margin-bottom: 8px; } }
  </style>
</head>
<body>
  <main>
    <h1>Port &amp; Process Detective</h1>
    <p class="intro">Find processes and services using local TCP or UDP ports.</p>
    <section class="card">
      <h2>Services &amp; listening ports</h2>
      <p id="list-status" class="intro">Loading local listeners…</p>
      <form id="filters-form" class="filters">
        <label>Port filter <input id="port-filter" inputmode="numeric" placeholder="e.g. 8000" aria-label="Filter by port"></label>
        <label>Process filter <input id="process-filter" placeholder="e.g. python" aria-label="Filter by process"></label>
        <button type="submit">Search</button>
        <button id="clear-filters" type="button" class="secondary">Clear</button>
      </form>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Port</th><th>Protocol</th><th>Address</th><th>Process</th><th>PID</th><th>Service</th><th>User</th><th>State</th><th>Actions</th></tr></thead>
          <tbody id="ports-body"></tbody>
        </table>
      </div>
    </section>
    <form id="check-form" class="search">
      <label>Port <input id="port" inputmode="numeric" value="8080" aria-label="Port number"></label>
      <button type="submit">Check port</button>
    </form>
    <p id="notice">Enter a port and choose Check port.</p>
    <section id="result" class="card hidden" aria-live="polite">
      <h2 id="title"></h2>
      <p id="summary"></p>
      <dl>
        <dt>Process</dt><dd id="process">—</dd>
        <dt>Container</dt><dd id="container">—</dd>
        <dt>Service</dt><dd id="service">—</dd>
        <dt>Startup source</dt><dd id="startup">—</dd>
        <dt>User</dt><dd id="user">—</dd>
        <dt>CPU</dt><dd id="cpu">—</dd>
        <dt>Memory</dt><dd id="memory">—</dd>
      </dl>
      <div class="actions">
        <button class="danger" data-action="kill" disabled>Kill</button>
        <button id="suspend" data-action="suspend" disabled>Suspend</button>
        <button data-action="restart" disabled>Restart service</button>
      </div>
    </section>
    <section class="card">
      <h2>Developer shortcuts</h2>
      <p id="shortcut-help" class="intro">Try <code>ports</code>, <code>disk</code>, <code>errors</code>, <code>docker</code>, <code>ip</code>, <code>git</code>, or <code>process python</code>.</p>
      <form id="shortcut-form" class="search">
        <label>Shortcut <input id="shortcut" value="ports" autocomplete="off" aria-label="Developer shortcut"></label>
        <button type="submit">Run</button>
      </form>
      <section id="quick-result" class="hidden" aria-live="polite">
        <h2 id="quick-title"></h2>
        <pre id="quick-output"></pre>
      </section>
    </section>
  </main>
  <script>
    const byId = id => document.getElementById(id);
    let owner = null;
    const actionButtons = [...document.querySelectorAll('[data-action]')];
    const showNotice = (message, kind = '') => { const n = byId('notice'); n.textContent = message; n.className = kind; };
    const setActions = enabled => {
      actionButtons.forEach(button => {
        button.disabled = !enabled || (button.dataset.action === 'restart' && !owner.service.endsWith('.service'));
      });
      const suspend = byId('suspend');
      suspend.dataset.action = owner && owner.state.toLowerCase().includes('stopped') ? 'resume' : 'suspend';
      suspend.textContent = suspend.dataset.action === 'resume' ? 'Resume' : 'Suspend';
    };
    function renderPorts(records) {
      const body = byId('ports-body');
      body.replaceChildren();
      if (!records.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 9; cell.textContent = 'No matching listening ports found.';
        row.append(cell); body.append(row); return;
      }
      records.forEach(record => {
        const row = document.createElement('tr');
        [record.port, record.protocol, record.address, record.process, record.pid ?? '—', record.service, record.user, record.state].forEach(value => {
          const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
        });
        const actions = document.createElement('td');
        const inspectButton = document.createElement('button');
        inspectButton.type = 'button'; inspectButton.className = 'secondary'; inspectButton.textContent = 'Inspect';
        inspectButton.addEventListener('click', () => { byId('port').value = record.port; inspect(); });
        actions.append(inspectButton);
        const killButton = document.createElement('button');
        killButton.type = 'button'; killButton.className = 'danger'; killButton.textContent = 'Kill';
        killButton.disabled = !record.pid;
        killButton.style.marginLeft = '6px';
        killButton.addEventListener('click', () => killListedProcess(record));
        actions.append(killButton);
        row.append(actions);
        body.append(row);
      });
    }
    async function killListedProcess(record) {
      if (!record.pid || !confirm(`Send SIGTERM to ${record.process} (PID ${record.pid})?`)) return;
      try {
        const response = await fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({port: record.port, pid: record.pid, action: 'kill'}) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Kill request failed.');
        showNotice(data.message, 'success');
        setTimeout(loadPorts, 250);
      } catch (error) { showNotice(error.message, 'error'); }
    }
    async function loadPorts() {
      const port = byId('port-filter').value.trim();
      const process = byId('process-filter').value.trim();
      const status = byId('list-status');
      status.textContent = 'Loading local listeners…'; status.className = 'intro';
      try {
        const response = await fetch(`/api/ports?port=${encodeURIComponent(port)}&process=${encodeURIComponent(process)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not load listening ports.');
        renderPorts(data.records);
        status.textContent = `${data.records.length} listening port${data.records.length === 1 ? '' : 's'} found.`;
      } catch (error) { renderPorts([]); status.textContent = error.message; status.className = 'error'; }
    }
    function showOwner(data) {
      owner = data;
      byId('result').classList.remove('hidden');
      byId('title').textContent = `Port ${data.port} is owned by ${data.process} (PID ${data.pid})`;
      byId('summary').textContent = `${data.protocol} listener · ${data.command}`;
      byId('process').textContent = `${data.process} · PID ${data.pid} · ${data.state}`;
      byId('container').textContent = data.container;
      byId('service').textContent = data.service;
      byId('startup').textContent = data.startup_source;
      byId('user').textContent = data.user;
      byId('cpu').textContent = data.cpu;
      byId('memory').textContent = data.memory;
      setActions(true);
    }
    async function inspect() {
      const port = byId('port').value.trim();
      showNotice('Checking…');
      try {
        const response = await fetch(`/api/inspect?port=${encodeURIComponent(port)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not inspect this port.');
        if (!data.found) { owner = null; byId('result').classList.add('hidden'); setActions(false); showNotice(data.message); return; }
        showOwner(data.owner); showNotice(`Found a ${data.owner.protocol} listener on port ${data.owner.port}.`, 'success');
      } catch (error) { showNotice(error.message, 'error'); }
    }
    byId('check-form').addEventListener('submit', event => { event.preventDefault(); inspect(); });
    byId('filters-form').addEventListener('submit', event => { event.preventDefault(); loadPorts(); });
    byId('clear-filters').addEventListener('click', () => {
      byId('port-filter').value = ''; byId('process-filter').value = ''; loadPorts();
    });
    byId('shortcut-form').addEventListener('submit', async event => {
      event.preventDefault();
      const shortcut = byId('shortcut').value.trim();
      const help = byId('shortcut-help');
      help.textContent = 'Running…'; help.className = 'intro';
      try {
        const response = await fetch(`/api/quick?query=${encodeURIComponent(shortcut)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not run this shortcut.');
        byId('quick-title').textContent = data.title;
        byId('quick-output').textContent = data.output;
        byId('quick-result').classList.remove('hidden');
        help.textContent = 'Only predefined, read-only developer shortcuts can run here.';
      } catch (error) { help.textContent = error.message; help.className = 'error'; }
    });
    actionButtons.forEach(button => button.addEventListener('click', async () => {
      const action = button.dataset.action;
      if (!owner || !confirm(`${action[0].toUpperCase() + action.slice(1)} ${owner.process} (PID ${owner.pid})?`)) return;
      try {
        const response = await fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({port: owner.port, action}) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Action failed.');
        showNotice(data.message, 'success'); setTimeout(inspect, 250);
      } catch (error) { showNotice(error.message, 'error'); }
    }));
    loadPorts();
  </script>
</body>
</html>"""
