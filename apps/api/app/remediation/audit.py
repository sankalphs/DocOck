"""Append-only remediation audit trail (`runtime/audit/actions.jsonl`).

Same contract as the fault audit: the database rows are the queryable record,
this file is the durable one. Every plan, decision, dry-run, refusal,
execution and rollback writes one line with the actor and the outcome.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)


class ActionAuditEntry(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    event: str
    incident: str | None = None
    approval_id: int | None = None
    action_row_id: int | None = None
    action_id: str | None = None
    target: str | None = None
    outcome: str
    detail: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class ActionAuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.remediation_audit_file
        self._lock = asyncio.Lock()

    async def append(self, entry: ActionAuditEntry) -> None:
        try:
            async with self._lock:
                await asyncio.to_thread(self._append_sync, entry)
        except Exception as exc:  # the audit file must never break a decision
            log.error("action_audit_failed", error=str(exc))

    def _append_sync(self, entry: ActionAuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    async def tail(
        self, limit: int = 100, *, incident: str | None = None
    ) -> list[ActionAuditEntry]:
        return await asyncio.to_thread(self._tail_sync, limit, incident)

    def _tail_sync(self, limit: int, incident: str | None) -> list[ActionAuditEntry]:
        if not self.path.exists():
            return []
        entries: list[ActionAuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                entry = ActionAuditEntry.model_validate_json(line)
            except ValueError:
                continue  # a torn last line must not break the page
            if incident is None or entry.incident == incident:
                entries.append(entry)
        return entries[-max(1, min(limit, 1000)) :]


__all__ = ["ActionAuditEntry", "ActionAuditLog"]
