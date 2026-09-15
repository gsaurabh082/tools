"""Local FastAPI UI for RDP Host Manager.

Keeps a saved list of RDP hosts and launches pre-authenticated mstsc
sessions from the browser, the same way the Jira report tool runs a local
FastAPI app with a browser front end.

Defaults:
  - username: .\\vmadmin
  - password: LocalAdm1n!   (used only when a host is added without one)
If a custom password is entered when adding/editing a host, it is saved
for that host and reused every time after that.

Connecting stages the host's credential in Windows Credential Manager
(cmdkey) right before mstsc launches - so the RDP login screen is skipped
- and removes it once the RDP window closes.

Note on local accounts (".\\user"): Windows' own login prompt quietly
resolves ".\\" to "this machine" when you type it interactively, but a
credential fetched from Credential Manager during the NLA handshake is
sent as-is, and many RDP hosts reject a literal "." domain there even
though the same account/password typed by hand works fine. So for cmdkey
we swap ".\\user" for "<host>\\user" - using the target's own address as
the domain - which is the documented fix for this exact mismatch.

Certificate trust: launching via a generated .rdp file (to set
"authentication level:i:0" and skip the certificate warning) turned out to
silently fail in this environment - mstsc reported success but never
opened a window, whereas the plain "mstsc /v:<host>" form is confirmed
working here. So this connects with the plain form and leaves the one-time
certificate prompt in place; ticking its own "Don't ask me again for
connections to this computer" box the first time you connect to a given
host makes Windows remember that trust decision going forward, without
relying on a mechanism this environment blocks.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rdp_credential_store import load_hosts, new_host_id, save_hosts

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

DEFAULT_USER = r".\vmadmin"
DEFAULT_PASS = "LocalAdm1n!"

logger = logging.getLogger("rdp-host-manager")

app = FastAPI(
    title="RDP Host Manager",
    description="Local tool to store RDP hosts and launch pre-authenticated sessions.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class HostIn(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    ip: str = Field(min_length=1, max_length=260)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=300)


class HostUpdate(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    ip: str = Field(min_length=1, max_length=260)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=300)
    keep_password: bool = True


def _public(host: dict) -> dict:
    """Never send the stored password back to the browser."""
    return {
        "id": host["id"],
        "alias": host["alias"],
        "ip": host["ip"],
        "username": host.get("username") or DEFAULT_USER,
        "custom_password": bool(host.get("password")) and host.get("password") != DEFAULT_PASS,
    }


def _find(hosts: list[dict], host_id: str) -> dict:
    for host in hosts:
        if host["id"] == host_id:
            return host
    raise HTTPException(status_code=404, detail="Host not found.")


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/defaults")
async def defaults() -> dict[str, str]:
    return {"username": DEFAULT_USER, "password_placeholder": "Default: " + DEFAULT_PASS}


@app.get("/api/hosts")
async def list_hosts() -> dict:
    hosts = sorted(load_hosts(), key=lambda h: h.get("alias", "").casefold())
    return {"hosts": [_public(h) for h in hosts]}


@app.post("/api/hosts")
async def add_host(request: HostIn) -> dict:
    hosts = load_hosts()
    host = {
        "id": new_host_id(),
        "alias": request.alias.strip(),
        "ip": request.ip.strip(),
        "username": request.username.strip() or DEFAULT_USER,
        "password": request.password or DEFAULT_PASS,
    }
    hosts.append(host)
    save_hosts(hosts)
    return _public(host)


@app.put("/api/hosts/{host_id}")
async def edit_host(host_id: str, request: HostUpdate) -> dict:
    hosts = load_hosts()
    host = _find(hosts, host_id)
    host["alias"] = request.alias.strip()
    host["ip"] = request.ip.strip()
    host["username"] = request.username.strip() or DEFAULT_USER
    if not request.keep_password:
        host["password"] = request.password or DEFAULT_PASS
    save_hosts(hosts)
    return _public(host)


@app.delete("/api/hosts/{host_id}")
async def delete_host(host_id: str) -> dict[str, bool]:
    hosts = load_hosts()
    remaining = [h for h in hosts if h["id"] != host_id]
    if len(remaining) == len(hosts):
        raise HTTPException(status_code=404, detail="Host not found.")
    save_hosts(remaining)
    return {"deleted": True}


@app.post("/api/hosts/{host_id}/test")
async def test_host(host_id: str) -> dict:
    hosts = load_hosts()
    host = _find(hosts, host_id)
    ip = host["ip"]
    count_flag = "-n" if platform.system() == "Windows" else "-c"
    try:
        result = subprocess.run(
            ["ping", count_flag, "2", ip],
            capture_output=True,
            text=True,
            timeout=8,
        )
        reachable = result.returncode == 0
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        output = lines[-1] if lines else result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        reachable = False
        output = str(exc)
    return {"reachable": reachable, "output": output}


def _cleanup_credential(ip: str) -> None:
    try:
        subprocess.run(
            ["cmdkey", f"/delete:TERMSRV/{ip}"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Credential cleanup failed for %s: %s", ip, exc)


def _wait_and_cleanup(proc: subprocess.Popen, ip: str) -> None:
    try:
        proc.wait()
    finally:
        _cleanup_credential(ip)


def _cmdkey_username(username: str, ip: str) -> str:
    """cmdkey/NLA sends the domain part of a credential as-is, so a local
    account entered as '.\\user' (which only mstsc's own interactive login
    box knows how to resolve to 'this machine') must be rewritten to use
    the target host itself as the domain before it's cached."""
    if username.startswith(".\\") or username.startswith("./"):
        return f"{ip}\\{username[2:]}"
    return username


