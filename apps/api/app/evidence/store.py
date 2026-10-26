"""Immutable, incident-scoped evidence with stable E0001 codes.

Two properties matter more than anything else here:

- **Stable**: rerunning the analysis never renumbers an existing observable. A
  row is identified by a hash of (type, source, service, data); the timestamp is
  deliberately excluded so a rerun with a new `at` is still a dedupe.
- **Unique**: the next code is `max(existing numeric code) + 1`, and the
  `(incident_id, code)` unique constraint backs that up.

The agent may cite only codes returned by this store (the validator in P11).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, Incident

CODE_RE = re.compile(r"^E\d{4}$")


def format_code(number: int) -> str:
    return f"E{number:04d}"


def parse_code(code: str) -> int | None:
    if not CODE_RE.match(code):
        return None
    return int(code[1:])


def is_valid_code(code: str) -> bool:
    return parse_code(code) is not None


def evidence_hash(*, type_: str, source: str, service: str | None, data: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": type_, "source": source, "service": service, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceStore:
    def __init__(self) -> None:
        # Writers (the analyzer, the agent's tools) hold this while allocating
        # codes: two writers would both compute the same next E#### and the
        # second would fail on the (incident_id, code) unique constraint.
        self.lock = asyncio.Lock()

    async def add(
        self,
        session: AsyncSession,
        incident: Incident,
        *,
        type_: str,
        source: str,
        service: str | None,
        data: dict[str, Any],
        at: datetime | None = None,
    ) -> tuple[Evidence, bool]:
        digest = evidence_hash(type_=type_, source=source, service=service, data=data)
        existing = await session.scalar(
            select(Evidence).where(Evidence.incident_id == incident.id, Evidence.hash == digest)
        )
        if existing is not None:
            return existing, False

        next_number = (
            await session.scalar(
                select(Evidence.code)
                .where(Evidence.incident_id == incident.id)
                .order_by(Evidence.code.desc())
                .limit(1)
            )
            or "E0000"
        )
        number = (parse_code(next_number) or 0) + 1
        row = Evidence(
            incident_id=incident.id,
            code=format_code(number),
            type=type_,
            source=source,
            service=service,
            at=at or datetime.now(UTC),
            data=data,
            hash=digest,
        )
        session.add(row)
        await session.flush()
        return row, True

    async def list(self, session: AsyncSession, incident: Incident) -> list[Evidence]:
        return list(
            (
                await session.execute(
                    select(Evidence)
                    .where(Evidence.incident_id == incident.id)
                    .order_by(Evidence.code)
                )
            )
            .scalars()
            .all()
        )

    async def by_code(
        self, session: AsyncSession, incident: Incident, code: str
    ) -> Evidence | None:
        return await session.scalar(
            select(Evidence).where(Evidence.incident_id == incident.id, Evidence.code == code)
        )


store = EvidenceStore()

__all__ = [
    "CODE_RE",
    "EvidenceStore",
    "evidence_hash",
    "format_code",
    "is_valid_code",
    "parse_code",
    "store",
]
