"""hub.py — DevHub: bookmark manager + service launcher on port 7272."""
from __future__ import annotations

# pythonw.exe sets stdout/stderr to None; patch before uvicorn imports logging
import sys as _sys, os as _os
if _sys.stdout is None: _sys.stdout = open(_os.devnull, "w")
if _sys.stderr is None: _sys.stderr = open(_os.devnull, "w")

import argparse
import ctypes
import json
import glob
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

APP_NAME = "DevHub"
BASE = Path(__file__).parent
BOOKMARKS_FILE = BASE / "bookmarks.json"
STATIC_DIR = BASE / "static"
LOG_DIR = BASE / "logs"
# seconds the UI waits for a service to answer HTTP after launch
DEFAULT_LAUNCH_TIMEOUT = 30
# Per-Windows-profile settings — lives in the user's home, not the (synced) hub dir,
# so each profile on the machine gets its own identity.
PROFILE_FILE = Path.home() / ".devhub" / "profile.json"
_LEGACY_PROFILES = [Path.home() / ".mydevhub" / "profile.json",
                    Path.home() / ".local-hub" / "profile.json"]
# Services the user removed from their board. Kept out of profile.json on purpose:
# switching profile shouldn't resurrect a tile someone deliberately hid.
HIDDEN_FILE = Path.home() / ".devhub" / "hidden-services.json"
PORT = 7272
HOST = "127.0.0.1"
# Suppresses console window on Windows for all child processes.
CREATE_NO_WINDOW = 0x08000000

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)

# ── storage ──────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if BOOKMARKS_FILE.exists():
        return json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
    return []


def _save(data: list[dict]) -> None:
    BOOKMARKS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_hidden() -> set[str]:
    """Ids of services the user removed from the board."""
    try:
        data = json.loads(HIDDEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def _save_hidden(ids: set[str]) -> None:
    HIDDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    HIDDEN_FILE.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


# ── current profile ──────────────────────────────────────────────────────────

def _login_name() -> str:
    return _os.environ.get("USERNAME") or _os.environ.get("USER") or "user"


def _natural_order(name: str) -> str:
    """AD hands back 'Gupta, Saurabh' — show it the way people say it."""
    if name.count(",") == 1:
        last, first = (p.strip() for p in name.split(","))
        if last and first:
            return f"{first} {last}"
    return name


def _os_display_name() -> str:
    """Friendly name of the signed-in Windows profile; falls back to the login name."""
    if _sys.platform == "win32":
        NAME_DISPLAY = 3
        try:
            size = ctypes.c_ulong(0)
            ctypes.windll.secur32.GetUserNameExW(NAME_DISPLAY, None, ctypes.byref(size))
            buf = ctypes.create_unicode_buffer(size.value or 256)
            if ctypes.windll.secur32.GetUserNameExW(NAME_DISPLAY, buf, ctypes.byref(size)):
                if buf.value.strip():
                    return _natural_order(buf.value.strip())
        except Exception:
            pass
        # Local (non-domain) accounts: NetUserGetInfo level 2 carries the full name.
        try:
            class _UserInfo2(ctypes.Structure):
                _fields_ = [("usri2_name", ctypes.c_wchar_p),
                            ("usri2_password", ctypes.c_wchar_p),
                            ("usri2_password_age", ctypes.c_ulong),
                            ("usri2_priv", ctypes.c_ulong),
                            ("usri2_home_dir", ctypes.c_wchar_p),
                            ("usri2_comment", ctypes.c_wchar_p),
                            ("usri2_flags", ctypes.c_ulong),
                            ("usri2_script_path", ctypes.c_wchar_p),
                            ("usri2_auth_flags", ctypes.c_ulong),
                            ("usri2_full_name", ctypes.c_wchar_p)]

            ptr = ctypes.POINTER(_UserInfo2)()
            if ctypes.windll.netapi32.NetUserGetInfo(
                    None, _login_name(), 2, ctypes.byref(ptr)) == 0:
                try:
                    full = (ptr.contents.usri2_full_name or "").strip()
                finally:
                    ctypes.windll.netapi32.NetApiBufferFree(ptr)
                if full:
                    return _natural_order(full)
        except Exception:
            pass
    return _login_name()


def _initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _migrate_profile() -> None:
    """Carry a profile over from an earlier name of the app — renaming the app
    shouldn't make someone set themselves up again."""
    if PROFILE_FILE.exists():
        return
    for old in _LEGACY_PROFILES:
        if old.exists():
            try:
                PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(PROFILE_FILE))
                try:
                    old.parent.rmdir()  # only succeeds if now empty
                except OSError:
                    pass
            except Exception:
                pass
            return


def _load_profile() -> dict:
    _migrate_profile()
    try:
        if PROFILE_FILE.exists():
            return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_profile(data: dict) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _user_payload() -> dict:
    profile = _load_profile()
    os_name = _os_display_name()
    custom = (profile.get("display_name") or "").strip()
    name = custom or os_name
    return {
        "app_name": APP_NAME,
        "username": _login_name(),
        "display_name": name,
        "os_display_name": os_name,
        "initials": _initials(name),
        "custom": bool(custom),
        # False until the profile has been set up once — drives the first-run screen.
        "configured": bool(profile.get("configured")),
        "profile_path": str(PROFILE_FILE),
    }


# ── models ───────────────────────────────────────────────────────────────────

class BookmarkIn(BaseModel):
    name: str
    url: str
    description: str = ""
    category: str = "General"