@app.post("/api/hosts/{host_id}/connect")
async def connect_host(host_id: str) -> dict:
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=400,
            detail="RDP launch requires this server to run on Windows (mstsc/cmdkey).",
        )
    hosts = load_hosts()
    host = _find(hosts, host_id)
    ip = host["ip"]
    username = host.get("username") or DEFAULT_USER
    password = host.get("password") or DEFAULT_PASS
    cmdkey_user = _cmdkey_username(username, ip)

    try:
        result = subprocess.run(
            ["cmdkey", f"/generic:TERMSRV/{ip}", f"/user:{cmdkey_user}", f"/pass:{password}"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not stage credentials: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "cmdkey failed").strip()
        raise HTTPException(status_code=500, detail=f"cmdkey rejected the credential: {detail}")

    try:
        proc = subprocess.Popen(["mstsc", f"/v:{ip}"])
    except OSError as exc:
        _cleanup_credential(ip)
        raise HTTPException(status_code=500, detail=f"Could not launch mstsc: {exc}") from exc

    # mstsc normally keeps running for as long as the window is open. If it
    # exits within the first few seconds, something failed before a window
    # ever showed. Poll a few times rather than a single fixed wait, since
    # that isn't always instant.
    exit_code = None
    for _ in range(6):
        await asyncio.sleep(0.5)
        exit_code = proc.poll()
        if exit_code is not None:
            break
    if exit_code is not None:
        _cleanup_credential(ip)
        raise HTTPException(
            status_code=500,
            detail=f"mstsc closed immediately (exit code {exit_code}) - it likely never opened a window.",
        )

    # Wait for the RDP window to actually close before wiping the cached
    # credential, rather than guessing a fixed delay - avoids removing it
    # mid-handshake on a slow network.
    threading.Thread(target=_wait_and_cleanup, args=(proc, ip), daemon=True).start()
    return {
        "status": "launched",
        "alias": host["alias"],
        "ip": ip,
        "cmdkey_user": cmdkey_user,
    }


@app.post("/api/hosts/{host_id}/restart")
async def restart_host(host_id: str) -> dict:
    """Remote-restarts the VM using the saved credentials, over the classic
    Windows admin channel (net use to authenticate an IPC$ session, then
    'shutdown /r /m'). This needs RPC/SMB (ports 135 and 445) reachable and
    the account to be a local admin on the target - both are normally true
    for the kind of local admin lab/test VMs this tool is built for."""
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=400,
            detail="Remote restart requires this server to run on Windows (net use/shutdown).",
        )
    hosts = load_hosts()
    host = _find(hosts, host_id)
    ip = host["ip"]
    username = host.get("username") or DEFAULT_USER
    password = host.get("password") or DEFAULT_PASS
    cmdkey_user = _cmdkey_username(username, ip)

    try:
        auth = subprocess.run(
            ["net", "use", f"\\\\{ip}\\IPC$", password, f"/user:{cmdkey_user}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not authenticate to {ip}: {exc}") from exc
    if auth.returncode != 0:
        detail = (auth.stderr or auth.stdout or "net use failed").strip()
        raise HTTPException(
            status_code=500,
            detail=f"Could not open an admin session on {ip} (check the account is a local admin there): {detail}",
        )

    try:
        result = subprocess.run(
            [
                "shutdown", "/r", "/m", f"\\\\{ip}", "/t", "5", "/f",
                "/c", "Restart requested from RDP Host Manager",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not send the restart command: {exc}") from exc
    finally:
        subprocess.run(["net", "use", f"\\\\{ip}\\IPC$", "/delete", "/y"], capture_output=True, timeout=10)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "shutdown failed").strip()
        raise HTTPException(status_code=500, detail=f"Restart command was rejected: {detail}")

    return {"status": "restarting", "alias": host["alias"], "ip": ip}
