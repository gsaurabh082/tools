from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonListStore:
    """Tiny JSON-file-backed list store, keyed by "id", one lock per file."""

    def __init__(self, path: Path, *, max_items: int | None = None) -> None:
        self.path = path
        self.max_items = max_items
        self._lock = asyncio.Lock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._read)

    async def get(self, item_id: str) -> dict[str, Any] | None:
        items = await self.list()
        return next((item for item in items if item.get("id") == item_id), None)

    async def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            items = await asyncio.to_thread(self._read)
            for index, existing in enumerate(items):
                if existing.get("id") == item["id"]:
                    items[index] = item
                    break
            else:
                items.append(item)
            await asyncio.to_thread(self._write, items)
            return item

    async def delete(self, item_id: str) -> bool:
        async with self._lock:
            items = await asyncio.to_thread(self._read)
            filtered = [item for item in items if item.get("id") != item_id]
            if len(filtered) == len(items):
                return False
            await asyncio.to_thread(self._write, filtered)
            return True

    async def append(self, item: dict[str, Any]) -> None:
        async with self._lock:
            items = await asyncio.to_thread(self._read)
            items.append(item)
            if self.max_items is not None and len(items) > self.max_items:
                items = items[-self.max_items :]
            await asyncio.to_thread(self._write, items)

    async def replace_all(self, items: list[dict[str, Any]]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write, items)