class UserIn(BaseModel):
    display_name: str


# ── services config ───────────────────────────────────────────────────────────

# script_key: unique substring matched against process cmdline OR cwd
# cwd_key:    extra filter matched against process working directory
# proc_name:  process executable to scan ("python" or "node")
# launch:     how to start the service without a console window
_SERVICES: list[dict] = [
    # ── Analytics & Data ────────────────────────────────────────────────────
    {
        "id": "db", "name": "DB Manager",
        "category": "Analytics & Data", "icon": "db",
        "description": "DOsewatch database connection manager",
        "script_key": "db_web.py", "proc_name": "python", "cwd_key": "",
        "dir": "../db",
        "launch": {"type": "python", "args": ["db_web.py"]},
    },
    {
        "id": "jira", "name": "Jira Report",
        "category": "Analytics & Data", "icon": "chart",
        "description": "Jira Friday status & hygiene report",
        "script_key": "fastapi_app", "proc_name": "python", "cwd_key": "Jira",
        "dir": "../Jira",
        "launch": {"type": "python", "args": ["-m", "uvicorn", "fastapi_app:app",
                                               "--host", "127.0.0.1", "--port", "0"]},
    },
    {
        "id": "gitlab", "name": "GitLab Dashboard",
        "category": "Analytics & Data", "icon": "git",
        "description": "GitLab MR focus dashboard",
        "script_key": "run.py", "proc_name": "python", "cwd_key": "gitlab",
        "dir": "../gitlab",
        "launch": {"type": "python", "args": ["run.py"]},
    },
    {
        "id": "jenkins-chain", "name": "Jenkins Pipeline Chain",
        "category": "Analytics & Data", "icon": "pipeline",
        "description": "Chain Jenkins jobs, poll status, retry failures, auto-merge GitLab MRs",
        "script_key": "run.py", "proc_name": "python", "cwd_key": "jenkins",
        "dir": "../Jenkins",
        "launch": {"type": "python", "args": ["run.py"]},
    },
    # ── Medical Imaging ──────────────────────────────────────────────────────
    {
        "id": "dicom", "name": "DICOM Send",
        "category": "Medical Imaging", "icon": "send",
        "description": "DICOM file sender — throttled multi-target",
        "script_key": "dicom_send_web.py", "proc_name": "python", "cwd_key": "",
        "dir": "../dicom-send",
        "launch": {"type": "python", "args": ["dicom_send_web.py"]},
    },
    {
        "id": "bugdetect", "name": "SR Assistant",
        "category": "Medical Imaging", "icon": "bug",
        "description": "DoseWatch SR bug detection & AI assistant",
        "script_key": "", "proc_name": "node", "cwd_key": "sr-assistant",
        "dir": "../BugDetection/sr-assistant",
        "launch": {"type": "bat", "bat": "start.bat"},
        "timeout": 120,
    },
    {
        "id": "microdicom", "name": "MicroDicom Viewer",
        "category": "Medical Imaging", "icon": "scan",
        "description": "MicroDicom \u2014 open and inspect DICOM studies",
        "script_key": "", "proc_name": "", "cwd_key": "",
        "dir": ".",
        "launch": {
            "type": "app", "proc": "mDicom.exe",
            "exe_globs": [
                "C:/Program Files/MicroDicom/mDicom.exe",
                "C:/Program Files (x86)/MicroDicom/mDicom.exe",
            ],
        },
    },
    # ── Knowledge & Tools ────────────────────────────────────────────────────
    {
        "id": "kb", "name": "QuickFind",
        "category": "Knowledge & Tools", "icon": "search",
        "description": "DoseWatch knowledge-base quick search",
        "script_key": "", "proc_name": "node", "cwd_key": "quickfind",
        "dir": "../kb/dosewatch-quickfind",
        "launch": {"type": "bat", "bat": "Start DoseWatch Quick.bat"},
        "port_hint": 3000,  # serves an API on 8080 too - open the UI, not the API
        "timeout": 120,
    },
    {
        "id": "patches", "name": "Patches",
        "category": "Knowledge & Tools", "icon": "wrench",
        "description": "Local dev patches & hotfixes — opens folder",
        "script_key": "", "proc_name": "", "cwd_key": "",
        "dir": "../patches",
        "launch": {"type": "explorer"},
    },
    # ── Dev Tools ────────────────────────────────────────────────────────────
    # launch type "app": a desktop program. No port, no browser tab - the hub
    # just starts it and reports whether its process is already up.
    # Globs use forward slashes: Windows glob accepts them and it keeps the
    # patterns free of backslash-escaping traps.
    {
        "id": "windock", "name": "WinDock",
        "script_key": "", "proc_name": "", "cwd_key": "", "dir": "../win-docker",
        "category": "Dev Tools", "icon": "container",
        "description": "Docker on Windows via WSL2 - no Docker Desktop",
        "launch": {
            "type": "app", "proc": "windock.exe",
            "exe_globs": [
                "C:/Program Files/WinDock*/WinDock/WinDock.exe",
                "C:/Program Files/WinDock*/WinDock.exe",
            ],
            "fallback": "../win-docker/launch-wincolima.bat",
        },
    },
    {
        "id": "intellij", "name": "IntelliJ IDEA",
        "script_key": "", "proc_name": "", "cwd_key": "", "dir": ".",
        "category": "Dev Tools", "icon": "idea",
        "description": "Open the dosewatch-all project in IntelliJ",
        "launch": {
            "type": "app", "proc": "idea64.exe", "open_repo": True,
            "exe_globs": [
                "C:/Program Files/JetBrains/IntelliJ IDEA*/bin/idea64.exe",
                "C:/Program Files (x86)/JetBrains/IntelliJ IDEA*/bin/idea64.exe",
                str(Path.home()).replace("\\", "/") +
                    "/AppData/Local/JetBrains/Toolbox/apps/**/idea64.exe",
            ],
        },
    },
    {
        "id": "vstudio", "name": "Visual Studio",
        "script_key": "", "proc_name": "", "cwd_key": "", "dir": ".",
        "category": "Dev Tools", "icon": "vs",
        "description": "Visual Studio IDE",
        "launch": {
            "type": "app", "proc": "devenv.exe",
            "exe_globs": [
                "C:/Program Files/Microsoft Visual Studio/*/*/Common7/IDE/devenv.exe",
                "C:/Program Files (x86)/Microsoft Visual Studio/*/*/Common7/IDE/devenv.exe",
            ],
        },
    },
    {
        "id": "devwork", "name": "Dev Workstation Manager",
        "script_key": "", "proc_name": "", "cwd_key": "",
        "dir": "../Dev Workstation Manager",
        "category": "Dev Tools", "icon": "toolbox",
        "description": "winget installer for the whole dev stack",
        # Its own HttpListener always binds 38217, so status comes from the port.
        "fixed_port": 38217,
        # -NoBrowser: DevHub's /go page owns the tab, so we don't end up with two.
        "launch": {"type": "ps1", "script": "BrowserServer.ps1", "args": ["-NoBrowser"]},
        "timeout": 45,
    },
    {
        "id": "vscode", "name": "VS Code",
        "script_key": "", "proc_name": "", "cwd_key": "", "dir": ".",
        "category": "Dev Tools", "icon": "code",
        "description": "Open the dosewatch-all project in VS Code",
        "launch": {
            "type": "app", "proc": "code.exe", "open_repo": True,
            "exe_globs": [
                str(Path.home()).replace("\\", "/") +
                    "/AppData/Local/Programs/Microsoft VS Code/Code.exe",
                "C:/Program Files/Microsoft VS Code/Code.exe",
                "C:/Program Files (x86)/Microsoft VS Code/Code.exe",
            ],
        },
    },
    {
        "id": "ear-viewer", "name": "EAR/JAR Viewer",
        "script_key": "", "proc_name": "", "cwd_key": "", "dir": "../ear-viewer",
        "category": "Dev Tools", "icon": "jar",
        "description": "JD-GUI — decompile and inspect .jar / .ear / .class files",
        "launch": {"type": "jar", "jar": "jd-gui-1.6.6.jar", "proc": "javaw.exe"},
    },
    # ── Infrastructure ─────────────────────────────────────────────────────────
    {
        "id": "rdp", "name": "RDP Host Manager",
        "category": "Infrastructure", "icon": "remote",
        "description": "Remote desktop hosts & saved credentials",
        # "/rdp" rather than "rdp": the leading slash keeps this off any other
        # directory that merely contains those three letters.
        "script_key": "rdp_manager_app", "proc_name": "python", "cwd_key": "/rdp",
        "dir": "../rdp",
        "launch": {"type": "python", "args": ["-m", "uvicorn", "rdp_manager_app:app",
                                               "--host", "127.0.0.1", "--port", "0"]},
    },
    {
        "id": "windowvm", "name": "DoseWatch Dev VM",
        "category": "Infrastructure", "icon": "vm",
        # Requires admin/UAC — opens folder so user can run the bat manually
        "description": "Hyper-V / VirtualBox VM setup scripts (run bat as Admin)",
        "script_key": "", "proc_name": "", "cwd_key": "",
        "dir": "../WindowVM",
        "launch": {"type": "explorer"},
    },
    # launch type "mmc": a Microsoft Management Console snap-in. mmc.exe hosts every
    # console, so "is it running" means "is there an mmc.exe naming this .msc".
    {
        "id": "hyperv", "name": "Hyper-V Manager",
        "category": "Infrastructure", "icon": "hyperv",
        "description": "Windows Hyper-V console \u2014 manage local VMs",
        "script_key": "", "proc_name": "", "cwd_key": "",
        "dir": ".",
        "launch": {"type": "mmc", "msc": "virtmgmt.msc",
                   "feature": "Hyper-V Management Tools"},
    },
    {
        "id": "keep-awake", "name": "Keep Awake",
        "category": "Infrastructure", "icon": "moon",
        "description": "Prevents Windows from sleeping/locking during long builds or idle VPN/RDP sessions",
        "script_key": "keep-awake-typer.ps1", "proc_name": "powershell",
        "cwd_key": "windowpreventsleeeplock",
        "dir": "../WindowPreventSleeepLock",
        "launch": {"type": "ps1", "script": "keep-awake-typer.ps1", "args": []},
        # Force-killing this directly (the generic ps1 stop) leaves an orphaned
        # scratch Notepad window and temp file behind; its own stop script closes
        # them properly, so route Stop through that instead.
        "stop": {"type": "bat", "bat": "stop-keep-awake.bat"},
    },
]


