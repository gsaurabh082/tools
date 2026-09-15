#!/usr/bin/env python3
"""
dicom_send_web.py - a small browser-based UI for send-to-dicom.sh.

Wraps the existing, working send-to-dicom.sh (dcm4che DcmSnd) so you don't
have to type folder paths and AET strings in a terminal every time. Same
underlying logic as send-to-dicom.bat: finds Git Bash (preferred) or WSL and
runs send-to-dicom.sh with the folder/AET/SCP you pick.

Beyond the plain "send this folder" case it also does three things aimed at
not flattening whatever is listening on the other end:

  1. Throttled (paced) sending. Instead of handing the whole folder to one
     DcmSnd process - which pushes everything as fast as the network allows and
     is what creates the downstream backlog - the folder is split into series
     (each subfolder that directly contains files) and sent one series at a
     time, sleeping a random interval between them. Optionally the files inside
     each series are sent one at a time with their own random inter-file delay.
     Randomised rather than fixed intervals so the traffic doesn't land in a
     lockstep pattern that can itself resonate with downstream batch windows.

  2. Health-aware sending and retry. A DICOM C-ECHO (via dicom-echo.sh) is used
     as the liveness probe for the target. Optionally the sender waits for the
     target to answer an echo before each series, and retries a series that
     failed only once the target is answering again - so a struggling
     downstream gets breathing room instead of an immediate retry storm.

  3. Multiple targets from one screen. Any number of AET/host/port rows can be
     configured and the selected folder is sent to each enabled one in turn
     (sequentially - deliberately, since sending in parallel would multiply the
     very load this is trying to limit).

Setup (one time):
    pip install fastapi uvicorn

Run:
    python dicom_send_web.py
or double-click run_dicom_send_web.bat

This starts a local web server on http://127.0.0.1:8766 and opens it in your
browser automatically. It only runs commands on this machine - nothing here
talks to the internet except your browser talking to localhost.

Requires the same things send-to-dicom.sh already required: Git Bash (or
WSL) and a JDK on PATH, plus the dcm4che install path set correctly inside
send-to-dicom.sh and dicom-echo.sh.
"""
import ctypes
import json
import os
import random
import shutil
import socket
import string
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
SH_SCRIPT = BASE_DIR / "send-to-dicom.sh"
ECHO_SCRIPT = BASE_DIR / "dicom-echo.sh"
SETTINGS_FILE = BASE_DIR / "dicom_send_settings.json"
HOST = "127.0.0.1"
PORT = 8766  # preferred - overwritten with a free port at startup if taken

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 2001
DEFAULT_CALLED_AET = "DW_SCP"
DEFAULT_CALLING_AET = "ct01"

# Files that are never DICOM instances and would only waste a JVM launch.
SKIP_SUFFIXES = {
    ".txt", ".xml", ".json", ".csv", ".log", ".ini", ".db", ".zip", ".gz",
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".html", ".htm", ".md",
    ".exe", ".bat", ".sh", ".py",
}
SKIP_NAMES = {"dicomdir", "thumbs.db", ".ds_store"}

MAX_LOG_LINES = 4000


def find_free_port(preferred):
    """Try the preferred port first; if something else already owns it
    (e.g. some other local tool), ask the OS for any free port instead so
    the browser never ends up showing that other service by mistake."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


app = FastAPI(title="DICOM Send")


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #

class Target(BaseModel):
    """One downstream destination. `label` is cosmetic, for the progress list."""
    label: str = ""
    enabled: bool = True
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    called_aet: str = DEFAULT_CALLED_AET
    calling_aet: str = DEFAULT_CALLING_AET

    def scp(self) -> str:
        return f"{self.called_aet}@{self.host}:{self.port}"

    def name(self) -> str:
        return self.label.strip() or self.scp()


class Throttle(BaseModel):
    """Randomised pacing. Delays are drawn uniformly from [min, max] each time
    so successive sends don't fall into a fixed rhythm."""
    enabled: bool = False
    series_min_s: float = Field(2.0, ge=0)
    series_max_s: float = Field(8.0, ge=0)
    # Second level of pacing: also stagger the files within each series.
    file_delay_enabled: bool = False
    file_min_s: float = Field(0.2, ge=0)
    file_max_s: float = Field(1.5, ge=0)


