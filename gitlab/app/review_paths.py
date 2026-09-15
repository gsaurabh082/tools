"""Persisted, per-project local checkout folders for code review.

Kept in a small JSON file next to the app so a project whose folder name does
not match its GitLab slug can be pointed at the right directory once, then
reused on every later review.
"""

from __future__ import annotations

import json
from pathlib import Path


def _load(store: Path) -> dict:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_override(store: Path, project_id: int) -> str | None:
    projects = _load(store).get("projects") or {}
    value = projects.get(str(project_id))
    return value or None


def set_override(store: Path, project_id: int, folder: str) -> None:
    data = _load(store)
    projects = data.setdefault("projects", {})
    if folder:
        projects[str(project_id)] = folder
    else:
        projects.pop(str(project_id), None)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data, indent=2), encoding="utf-8")