# One snapshot of the process + socket tables, shared by every service check.
# Scanning per-service was O(services x processes) with a full TCP-table walk
# inside each iteration - that is what made the page sit on "Loading services...".
_SCAN_TTL   = 3.0
_SCAN_LOCK  = threading.Lock()
_scan_cache: dict = {"at": 0.0, "procs": [], "ports": set()}
_WATCHED    = ("python", "node", "uvicorn", "powershell")


def _scan_processes(force: bool = False) -> list[dict]:
    now = time.monotonic()
    with _SCAN_LOCK:
        cached = _scan_cache["procs"]
        if cached and not force and (now - _scan_cache["at"]) < _SCAN_TTL:
            return cached

    # every listening port, keyed by owning pid - ONE syscall for the whole box
    ports: dict[int, list[int]] = {}
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.pid:
                ports.setdefault(conn.pid, []).append(conn.laddr.port)
    except Exception:
        pass

    procs: list[dict] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not any(k in name for k in _WATCHED):
                continue
            # cmdline/cwd are the expensive attrs - only fetch them for candidates
            detail = proc.as_dict(attrs=["cmdline", "cwd"])
            procs.append({
                "pid": proc.info["pid"],
                "name": name,
                "cmdline": " ".join(detail.get("cmdline") or []).lower(),
                "cwd": (detail.get("cwd") or "").replace("\\", "/").lower(),
                "ports": sorted(ports.get(proc.info["pid"], [])),
            })
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception:
            continue

    with _SCAN_LOCK:
        _scan_cache["at"] = time.monotonic()
        _scan_cache["procs"] = procs
        # every listening port on the box, for services identified by port alone
        _scan_cache["ports"] = {q for plist in ports.values() for q in plist}
    return procs