class HealthCfg(BaseModel):
    """C-ECHO based gating and retry."""
    # Probe the target before each series and hold off while it isn't answering.
    gate_enabled: bool = False
    # Retry a series that failed to send, once the target answers again.
    retry_enabled: bool = False
    max_retries: int = Field(2, ge=0, le=10)
    retry_backoff_s: float = Field(5.0, ge=0)
    # How long to keep waiting for an unhealthy target before giving up on it.
    wait_attempts: int = Field(6, ge=1, le=100)
    wait_interval_s: float = Field(10.0, ge=1)
    # If the target never comes back: stop this target's run, or skip the
    # series and press on.
    abort_target_on_unhealthy: bool = True


class SendIn(BaseModel):
    folder: str
    targets: List[Target] = []
    throttle: Throttle = Throttle()
    health: HealthCfg = HealthCfg()
    # Legacy single-target fields, still accepted so an older UI/script that
    # POSTs the flat shape keeps working.
    host: Optional[str] = None
    port: Optional[int] = None
    called_aet: Optional[str] = None
    calling_aet: Optional[str] = None

    def resolved_targets(self) -> List[Target]:
        if self.targets:
            return [t for t in self.targets if t.enabled]
        return [Target(
            host=self.host or DEFAULT_HOST,
            port=self.port or DEFAULT_PORT,
            called_aet=self.called_aet or DEFAULT_CALLED_AET,
            calling_aet=self.calling_aet or DEFAULT_CALLING_AET,
        )]


class EchoIn(BaseModel):
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    called_aet: str = DEFAULT_CALLED_AET
    calling_aet: str = DEFAULT_CALLING_AET


class SettingsIn(BaseModel):
    targets: List[Target] = []
    throttle: Throttle = Throttle()
    health: HealthCfg = HealthCfg()


# --------------------------------------------------------------------------- #
# Shell plumbing (unchanged approach: Git Bash preferred, WSL fallback)
# --------------------------------------------------------------------------- #

def find_git_bash():
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("LocalAppData", "")) / "Programs" / "Git" / "bin" / "bash.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def wslpath_u(win_path):
    r = subprocess.run(["wsl", "wslpath", "-u", win_path], capture_output=True, text=True)
    return r.stdout.strip()


def _build_command(script: Path, path_args: List[str], plain_args: List[str]):
    """Returns (argv, error). path_args are filesystem paths that need
    translating for WSL; plain_args are passed through as-is."""
    if not script.exists():
        return None, f"Could not find {script.name} next to this script (expected at {script})."

    git_bash = find_git_bash()
    if git_bash:
        return [git_bash, str(script), *path_args, *plain_args], None

    if shutil.which("wsl"):
        wsl_script = wslpath_u(str(script))
        if not wsl_script:
            return None, "Found WSL but couldn't translate the script path for it."
        translated = []
        for p in path_args:
            t = wslpath_u(p)
            if not t:
                return None, f"Found WSL but couldn't translate the path for it: {p}"
            translated.append(t)
        return ["wsl", "bash", wsl_script, *translated, *plain_args], None

    return None, (
        "Could not find Git Bash or WSL. Install Git for Windows "
        "(https://git-scm.com/download/win) or enable WSL, then try again."
    )


class Cancelled(Exception):
    pass


