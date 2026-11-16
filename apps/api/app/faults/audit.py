"""Append-only fault audit trail.

The database row is the queryable record; this file is the durable one. It
survives a database reset, is easy to tail during a demo, and holds the
before/after image of every write together with the actor and outcome.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)


# Several services keep their own log object on the same file (approvals, the
# executor and the verifier all write actions.jsonl). An asyncio.Lock per object
# would not serialise them, and a line larger than the write buffer can then
# interleave with another. One thread lock per resolved path does.
_FILE_LOCKS: dict[Path, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.Lock:
    key = path.resolve()
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


class AuditEntry(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    action: str
    fault_row_id: int | None = None
    fault_id: str | None = None
    target: str | None = None
    outcome: str
    detail: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.fault_audit_file
        self._lock = asyncio.Lock()

    async def append(self, entry: AuditEntry) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append_sync, entry)

    def _append_sync(self, entry: AuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
        with _file_lock(self.path), self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    async def tail(self, limit: int = 100) -> list[AuditEntry]:
        return await asyncio.to_thread(self._tail_sync, limit)

    def _tail_sync(self, limit: int) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        entries: list[AuditEntry] = []
        for line in lines[-max(1, min(limit, 1000)) :]:
            try:
                entries.append(AuditEntry.model_validate_json(line))
            except ValueError:
                continue  # a torn last line must not break the page
        return entries


__all__ = ["AuditEntry", "AuditLog"]