def _get_service_status(
    script_key: str, cwd_key: str = "", proc_name: str = "python",
    port_hint: int | None = None,
) -> tuple[bool, int | None]:
    """Match a service against the cached snapshot; return (is_running, port)."""
    if not script_key and not cwd_key:  # explorer-type service - no process concept
        return False, None

    script_key = script_key.lower()
    cwd_key = cwd_key.lower()
    proc_name = (proc_name or "").lower()

    found = False
    candidate_ports: list[int] = []

    for proc in _scan_processes():
        if proc_name and proc_name not in proc["name"]:
            continue
        # match script_key in cmdline OR in cwd (catches node/npm apps)
        if script_key and script_key not in proc["cmdline"] and script_key not in proc["cwd"]:
            continue
        if cwd_key and cwd_key not in proc["cwd"]:
            continue
        found = True
        candidate_ports.extend(proc["ports"])

    if not found:
        return False, None
    if not candidate_ports:
        return True, None
    # a service can own several ports (API + UI); prefer the one worth opening
    if port_hint and port_hint in candidate_ports:
        return True, port_hint
    return True, min(candidate_ports)


# Where the dosewatch-all checkout lives — same candidates local-patch.sh uses,
# so the hub and the shell script never disagree about the repo.
_REPO_CANDIDATES = [
    Path.home() / "project" / "dosewatch-all",
    Path.home() / "OneDrive - GE HealthCare" / "Desktop" / "project" / "dosewatch-all",
    Path.home() / "project" / "Final" / "dosewatch-all",
]


def _find_repo() -> Path | None:
    for candidate in _REPO_CANDIDATES:
        if (candidate / ".git").is_dir():
            return candidate
    return None


def _resolve_app(launch: dict) -> Path | None:
    """First existing match for an app's exe_globs. Versioned install paths move
    with every IDE update, so glob rather than hard-code."""
    for pattern in launch.get("exe_globs", []):
        try:
            matches = sorted(glob.glob(pattern, recursive=True), reverse=True)
        except Exception:
            continue
        for match in matches:
            candidate = Path(match)
            if candidate.is_file():
                return candidate
    return None


def _port_listening(port: int) -> bool:
    """Is anything listening on this port? For a service that always binds the same
    port this beats scanning command lines - and it is the only cheap way to see a
    .NET HttpListener, whose socket belongs to http.sys rather than to its host."""
    _scan_processes()   # refreshes the shared socket snapshot only when stale
    with _SCAN_LOCK:
        return port in _scan_cache["ports"]


def _stop_by_script(names: tuple[str, ...], needle: str) -> int:
    """Kill the interpreter running a given script, matched on its command line.

    Deliberately NOT by who owns the port: an HttpListener's socket is held by
    http.sys inside the System process, so the port's owner is pid 4 - killing that
    is never what anyone meant. Restricting to `names` keeps this to the shells we
    launched ourselves, and the script filename keeps it to the right one of them."""
    if not needle:
        return 0
    needle = needle.lower()
    stopped = 0
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if (proc.info.get("name") or "").lower() not in names:
                continue
            if proc.info["pid"] == _os.getpid():
                continue
            if needle not in " ".join(proc.info.get("cmdline") or []).lower():
                continue
            try:
                parent = psutil.Process(proc.info["pid"])
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                parent.kill()
                stopped += 1
            except psutil.Error:
                continue
    except Exception:
        pass
    return stopped


def _find_powershell() -> str:
    """Windows PowerShell from System32 - on every Windows box, and never shadowed
    by the Store alias that a bare PATH lookup can turn up."""
    root = Path(_os.environ.get("SystemRoot", r"C:\Windows"))
    builtin = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if _is_real_exe(builtin):
        return str(builtin)
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found and _is_real_exe(Path(found)):
            return found
    return "powershell.exe"