def _run(argv, timeout=None, register=None):
    """Run argv, optionally handing the live Popen to `register` so a cancel
    request can kill it mid-flight."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if register:
        register(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return 124, out, (err or "") + "\n[timed out]"
    finally:
        if register:
            register(None)
    return proc.returncode, out, err


def run_send_script(path, calling_aet, scp, register=None, timeout=None):
    """Send one file or folder. Returns (returncode, stdout, stderr, error).
    error is set (and the rest None) if we couldn't even find a way to run
    the script."""
    argv, error = _build_command(SH_SCRIPT, [path], [calling_aet, scp])
    if error:
        return None, None, None, error
    rc, out, err = _run(argv, timeout=timeout, register=register)
    return rc, out, err, None


def run_echo_script(calling_aet, scp, register=None, timeout=30):
    """C-ECHO the target. Returns (ok, detail)."""
    argv, error = _build_command(ECHO_SCRIPT, [], [calling_aet, scp])
    if error:
        return False, error
    rc, out, err = _run(argv, timeout=timeout, register=register)
    detail = "\n".join(x.strip() for x in (out, err) if x and x.strip())
    return rc == 0, detail or f"exit code {rc}"


def list_drives():
    drives = []
    if os.name == "nt":
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    drives.append(f"{letter}:\\")
        except Exception:
            pass
    return drives


# --------------------------------------------------------------------------- #
# Planning: split a study folder into series-sized chunks
# --------------------------------------------------------------------------- #

def looks_like_instance(p: Path) -> bool:
    if not p.is_file():
        return False
    if p.name.lower() in SKIP_NAMES:
        return False
    if p.suffix.lower() in SKIP_SUFFIXES:
        return False
    return True


def plan_groups(root: Path) -> List[dict]:
    """Every directory at or under `root` that directly contains candidate
    instances becomes one group. For typical DICOM exports that is one group
    per series, which is the natural unit to pace on: a series is a coherent
    thing for the downstream to receive, and the gaps land between series
    rather than mid-series.

    Returns [{'path': str, 'name': str, 'files': [str], 'file_count': int}].
    """
    if root.is_file():
        return [{
            "path": str(root),
            "name": root.name,
            "files": [str(root)],
            "file_count": 1,
        }]

    groups = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        d = Path(dirpath)
        files = sorted(
            str(d / f) for f in sorted(filenames) if looks_like_instance(d / f)
        )
        if not files:
            continue
        try:
            rel = d.relative_to(root)
            name = str(rel) if str(rel) != "." else root.name
        except ValueError:
            name = str(d)
        groups.append({
            "path": str(d),
            "name": name,
            "files": files,
            "file_count": len(files),
        })
    return groups


# --------------------------------------------------------------------------- #
# The job engine
# --------------------------------------------------------------------------- #

class Job:
    """One paced run over (targets x series). Runs on its own thread; the HTTP
    handlers only ever read a snapshot under the lock."""

    def __init__(self, folder: str, targets: List[Target], throttle: Throttle, health: HealthCfg,
                 groups: List[dict]):
        self.folder = folder
        self.targets = targets
        self.throttle = throttle
        self.health = health
        self.groups = groups

        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._child: Optional[subprocess.Popen] = None

        self.state = "running"          # running | done | cancelled | error
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.log: List[str] = []
        self.current = ""               # human-readable "what is happening now"
        self.waiting_until: Optional[float] = None
        self.error: Optional[str] = None

        self.total_files = sum(g["file_count"] for g in groups)
        self.total_units = self.total_files * max(len(targets), 1)
        self.done_units = 0

        self.target_status: List[dict] = [{
            "name": t.name(),
            "scp": t.scp(),
            "calling_aet": t.calling_aet,
            "state": "pending",         # pending | running | done | failed | cancelled | skipped
            "groups_total": len(groups),
            "groups_done": 0,
            "groups_failed": 0,
            "groups_skipped": 0,
            "files_sent": 0,
            "files_failed": 0,
            "retries": 0,
            "health": "unknown",        # unknown | ok | down
        } for t in targets]

    # -- small helpers ----------------------------------------------------- #

    def say(self, msg: str, current: Optional[str] = None):
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.log.append(f"[{stamp}] {msg}")
            if len(self.log) > MAX_LOG_LINES:
                del self.log[: len(self.log) - MAX_LOG_LINES]
            if current is not None:
                self.current = current

    def set_target(self, idx: int, **kw):
        with self.lock:
            self.target_status[idx].update(kw)

    def bump_target(self, idx: int, **kw):
        with self.lock:
            for k, v in kw.items():
                self.target_status[idx][k] = self.target_status[idx].get(k, 0) + v

    def register_child(self, proc):
        with self.lock:
            self._child = proc
        # If a cancel arrived while this child was starting, kill it now.
        if proc is not None and self.cancel_event.is_set():
            try:
                proc.kill()
            except Exception:
                pass

    def cancel(self):
        self.cancel_event.set()
        with self.lock:
            child = self._child
        if child:
            try:
                child.kill()
            except Exception:
                pass

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def sleep(self, seconds: float, reason: str):
        """Interruptible sleep that also publishes a countdown for the UI."""
        if seconds <= 0:
            return
        with self.lock:
            self.waiting_until = time.time() + seconds
            self.current = reason
        try:
            if self.cancel_event.wait(seconds):
                raise Cancelled()
        finally:
            with self.lock:
                self.waiting_until = None

    def rand_delay(self, lo: float, hi: float) -> float:
        lo, hi = max(0.0, lo), max(0.0, hi)
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(lo, hi)

    # -- health ------------------------------------------------------------ #

    def echo(self, t: Target) -> tuple:
        return run_echo_script(t.calling_aet, t.scp(), register=self.register_child)

    def wait_for_health(self, idx: int, t: Target) -> bool:
        """Probe until the target answers, up to wait_attempts. Returns whether
        it ever came back."""
        for attempt in range(1, self.health.wait_attempts + 1):
            self.check_cancel()
            ok, detail = self.echo(t)
            if ok:
                self.set_target(idx, health="ok")
                if attempt > 1:
                    self.say(f"{t.name()}: responding again after {attempt} probe(s).")
                return True
            self.set_target(idx, health="down")
            self.say(
                f"{t.name()}: C-ECHO failed (probe {attempt}/{self.health.wait_attempts}) - "
                f"{detail.splitlines()[-1] if detail else 'no detail'}"
            )
            if attempt < self.health.wait_attempts:
                self.sleep(
                    self.health.wait_interval_s,
                    f"{t.name()} not responding - waiting {self.health.wait_interval_s:.0f}s before probing again",
                )
        return False

    # -- sending ----------------------------------------------------------- #

    def send_path(self, t: Target, path: str) -> tuple:
        rc, out, err, error = run_send_script(
            path, t.calling_aet, t.scp(), register=self.register_child
        )
        self.check_cancel()
        if error:
            return False, error
        detail = "\n".join(x.strip() for x in (out, err) if x and x.strip())
        return rc == 0, detail

    def send_group_once(self, idx: int, t: Target, group: dict) -> bool:
        """Send one series. With file-level pacing on, each instance goes as its
        own DcmSnd association with a random gap between them; otherwise the
        whole series folder goes in one association."""
        if self.throttle.enabled and self.throttle.file_delay_enabled:
            files = group["files"]
            failed = 0
            for i, f in enumerate(files, start=1):
                self.check_cancel()
                with self.lock:
                    self.current = f"{t.name()}: {group['name']} - file {i}/{len(files)}"
                ok, detail = self.send_path(t, f)
                if ok:
                    self.bump_target(idx, files_sent=1)
                else:
                    failed += 1
                    self.bump_target(idx, files_failed=1)
                    self.say(f"{t.name()}: FAILED {Path(f).name} - {(detail or '').splitlines()[-1] if detail else 'no detail'}")
                with self.lock:
                    self.done_units += 1
                if i < len(files):
                    d = self.rand_delay(self.throttle.file_min_s, self.throttle.file_max_s)
                    self.sleep(d, f"{t.name()}: pausing {d:.1f}s before next file in {group['name']}")
            return failed == 0

        with self.lock:
            self.current = f"{t.name()}: sending {group['name']} ({group['file_count']} files)"
        ok, detail = self.send_path(t, group["path"])
        with self.lock:
            self.done_units += group["file_count"]
        if ok:
            self.bump_target(idx, files_sent=group["file_count"])
        else:
            self.bump_target(idx, files_failed=group["file_count"])
            self.say(f"{t.name()}: FAILED {group['name']} - {(detail or '').splitlines()[-1] if detail else 'no detail'}")
        return ok

    def rewind_units(self, group: dict):
        """A retry re-sends files already counted, so take them back off the
        progress total rather than letting progress exceed 100%."""
        with self.lock:
            self.done_units = max(0, self.done_units - group["file_count"])

    def undo_counts(self, idx: int, group: dict, ok: bool):
        with self.lock:
            s = self.target_status[idx]
            if ok:
                s["files_sent"] = max(0, s["files_sent"] - group["file_count"])
            else:
                s["files_failed"] = max(0, s["files_failed"] - group["file_count"])

    def send_group(self, idx: int, t: Target, group: dict) -> str:
        """Returns 'ok' | 'failed' | 'skipped'."""
        # Gate: don't even start if the target isn't answering.
        if self.health.gate_enabled:
            with self.lock:
                self.current = f"{t.name()}: checking health before {group['name']}"
            ok, detail = self.echo(t)
            if ok:
                self.set_target(idx, health="ok")
            else:
                self.set_target(idx, health="down")
                self.say(f"{t.name()}: not responding before {group['name']} - holding off.")
                if not self.wait_for_health(idx, t):
                    return "unhealthy"

        ok = self.send_group_once(idx, t, group)
        if ok:
            return "ok"

        if not self.health.retry_enabled:
            return "failed"

        for attempt in range(1, self.health.max_retries + 1):
            self.check_cancel()
            # Wait for the target to actually be answering before retrying -
            # the point is to avoid hammering something that is already
            # struggling.
            self.say(f"{t.name()}: {group['name']} failed - waiting for target to respond before retry {attempt}/{self.health.max_retries}.")
            if not self.wait_for_health(idx, t):
                return "unhealthy"
            backoff = self.health.retry_backoff_s * attempt
            self.sleep(backoff, f"{t.name()}: backing off {backoff:.0f}s before retry {attempt} of {group['name']}")
            self.bump_target(idx, retries=1)
            self.rewind_units(group)
            self.undo_counts(idx, group, ok=False)
            if self.send_group_once(idx, t, group):
                self.say(f"{t.name()}: {group['name']} succeeded on retry {attempt}.")
                return "ok"
        return "failed"

    # -- main loop --------------------------------------------------------- #

    def run(self):
        try:
            self.say(
                f"Planned {len(self.groups)} series / {self.total_files} files across "
                f"{len(self.targets)} target(s)."
            )
            if self.throttle.enabled:
                self.say(
                    f"Pacing on: {self.throttle.series_min_s:g}-{self.throttle.series_max_s:g}s between series"
                    + (f", {self.throttle.file_min_s:g}-{self.throttle.file_max_s:g}s between files"
                       if self.throttle.file_delay_enabled else "")
                    + "."
                )
            else:
                self.say("Pacing off: each series is sent back-to-back.")

            for idx, t in enumerate(self.targets):
                self.check_cancel()
                self.set_target(idx, state="running")
                self.say(f"--- {t.name()} ({t.scp()}, calling AET {t.calling_aet}) ---")

                target_failed = False
                for gi, group in enumerate(self.groups, start=1):
                    self.check_cancel()
                    result = self.send_group(idx, t, group)

                    if result == "ok":
                        self.bump_target(idx, groups_done=1)
                    elif result == "failed":
                        target_failed = True
                        self.bump_target(idx, groups_done=1, groups_failed=1)
                    else:  # unhealthy
                        if self.health.abort_target_on_unhealthy:
                            remaining = len(self.groups) - gi + 1
                            self.say(f"{t.name()}: still not responding - abandoning this target ({remaining} series not sent).")
                            self.bump_target(idx, groups_skipped=remaining)
                            with self.lock:
                                self.done_units += sum(g["file_count"] for g in self.groups[gi - 1:])
                            target_failed = True
                            break
                        self.say(f"{t.name()}: skipping {group['name']} - target not responding.")
                        self.bump_target(idx, groups_skipped=1)
                        with self.lock:
                            self.done_units += group["file_count"]
                        target_failed = True
                        continue

                    # Gap between series - the main throttle.
                    if self.throttle.enabled and gi < len(self.groups):
                        d = self.rand_delay(self.throttle.series_min_s, self.throttle.series_max_s)
                        self.sleep(d, f"{t.name()}: pausing {d:.1f}s before next series")

                with self.lock:
                    s = self.target_status[idx]
                    s["state"] = "failed" if target_failed else "done"
                self.say(f"{t.name()}: finished ({'with failures' if target_failed else 'all series sent'}).")

                # Also pace between targets, so the next destination doesn't
                # start the instant the previous one finished.
                if self.throttle.enabled and idx < len(self.targets) - 1:
                    d = self.rand_delay(self.throttle.series_min_s, self.throttle.series_max_s)
                    self.sleep(d, f"Pausing {d:.1f}s before the next target")

            with self.lock:
                self.state = "done"
                self.current = ""
        except Cancelled:
            with self.lock:
                self.state = "cancelled"
                self.current = ""
                for s in self.target_status:
                    if s["state"] in ("running", "pending"):
                        s["state"] = "cancelled"
            self.say("Cancelled.")
        except Exception as e:  # noqa: BLE001 - surface anything unexpected in the UI
            with self.lock:
                self.state = "error"
                self.error = f"{type(e).__name__}: {e}"
                self.current = ""
            self.say(f"Unexpected error: {type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.finished_at = time.time()

    # -- snapshot for the UI ----------------------------------------------- #

    def snapshot(self, log_from: int = 0) -> dict:
        with self.lock:
            wait_left = None
            if self.waiting_until:
                wait_left = max(0.0, self.waiting_until - time.time())
            return {
                "state": self.state,
                "folder": self.folder,
                "current": self.current,
                "waiting_seconds_left": round(wait_left, 1) if wait_left is not None else None,
                "error": self.error,
                "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
                "total_files": self.total_files,
                "total_units": self.total_units,
                "done_units": min(self.done_units, self.total_units),
                "percent": round(100.0 * self.done_units / self.total_units, 1) if self.total_units else 100.0,
                "groups": len(self.groups),
                "targets": [dict(s) for s in self.target_status],
                "log_from": log_from,
                "log": self.log[log_from:],
                "log_len": len(self.log),
            }


_job_lock = threading.Lock()
_job: Optional[Job] = None


# --------------------------------------------------------------------------- #
# Settings persistence (so the target list survives a restart)
# --------------------------------------------------------------------------- #

def load_settings() -> Optional[dict]:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_settings(data: dict) -> Optional[str]:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return None
    except Exception as e:
        return str(e)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/defaults")
def defaults():
    saved = load_settings()
    base = {
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "called_aet": DEFAULT_CALLED_AET,
        "calling_aet": DEFAULT_CALLING_AET,
        "targets": [Target().model_dump()],
        "throttle": Throttle().model_dump(),
        "health": HealthCfg().model_dump(),
        "echo_available": ECHO_SCRIPT.exists(),
    }
    if saved:
        for key in ("targets", "throttle", "health"):
            if key in saved and saved[key]:
                base[key] = saved[key]
    return base


@app.post("/api/settings")
def settings(payload: SettingsIn):
    err = save_settings(payload.model_dump())
    return {"ok": err is None, "error": err}


@app.get("/api/browse")
def browse(path: Optional[str] = None):
    if not path:
        home = Path.home()
        shortcuts = {
            "Home": str(home),
            "Desktop": str(home / "Desktop"),
            "Downloads": str(home / "Downloads"),
        }
        shortcuts = {k: v for k, v in shortcuts.items() if Path(v).exists()}
        return {"ok": True, "path": None, "parent": None, "shortcuts": shortcuts, "drives": list_drives(), "entries": []}

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": f"Not a folder: {path}"}

    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child)})
    except PermissionError:
        return {"ok": False, "error": f"No permission to read: {path}"}

    parent = str(p.parent) if p.parent != p else None
    return {"ok": True, "path": str(p), "parent": parent, "shortcuts": {}, "drives": [], "entries": entries}


@app.get("/api/plan")
def plan(folder: str):
    """What the pacer would do with this folder - shown in the UI before you
    commit to a send, so the number of series and the resulting spread of
    delays isn't a surprise."""
    folder = folder.strip()
    if not folder:
        return {"ok": False, "error": "Folder is required."}
    p = Path(folder)
    if not p.exists():
        return {"ok": False, "error": f"Folder does not exist: {folder}"}
    groups = plan_groups(p)
    return {
        "ok": True,
        "groups": len(groups),
        "files": sum(g["file_count"] for g in groups),
        "preview": [{"name": g["name"], "file_count": g["file_count"]} for g in groups[:50]],
        "truncated": len(groups) > 50,
    }


