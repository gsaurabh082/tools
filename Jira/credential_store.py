"""Windows DPAPI-backed storage for one local Jira credential."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


APP_DATA_DIR = Path(
    os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
) / "JiraFridayReport"
CREDENTIAL_FILE = APP_DATA_DIR / "credentials.bin"
GITLAB_CREDENTIAL_FILE = APP_DATA_DIR / "gitlab_credentials.bin"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Secure credential saving is supported on Windows only.")


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
        "Jira Friday Report",
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


def _save_to(path: Path, credentials: dict[str, str]) -> None:
    payload = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
    encrypted = _protect(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypted)


def _load_from(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        decrypted = _unprotect(path.read_bytes())
        value: Any = json.loads(decrypted.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and item is not None
    }


def _delete_from(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True


def save_credentials(credentials: dict[str, str]) -> None:
    _save_to(CREDENTIAL_FILE, credentials)


def load_credentials() -> dict[str, str] | None:
    return _load_from(CREDENTIAL_FILE)


def delete_credentials() -> bool:
    return _delete_from(CREDENTIAL_FILE)


def credential_metadata() -> dict[str, str | bool]:
    saved = load_credentials()
    if not saved or not saved.get("token"):
        return {"available": False}
    return {
        "available": True,
        "base_url": saved.get("base_url", ""),
        "username": saved.get("username", ""),
        "auth_type": saved.get("auth_type", "auto"),
    }


def save_gitlab_credentials(credentials: dict[str, str]) -> None:
    """Store the GitLab group URL and token, encrypted for this Windows account."""
    _save_to(GITLAB_CREDENTIAL_FILE, credentials)


def load_gitlab_credentials() -> dict[str, str] | None:
    return _load_from(GITLAB_CREDENTIAL_FILE)


def delete_gitlab_credentials() -> bool:
    return _delete_from(GITLAB_CREDENTIAL_FILE)


def gitlab_credential_metadata() -> dict[str, str | bool]:
    saved = load_gitlab_credentials()
    if not saved or not saved.get("token"):
        return {"available": False}
    return {
        "available": True,
        "group_url": saved.get("group_url", ""),
    }