def _resolve_msc(launch: dict) -> Path | None:
    """Locate an MMC snap-in. These only exist once the matching Windows feature is
    enabled - virtmgmt.msc is absent until the Hyper-V management tools are on."""
    name = launch.get("msc", "")
    if not name:
        return None
    root = Path(_os.environ.get("SystemRoot", r"C:\Windows"))
    for folder in ("System32", "SysWOW64"):
        candidate = root / folder / name
        if candidate.is_file():
            return candidate
    return None


def _mmc_running(msc: str) -> bool:
    """mmc.exe is shared by every console, so match on the snap-in it was given."""
    if not msc:
        return False
    needle = msc.lower()
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            if (proc.info.get("name") or "").lower() != "mmc.exe":
                continue
            if needle in " ".join(proc.info.get("cmdline") or []).lower():
                return True
    except Exception:
        pass
    return False


def _launch_mmc(svc: dict) -> None:
    """Open an MMC snap-in. No CREATE_NO_WINDOW here - mmc.exe *is* the window."""
    launch = svc["launch"]
    snap = _resolve_msc(launch)
    if snap is None:
        feature = launch.get("feature", launch.get("msc", "this console"))
        raise FileNotFoundError(
            f"{svc['name']} is not available on this machine - enable "
            f'"{feature}" in "Turn Windows features on or off"')
    mmc = Path(_os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "mmc.exe"
    subprocess.Popen([str(mmc), str(snap)], close_fds=True)


def _app_running(proc_name: str) -> bool:
    if not proc_name:
        return False
    target = proc_name.lower()
    for proc in _scan_processes():
        if proc["name"] == target:
            return True
    # apps aren't in the watched set (python/node), so check directly
    try:
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() == target:
                return True
    except Exception:
        pass
    return False


def _launch_app(svc: dict) -> None:
    """Start a desktop program, optionally opening the repo in it."""
    launch = svc["launch"]
    exe = _resolve_app(launch)

    if exe is None:
        fallback = launch.get("fallback")
        if fallback:
            bat = (BASE / fallback).resolve()
            if bat.exists():
                subprocess.Popen(
                    [_os.environ.get("COMSPEC", "cmd.exe"), "/c", str(bat)],
                    cwd=str(bat.parent), creationflags=CREATE_NO_WINDOW,
                )
                return
        raise FileNotFoundError(f"{svc['name']} is not installed on this machine")

    argv = [str(exe)]
    if launch.get("open_repo"):
        repo = _find_repo()
        if repo:
            argv.append(str(repo))

    subprocess.Popen(argv, cwd=str(exe.parent), close_fds=True)


def _run_custom_stop(svc: dict) -> int:
    """Run the service's own "stop" script instead of a bare process kill.

    Some services need real cleanup on shutdown beyond just dying - e.g. the
    keep-awake script leaves an orphaned scratch Notepad window and temp file
    behind if force-killed directly, but its own stop-keep-awake.bat closes
    them properly. Declaring "stop" in a service's config routes through here
    first; anything without one falls through to the generic kill below.
    """
    stop_cfg = svc.get("stop")
    if not stop_cfg:
        return 0
    cwd = (BASE / svc["dir"]).resolve()
    if stop_cfg.get("type") == "bat":
        bat = (cwd / stop_cfg["bat"]).resolve()
        if not bat.is_file():
            return 0
        try:
            subprocess.run(
                [_os.environ.get("COMSPEC", "cmd.exe"), "/c", str(bat)],
                cwd=str(cwd), timeout=30, creationflags=CREATE_NO_WINDOW,
                capture_output=True,
            )
            return 1
        except Exception:
            return 0
    return 0


def _stop_service(svc: dict) -> int:
    """Kill any running instance of this service. Without this, a second Launch
    leaves the old process holding the original port while the new one binds a
    different one, and the UI opens whichever it happened to find first."""
    launch = svc.get("launch", {})

    stopped = _run_custom_stop(svc)
    if stopped:
        return stopped

    if launch.get("type") in ("app", "mmc", "jar"):
        return 0   # never kill an editor, console, or viewer window the user already has open

    # A script host is a generic powershell.exe with nothing service-shaped in its
    # cwd, so the cwd-based matching below can't see it. Match the script instead.
    if launch.get("type") == "ps1":
        return _stop_by_script(("powershell.exe", "pwsh.exe"), launch.get("script", ""))

    script_key = (svc.get("script_key") or "").lower()
    cwd_key = (svc.get("cwd_key") or "").lower()
    proc_name = (svc.get("proc_name") or "").lower()
    if not script_key and not cwd_key:
        return 0

    # Killing is destructive, so match far more strictly than mere detection does:
    # the process must actually be running out of this service's directory.
    svc_dir = str((BASE / svc["dir"]).resolve()).replace("\\", "/").lower()

    stopped = 0
    for proc in _scan_processes(force=True):
        if proc["pid"] == _os.getpid():
            continue
        if not proc["cwd"] or not proc["cwd"].startswith(svc_dir):
            continue
        if proc_name and proc_name not in proc["name"]:
            continue
        if script_key and script_key not in proc["cmdline"] and script_key not in proc["cwd"]:
            continue
        if cwd_key and cwd_key not in proc["cwd"]:
            continue
        try:
            parent = psutil.Process(proc["pid"])
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            parent.kill()
            stopped += 1
        except psutil.Error:
            continue

    if stopped:
        _scan_cache["procs"] = []
    return stopped


def _is_real_exe(path: Path) -> bool:
    """Reject Microsoft Store App Execution Aliases. They live in WindowsApps, are
    0-byte reparse points, and silently fail to launch a script from a working
    directory — which is exactly how every service launch used to die."""
    try:
        if not path.is_file():
            return False
        if "windowsapps" in str(path).lower():
            return False
        return path.stat().st_size > 0
    except OSError:
        return False


def _find_javaw() -> str | None:
    """Locate javaw.exe (no console window) for launching a bundled .jar tool."""
    java_home = _os.environ.get("JAVA_HOME", "")
    if java_home:
        candidate = Path(java_home) / "bin" / "javaw.exe"
        if _is_real_exe(candidate):
            return str(candidate)
    found = shutil.which("javaw")
    if found and _is_real_exe(Path(found)):
        return found
    for pattern in (
        "C:/Program Files/Java/*/bin/javaw.exe",
        "C:/Program Files (x86)/Java/*/bin/javaw.exe",
        "C:/Program Files/Eclipse Adoptium/*/bin/javaw.exe",
        "C:/Program Files/Microsoft/jdk-*/bin/javaw.exe",
    ):
        for match in sorted(glob.glob(pattern), reverse=True):
            candidate = Path(match)
            if _is_real_exe(candidate):
                return str(candidate)
    return None


def _find_python(cwd: Path) -> str:
    """Return python.exe - deliberately NOT pythonw.exe.

    Under pythonw sys.stdout is None, so the first print() in a service raises
    AttributeError and the process dies on startup. We keep the window hidden with
    CREATE_NO_WINDOW instead, and hand the child a real log file to write to.
    Ordered most-specific to most-generic, skipping Store stubs at every step."""
    candidates: list[Path] = []

    # 1. the service's own virtualenv
    for venv_dir in (cwd / ".venv", cwd / "venv"):
        candidates.append(venv_dir / "Scripts" / "python.exe")

    # 2. the interpreter running this hub - guaranteed real, guaranteed present
    candidates.append(Path(_sys.executable))

    # 3. what install-service.ps1 verified (recorded as pythonw - take its sibling)
    try:
        recorded = (BASE / "_pythonw.txt").read_text(encoding="utf-8").strip()
        if recorded:
            candidates.append(Path(recorded).with_name("python.exe"))
    except OSError:
        pass

    # 4. PATH, as a last resort
    found = shutil.which("python")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if _is_real_exe(candidate):
            return str(candidate)

    return _sys.executable


def _launch_silent(svc: dict) -> Path | None:
    """Start a service hidden, with its output captured to logs/<id>.log.

    Spawns the child directly rather than bouncing through a generated .vbs - the
    old indirection swallowed every error, which is why a failed launch looked
    exactly like nothing happening. Returns the log path (None for folders)."""
    cwd = (BASE / svc["dir"]).resolve()
    launch = svc["launch"]

    if launch["type"] == "explorer":
        subprocess.Popen(["explorer.exe", str(cwd)], creationflags=CREATE_NO_WINDOW)
        return None

    if launch["type"] == "app":
        _launch_app(svc)
        return None

    if launch["type"] == "mmc":
        _launch_mmc(svc)
        return None

    if launch["type"] == "jar":
        javaw = _find_javaw()
        if javaw is None:
            raise FileNotFoundError("No Java runtime found (javaw.exe) - install a JRE to use this tool")
        jar_path = (cwd / launch["jar"]).resolve()
        if not jar_path.is_file():
            raise FileNotFoundError(f"Jar not found: {jar_path}")
        subprocess.Popen([javaw, "-jar", str(jar_path)], cwd=str(jar_path.parent), close_fds=True)
        return None

    if not cwd.exists():
        raise FileNotFoundError(f"Service directory not found: {cwd}")

    if launch["type"] == "python":
        argv = [_find_python(cwd)] + list(launch["args"])
    elif launch["type"] == "ps1":
        script = (cwd / launch["script"]).resolve()
        if not script.exists():
            raise FileNotFoundError(f"Launch script not found: {script}")
        argv = [_find_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script)] + list(launch.get("args", []))
    elif launch["type"] == "bat":
        bat = (cwd / launch["bat"]).resolve()
        if not bat.exists():
            raise FileNotFoundError(f"Launch script not found: {bat}")
        argv = [_os.environ.get("COMSPEC", "cmd.exe"), "/c", str(bat)]
    else:
        raise ValueError(f"Unknown launch type: {launch['type']}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{svc['id']}.log"
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    log.write(f"[hub] {datetime.now().isoformat()} launching {svc['id']}\n")
    log.write(f"[hub] cwd: {cwd}\n")
    log.write(f"[hub] cmd: {' '.join(argv)}\n\n")
    log.flush()

    try:
        subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log,            # a real handle, so print() inside the service works
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    finally:
        log.close()                # the child keeps its own inherited handle
    return log_path


# ── patches ──────────────────────────────────────────────────────────────────

PATCH_DIR = (BASE / ".." / "patches").resolve()
PATCH_SCRIPT = PATCH_DIR / "local-patch.sh"
_BASH_CANDIDATES = [
    Path(r"C:/Program Files/Git/bin/bash.exe"),
    Path(r"C:/Program Files (x86)/Git/bin/bash.exe"),
    Path.home() / "AppData/Local/Programs/Git/bin/bash.exe",
]


def _find_bash() -> Path | None:
    for candidate in _BASH_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("bash")
    return Path(found) if found else None


def _run_patch(action: str) -> dict:
    """Run local-patch.sh {status|apply|revert} through Git Bash and parse it.
    Same script patch.bat uses, so the UI and the terminal can't drift apart."""
    bash = _find_bash()
    if bash is None:
        raise HTTPException(500, "Git Bash not found - install Git for Windows")
    if not PATCH_SCRIPT.is_file():
        raise HTTPException(500, f"Patch script missing: {PATCH_SCRIPT}")

    repo = _find_repo()
    env = dict(_os.environ)
    if repo:
        env["REPO_ROOT"] = str(repo).replace("\\", "/")

    try:
        proc = subprocess.run(
            [str(bash), str(PATCH_SCRIPT), action],
            cwd=str(PATCH_DIR), env=env, capture_output=True, text=True,
            timeout=120, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Patch script timed out after 120s")

    output = (proc.stdout or "") + (proc.stderr or "")

    # lines look like "applied     : 01-foo.patch" / "NOT applied : 02-bar.patch"
    patches = []
    for line in output.splitlines():
        if ":" not in line:
            continue
        state, _, name = line.partition(":")
        state, name = state.strip(), name.strip()
        if not name.endswith(".patch"):
            continue
        low = state.lower()
        patches.append({
            "name": name,
            "state": ("applied" if low == "applied"
                      else "not-applied" if low.startswith("not")
                      else low.split()[0] if low else "unknown"),
            "detail": state,
        })

    return {
        "action": action,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "repo": str(repo) if repo else None,
        "output": output.strip(),
        "patches": patches,
    }


@app.get("/api/patches")
def patch_status():
    result = _run_patch("status")
    result["available"] = sorted(f.name for f in PATCH_DIR.glob("*.patch"))
    return result


@app.post("/api/patches/{action}")
def patch_action(action: str):
    if action not in ("apply", "revert", "status"):
        raise HTTPException(400, "action must be apply, revert or status")
    return _run_patch(action)


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/user")
def current_user():
    """Identity of the Windows profile running the hub (plus any saved override)."""
    return _user_payload()


@app.put("/api/user")
def set_user(user: UserIn):
    """Save the display name for this profile only, and mark setup complete."""
    name = user.display_name.strip()
    if not name:
        raise HTTPException(422, "display_name must not be empty")
    profile = _load_profile()
    profile["display_name"] = name
    profile["username"] = _login_name()
    profile.setdefault("created_at", datetime.now().isoformat())
    profile["updated_at"] = datetime.now().isoformat()
    profile["configured"] = True
    _save_profile(profile)
    return _user_payload()


@app.delete("/api/user")
def reset_user():
    """Drop the custom name and fall back to the Windows profile name."""
    profile = _load_profile()
    profile.pop("display_name", None)
    profile["configured"] = True  # still set up — just using the OS name
    profile["updated_at"] = datetime.now().isoformat()
    _save_profile(profile)
    return _user_payload()


@app.post("/api/user/signout")
def signout_user():
    """Forget this profile — the next page load shows the setup screen again."""
    _save_profile({})
    return _user_payload()


@app.get("/api/bookmarks")
def list_bookmarks():
    return _load()


@app.post("/api/bookmarks", status_code=201)
def add_bookmark(bm: BookmarkIn):
    data = _load()
    entry = {
        "id": str(uuid.uuid4()),
        "name": bm.name,
        "url": bm.url,
        "description": bm.description,
        "category": bm.category,
        "created_at": datetime.now().isoformat(),
    }
    data.append(entry)
    _save(data)
    return entry


@app.delete("/api/bookmarks/{bm_id}", status_code=204)
def delete_bookmark(bm_id: str):
    data = _load()
    new_data = [b for b in data if b["id"] != bm_id]
    if len(new_data) == len(data):
        raise HTTPException(404, "Bookmark not found")
    _save(new_data)


@app.get("/save")
def quick_save(
    url: str = Query(...),
    name: str = Query(""),
    description: str = Query(""),
    category: str = Query("General"),
):
    """Called by the browser bookmarklet — saves current page and redirects home."""
    data = _load()
    data.append({
        "id": str(uuid.uuid4()),
        "name": name or url,
        "url": url,
        "description": description,
        "category": category,
        "created_at": datetime.now().isoformat(),
    })
    _save(data)
    return RedirectResponse("/")


def _svc_payload(svc: dict) -> dict:
    launch = svc.get("launch", {})
    launch_type = launch.get("type", "python")
    installed = True
    if svc.get("fixed_port"):
        # Always the same port, so ownership of that port *is* the status.
        running = _port_listening(svc["fixed_port"])
        port = svc["fixed_port"] if running else None
    elif launch_type == "explorer":
        running, port = None, None
    elif launch_type == "app":
        exe = _resolve_app(launch)
        installed = exe is not None or bool(launch.get("fallback"))
        running, port = (_app_running(launch.get("proc", "")) if installed else False), None
    elif launch_type == "mmc":
        installed = _resolve_msc(launch) is not None
        running, port = (_mmc_running(launch.get("msc", "")) if installed else False), None
    elif launch_type == "jar":
        jar_path = (BASE / svc["dir"] / launch.get("jar", "")).resolve()
        installed = jar_path.is_file() and _find_javaw() is not None
        running, port = (_app_running(launch.get("proc", "javaw.exe")) if installed else False), None
    else:
        running, port = _get_service_status(
            svc["script_key"], svc.get("cwd_key", ""), svc.get("proc_name", "python"),
            svc.get("port_hint"),
        )
    return {
        "id": svc["id"],
        "name": svc["name"],
        "category": svc.get("category", "General"),
        "icon": svc.get("icon", "wrench"),
        "description": svc.get("description", ""),
        "running": running,
        "url": f"http://127.0.0.1:{port}" if port else None,
        "port": port,
        "launch_type": launch_type,
        "installed": installed,
        # how long the UI should wait for this one before giving up
        "timeout": svc.get("timeout", DEFAULT_LAUNCH_TIMEOUT),
    }


def _probe(url: str, timeout: float = 2.0) -> bool:
    """True once the service answers HTTP. A listening port alone isn't 'ready' —
    dev servers bind the socket seconds before they can serve a page."""
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # it responded — 404/500 still means the server is up
    except Exception:
        return False


@app.on_event("startup")
def _warm_scan() -> None:
    """Take the first process snapshot off the request path, so the very first
    /api/services call is served from a warm cache instead of waiting on a scan."""
    threading.Thread(target=_scan_processes, kwargs={"force": True}, daemon=True).start()


@app.get("/api/services")
def service_status(include_hidden: bool = False):
    hidden = _load_hidden()
    return [_svc_payload(svc) for svc in _SERVICES
            if include_hidden or svc["id"] not in hidden]


# Declared before /api/services/{svc_id} so the path param doesn't swallow "hidden".
@app.get("/api/services/hidden")
def hidden_services():
    """Removed services, so the UI can offer them back instead of losing them."""
    hidden = _load_hidden()
    return [{"id": s["id"], "name": s["name"], "category": s.get("category", "General")}
            for s in _SERVICES if s["id"] in hidden]


@app.delete("/api/services/{svc_id}", status_code=204)
def remove_service(svc_id: str):
    """Take a tile off this machine's board. Nothing is uninstalled - the entry is
    only a launcher, so removing it removes the shortcut and nothing else."""
    if not any(s["id"] == svc_id for s in _SERVICES):
        raise HTTPException(404, "Unknown service")
    hidden = _load_hidden()
    hidden.add(svc_id)
    _save_hidden(hidden)


@app.post("/api/services/{svc_id}/restore")
def restore_service(svc_id: str):
    svc = next((s for s in _SERVICES if s["id"] == svc_id), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    hidden = _load_hidden()
    if svc_id not in hidden:
        raise HTTPException(409, "Service is not hidden")
    hidden.discard(svc_id)
    _save_hidden(hidden)
    return _svc_payload(svc)


@app.get("/api/services/{svc_id}")
def one_service(svc_id: str):
    """Single service plus a live readiness probe — polled while a launch is in flight."""
    svc = next((s for s in _SERVICES if s["id"] == svc_id), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    payload = _svc_payload(svc)
    payload["ready"] = bool(payload["url"]) and _probe(payload["url"])
    return payload


@app.post("/api/services/{svc_id}/launch")
def launch_service(svc_id: str):
    svc = next((s for s in _SERVICES if s["id"] == svc_id), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    try:
        stopped = _stop_service(svc)   # idempotent: Launch and Restart behave alike
        if stopped:
            time.sleep(0.6)            # let the port actually release
        log_path = _launch_silent(svc)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
    _scan_cache["procs"] = []  # force a fresh scan on the next poll
    return {
        "launched": svc_id,
        "stopped": stopped,
        "timeout": svc.get("timeout", DEFAULT_LAUNCH_TIMEOUT),
        "log": log_path.name if log_path else None,
    }


@app.post("/api/services/{svc_id}/stop")
def stop_service(svc_id: str):
    """Stop a running service without relaunching it - the missing half of Launch.

    Without this, a stale instance from an earlier code version (or one left
    running by a manual start outside the hub) has no in-UI way to go away:
    Launch always restarts, and killing it required Task Manager. Same
    _stop_service() logic Launch already uses, so "is this stoppable" never
    disagrees between the two buttons.
    """
    svc = next((s for s in _SERVICES if s["id"] == svc_id), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    launch_type = svc.get("launch", {}).get("type")
    if launch_type in ("app", "mmc", "explorer", "jar"):
        raise HTTPException(409, f"{svc['name']} isn't something the hub can stop remotely.")
    try:
        stopped = _stop_service(svc)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
    _scan_cache["procs"] = []  # force a fresh scan on the next poll
    if not stopped:
        raise HTTPException(409, f"{svc['name']} does not appear to be running.")
    return {"stopped": stopped, "id": svc_id}


@app.get("/go/{svc_id}")
def go(svc_id: str):
    """Profile-first hop to a service. The tab lands on DevHub, shows who is signed
    in and what is starting, then navigates on once the service answers - so a
    launch never drops you on a dead port, and every hop is attributable."""
    if not any(s["id"] == svc_id for s in _SERVICES):
        raise HTTPException(404, "Unknown service")
    return FileResponse(STATIC_DIR / "go.html")


@app.get("/api/services/{svc_id}/log")
def service_log(svc_id: str, lines: int = 40):
    """Tail of the launch log — so a failure shows a reason instead of nothing."""
    svc = next((s for s in _SERVICES if s["id"] == svc_id), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    log_path = LOG_DIR / f"{svc_id}.log"
    if not log_path.exists():
        return {"id": svc_id, "log": "", "exists": False}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(500, str(exc))
    tail = "\n".join(text.splitlines()[-max(1, min(lines, 500)):])
    return {"id": svc_id, "log": tail, "exists": True}


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true", help="Skip auto-open (used by startup task)")
    args = ap.parse_args()

    print(f"[{APP_NAME}] Running at http://{HOST}:{PORT}")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