@app.post("/api/echo")
def echo(payload: EchoIn):
    """Manual health probe for one target - the 'Test' button on each row."""
    scp = f"{payload.called_aet}@{payload.host}:{payload.port}"
    started = time.time()
    ok, detail = run_echo_script(payload.calling_aet, scp)
    return {
        "ok": ok,
        "scp": scp,
        "ms": int((time.time() - started) * 1000),
        "detail": detail,
    }


@app.post("/api/send")
def send(payload: SendIn):
    global _job
    folder = payload.folder.strip()
    if not folder:
        return {"ok": False, "error": "Folder is required."}
    if not Path(folder).exists():
        return {"ok": False, "error": f"Folder does not exist: {folder}"}

    targets = payload.resolved_targets()
    if not targets:
        return {"ok": False, "error": "Enable at least one target."}
    for t in targets:
        if not t.called_aet.strip() or not t.calling_aet.strip() or not t.host.strip():
            return {"ok": False, "error": "Every enabled target needs a host, a called AET and a calling AET."}

    groups = plan_groups(Path(folder))
    if not groups:
        return {"ok": False, "error": f"No files found to send under: {folder}"}

    with _job_lock:
        if _job and _job.state == "running":
            return {"ok": False, "error": "A send is already running. Cancel it or wait for it to finish."}
        job = Job(folder, targets, payload.throttle, payload.health, groups)
        _job = job

    threading.Thread(target=job.run, name="dicom-send-job", daemon=True).start()
    return {
        "ok": True,
        "started": True,
        "groups": len(groups),
        "files": sum(g["file_count"] for g in groups),
        "targets": [t.name() for t in targets],
    }


@app.get("/api/job")
def job_status(log_from: int = 0):
    with _job_lock:
        job = _job
    if not job:
        return {"ok": True, "state": "idle"}
    snap = job.snapshot(log_from=log_from)
    snap["ok"] = True
    return snap


@app.post("/api/cancel")
def cancel():
    with _job_lock:
        job = _job
    if not job or job.state != "running":
        return {"ok": False, "error": "Nothing is running."}
    job.cancel()
    return {"ok": True}


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    PORT = find_free_port(PORT)
    print(f"DICOM Send UI: http://{HOST}:{PORT}")
    threading.Timer(1.0, open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
