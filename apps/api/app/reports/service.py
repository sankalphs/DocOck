"""Report storage: build once an incident is closed, serve the stored copy.

An open incident's report is always built live. A RESOLVED or ESCALATED
incident's report is stored in `reports` (one row per format) when the
verifier concludes or on first read, so the export stays identical even after
the audit file rotates or the stack is rebuilt. `refresh=true` rebuilds it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, Incident, Report, Verification
from app.db.session import SessionFactory
from app.domain.state import TERMINAL_STATUSES, IncidentStatus
from app.events.bus import bus
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.reports.builder import build
from app.reports.markdown import render

log = get_logger(__name__)

FORMATS = ("json", "md")


class ReportSummary(BaseModel):
    incident_ref: str
    title: str
    status: str
    severity: str
    primary_service: str | None
    opened_at: datetime
    closed_at: datetime | None
    action: str | None
    verdict: str | None
    stored_at: datetime | None


class ReportList(BaseModel):
    items: list[ReportSummary]
    generated_at: datetime


def _terminal(incident: Incident) -> bool:
    return IncidentStatus(incident.status) in TERMINAL_STATUSES


class ReportService:
    async def _stored(self, session: AsyncSession, incident: Incident, fmt: str) -> str | None:
        return await session.scalar(
            select(Report.content).where(Report.incident_id == incident.id, Report.format == fmt)
        )

    async def _store(
        self, session: AsyncSession, incident: Incident, document: dict[str, Any]
    ) -> None:
        contents = {"json": json.dumps(document, default=str), "md": render(document)}
        for fmt, content in contents.items():
            statement = insert(Report).values(incident_id=incident.id, format=fmt, content=content)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["incident_id", "format"],
                    set_={"content": content, "created_at": func.now()},
                )
            )

    async def document(self, ref: str, *, refresh: bool = False) -> dict[str, Any]:
        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            if _terminal(incident) and not refresh:
                stored = await self._stored(session, incident, "json")
                if stored is not None:
                    return dict(json.loads(stored))
            document = await build(session, incident)
            if _terminal(incident):
                await self._store(session, incident, document)
                await session.commit()
            return document

    async def markdown(self, ref: str, *, refresh: bool = False) -> str:
        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            if _terminal(incident) and not refresh:
                stored = await self._stored(session, incident, "md")
                if stored is not None:
                    return stored
        return render(await self.document(ref, refresh=refresh))

    async def generate(self, ref: str) -> None:
        """Verifier hook: store the final report as soon as the loop closes."""
        try:
            await self.document(ref, refresh=True)
            bus.publish("report.generated", {"incident_id": ref})
        except Exception as exc:
            log.warning("report_generation_failed", incident=ref, error=str(exc))

    async def list(self, *, limit: int = 50) -> ReportList:
        async with SessionFactory() as session:
            incidents = (
                (
                    await session.execute(
                        select(Incident).order_by(Incident.opened_at.desc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            ids = [row.id for row in incidents] or [0]
            latest: dict[int, tuple[str, str | None]] = {}
            for action, verdict in (
                await session.execute(
                    select(Action, Verification.verdict)
                    .outerjoin(Verification, Verification.action_row_id == Action.id)
                    .where(Action.incident_id.in_(ids))
                    .order_by(Action.id)
                )
            ).all():
                latest[action.incident_id] = (f"{action.action_id} on {action.target}", verdict)
            stored: dict[int, datetime] = {
                incident_id: created_at
                for incident_id, created_at in (
                    await session.execute(
                        select(Report.incident_id, Report.created_at).where(
                            Report.incident_id.in_(ids), Report.format == "json"
                        )
                    )
                ).all()
            }
            items = [
                ReportSummary(
                    incident_ref=row.public_id,
                    title=row.title,
                    status=row.status,
                    severity=row.severity,
                    primary_service=row.primary_service,
                    opened_at=row.opened_at,
                    closed_at=row.closed_at,
                    action=latest.get(row.id, (None, None))[0],
                    verdict=latest.get(row.id, (None, None))[1],
                    stored_at=stored.get(row.id),
                )
                for row in incidents
            ]
        return ReportList(items=items, generated_at=datetime.now(UTC))


reports = ReportService()

__all__ = ["FORMATS", "ReportList", "ReportService", "ReportSummary", "reports"]
