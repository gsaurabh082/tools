"""Windows DPAPI-backed storage for RDP Host Manager's saved hosts.

Hosts (alias, IP, username, password) are kept as one JSON blob encrypted
with CryptProtectData, tied to the current Windows user account - the same
approach the Jira report tool uses for its API token. Nothing is ever
written to disk in plain text.
"""

from __future__ import annotations

import ctypes
import json
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_DATA_DIR = Path(
    os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
) / "RDPHostManager"
HOSTS_FILE = APP_DATA_DIR / "hosts.bin"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Secure host storage is supported on Windows only.")


def _protect(data: bytes) -> bytes:
    _require_windows()
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(
        len(data),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "RDP Host Manager",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect(data: bytes) -> bytes:
    _require_windows()
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(
        len(data),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def load_hosts() -> list[dict[str, str]]:
    if not HOSTS_FILE.exists():
        return []
    try:
        decrypted = _unprotect(HOSTS_FILE.read_bytes())
        value: Any = json.loads(decrypted.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [
        item for item in value
        if isinstance(item, dict) and item.get("id") and item.get("ip")
    ]


def save_hosts(hosts: list[dict[str, str]]) -> None:
    payload = json.dumps(hosts, ensure_ascii=False).encode("utf-8")
    encrypted = _protect(payload)
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOSTS_FILE.write_bytes(encrypted)


def new_host_id() -> str:
    return uuid.uuid4().hex[:12]
