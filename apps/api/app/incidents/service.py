"""Incident persistence and the only writer of incident events.

The state machine lives in `app/domain/state.py`; this module applies it,
allocates public ids (`INC-00042`), appends the append-only timeline, and
publishes SSE. Everything that changes an incident goes through here, including
the detector (P8), the agent (P11), and remediation (P14).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, Approval, Evidence, Fault, Incident, IncidentEvent, Suspect
from app.domain.state import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    IncidentStatus,
    close_target,
    ensure_transition,
    recovery_target,
)
from app.errors import ApiError
from app.events.bus import bus
from app.logging import get_logger

log = get_logger(__name__)


class IncidentEventView(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime


class EvidenceView(BaseModel):
    id: int
    code: str
    type: str
    source: str
    service: str | None
    at: datetime
    data: dict[str, Any]
    hash: str


class SuspectView(BaseModel):
    service: str
    score: float
    rank: int
    signals: dict[str, Any]


class FaultContext(BaseModel):
    id: int
    fault_id: str
    target: str
    params: dict[str, Any]
    status: str
    injected_at: datetime
    reset_at: datetime | None


class IncidentSummary(BaseModel):
    id: int
    public_id: str
    status: IncidentStatus
    severity: str
    title: str
    primary_service: str | None
    signature: str | None
    opened_at: datetime
    closed_at: datetime | None
    duration_s: int | None
    services: list[str]
    signals: list[dict[str, Any]]
    event_count: int = 0
    evidence_count: int = 0
    suspect_count: int = 0


class IncidentDetail(IncidentSummary):
    trigger: dict[str, Any] = Field(default_factory=dict)
    events: list[IncidentEventView] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    suspects: list[SuspectView] = Field(default_factory=list)
    faults: list[FaultContext] = Field(default_factory=list)


class IncidentList(BaseModel):
    items: list[IncidentSummary]
    total: int
    open: int
    generated_at: datetime


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _summary(incident: Incident, *, now: datetime | None = None) -> IncidentSummary:
    now = now or datetime.now(UTC)
    opened = _utc(incident.opened_at)
    closed = _utc(incident.closed_at) if incident.closed_at else None
    trigger = incident.trigger or {}
    return IncidentSummary(
        id=incident.id,
        public_id=incident.public_id,
        status=IncidentStatus(incident.status),
        severity=incident.severity,
        title=incident.title,
        primary_service=incident.primary_service,
        signature=incident.signature,
        opened_at=opened,
        closed_at=closed,
        duration_s=int(((closed or now) - opened).total_seconds()),
        services=[str(item) for item in (trigger.get("services") or [])],
        signals=[dict(item) for item in (trigger.get("signals") or []) if isinstance(item, dict)],
    )


def _event_view(event: IncidentEvent) -> IncidentEventView:
    return IncidentEventView(
        seq=event.seq, kind=event.kind, payload=dict(event.payload or {}), at=_utc(event.at)
    )


class IncidentService:
    async def create(
        self,
        session: AsyncSession,
        *,
        service: str,
        signature: str,
        severity: str,
        title: str,
        trigger: dict[str, Any],
    ) -> Incident:
        incident = Incident(
            # VARCHAR(16) placeholder; replaced with INC-00042 after the
            # autoincrement id is known.
            public_id=uuid4().hex[:12],
            status=IncidentStatus.OPEN.value,
            severity=severity,
            title=title[:240],
            primary_service=service,
            signature=signature,
            trigger=trigger,
            opened_at=datetime.now(UTC),
        )
        session.add(incident)
        await session.flush()
        incident.public_id = f"INC-{incident.id:05d}"
        await session.flush()
        await self._append(session, incident, "opened", {"severity": severity, **trigger})
        await session.refresh(incident)
        bus.publish("incident.opened", _summary(incident).model_dump(mode="json"))
        log.info(
            "incident_opened",
            incident=incident.public_id,
            service=service,
            signature=signature,
            severity=severity,
        )
        return incident

    async def append_event(
        self,
        session: AsyncSession,
        incident: Incident,
        kind: str,
        payload: dict[str, Any],
        *,
        publish: bool = True,
    ) -> IncidentEvent:
        await self._lock(session, incident)
        event = await self._append(session, incident, kind, payload)
        await session.refresh(incident)
        if publish:
            bus.publish(
                "incident.signal" if kind == "signal" else "incident.transition",
                {
                    "incident_id": incident.public_id,
                    "id": incident.id,
                    "seq": event.seq,
                    "kind": kind,
                    **payload,
                },
            )
        return event

    async def attach_signal(
        self,
        session: AsyncSession,
        incident: Incident,
        *,
        service: str,
        signal: str,
        value: float | None,
        baseline: float | None,
        threshold: float | None,
        detail: str,
    ) -> None:
        trigger = dict(incident.trigger or {})
        services = [str(item) for item in (trigger.get("services") or [])]
        if service not in services:
            services.append(service)
        signals = [dict(item) for item in (trigger.get("signals") or []) if isinstance(item, dict)]
        signature = f"{signal}:{service}"
        if not any(item.get("signature") == signature for item in signals):
            signals.append(
                {
                    "service": service,
                    "signal": signal,
                    "signature": signature,
                    "value": value,
                    "baseline": baseline,
                    "threshold": threshold,
                    "detail": detail,
                }
            )
        trigger["services"] = services
        trigger["signals"] = signals
        incident.trigger = trigger
        await session.flush()
        await self.append_event(
            session,
            incident,
            "signal",
            {
                "service": service,
                "signal": signal,
                "value": value,
                "baseline": baseline,
                "threshold": threshold,
                "detail": detail,
            },
        )

    async def transition(
        self,
        session: AsyncSession,
        incident: Incident,
        target: IncidentStatus,
        *,
        kind: str = "status",
        payload: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> Incident:
        await self._lock(session, incident)
        current = IncidentStatus(incident.status)
        ensure_transition(current, target)
        if current == IncidentStatus.AWAITING_APPROVAL and target in TERMINAL_STATUSES:
            await self.withdraw_pending(session, incident, reason=f"incident {target.value}")
        incident.status = target.value
        if target in TERMINAL_STATUSES:
            incident.closed_at = datetime.now(UTC)
        await session.flush()
        await self._append(
            session,
            incident,
            kind,
            {"from": current.value, "to": target.value, "actor": actor, **(payload or {})},
        )
        await session.refresh(incident)
        bus.publish(
            "incident.transition",
            {
                "incident_id": incident.public_id,
                "id": incident.id,
                "from": current.value,
                "to": target.value,
                "kind": kind,
                "actor": actor,
                **(payload or {}),
            },
        )
        log.info(
            "incident_transition",
            incident=incident.public_id,
            previous=current.value,
            status=target.value,
            actor=actor,
        )
        return incident

    async def recover(
        self, session: AsyncSession, incident: Incident, *, reason: str, detail: str
    ) -> Incident | None:
        if recovery_target(IncidentStatus(incident.status)) is None:
            return None
        await self._lock(session, incident)
        current = IncidentStatus(incident.status)
        target = recovery_target(current)
        if target is None:  # moved on while we waited for the lock
            return None
        if current == IncidentStatus.AWAITING_APPROVAL:
            await self.withdraw_pending(session, incident, reason="symptom cleared")
        incident.status = target.value
        incident.closed_at = datetime.now(UTC)
        await session.flush()
        await self._append(
            session,
            incident,
            "recovered",
            {"reason": reason, "detail": detail, "actor": "detector"},
        )
        await session.refresh(incident)
        bus.publish(
            "incident.closed",
            {
                "incident_id": incident.public_id,
                "id": incident.id,
                "status": incident.status,
                "reason": reason,
                "detail": detail,
            },
        )
        log.info("incident_recovered", incident=incident.public_id, detail=detail)
        return incident

    async def close_manually(
        self, session: AsyncSession, incident: Incident, *, actor: str, note: str
    ) -> Incident:
        target = close_target(IncidentStatus(incident.status))
        if IncidentStatus(incident.status) in TERMINAL_STATUSES:
            return incident
        return await self.transition(
            session,
            incident,
            target,
            kind="closed",
            payload={"actor": actor, "note": note, "reason": "manual"},
            actor=actor,
        )

    async def withdraw_pending(
        self, session: AsyncSession, incident: Incident, *, reason: str, keep: int | None = None
    ) -> None:
        """Withdraw the PENDING approvals of an incident (all but `keep`).

        Called when the incident leaves AWAITING_APPROVAL without a decision, and
        when an operator replaces a proposal.
        """
        rows = (
            await session.execute(
                select(Approval, Action)
                .join(Action, Action.id == Approval.action_row_id)
                .where(
                    Action.incident_id == incident.id,
                    Approval.status == "PENDING",
                    Approval.id != (keep or 0),
                )
                .with_for_update()
            )
        ).all()
        now = datetime.now(UTC)
        for approval, action in rows:
            approval.status = "WITHDRAWN"
            approval.decided_at = now
            approval.decided_by = "system"
            approval.note = reason
            action.status = "WITHDRAWN"
            bus.publish(
                "approval.withdrawn",
                {"approval_id": approval.id, "incident_id": incident.public_id, "reason": reason},
            )
        await session.flush()

    async def _lock(self, session: AsyncSession, incident: Incident) -> None:
        await session.execute(
            select(Incident.id).where(Incident.id == incident.id).with_for_update()
        )
        await session.refresh(incident)

    async def _append(
        self,
        session: AsyncSession,
        incident: Incident,
        kind: str,
        payload: dict[str, Any],
    ) -> IncidentEvent:
        seq = (
            await session.scalar(
                select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
                    IncidentEvent.incident_id == incident.id
                )
            )
            or 0
        ) + 1
        event = IncidentEvent(incident_id=incident.id, seq=seq, kind=kind, payload=payload)
        session.add(event)
        await session.flush()
        return event

    # --- reads -------------------------------------------------------------
    async def list_incidents(
        self,
        session: AsyncSession,
        *,
        status: str = "all",
        service: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> IncidentList:
        query = select(Incident).order_by(Incident.id.desc())
        if status == "open":
            query = query.where(Incident.status.in_([item.value for item in ACTIVE_STATUSES]))
        elif status == "closed":
            query = query.where(Incident.status.in_([item.value for item in TERMINAL_STATUSES]))
        elif status not in ("all", ""):
            query = query.where(Incident.status == status)
        if service:
            query = query.where(Incident.primary_service == service)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
        ids = [row.id for row in rows]
        counts = await self._counts(session, ids)
        now = datetime.now(UTC)
        items = []
        for row in rows:
            summary = _summary(row, now=now)
            item_counts = counts.get(row.id, {})
            summary.event_count = item_counts.get("events", 0)
            summary.evidence_count = item_counts.get("evidence", 0)
            summary.suspect_count = item_counts.get("suspects", 0)
            items.append(summary)
        open_count = (
            await session.scalar(
                select(func.count(Incident.id)).where(
                    Incident.status.in_([item.value for item in ACTIVE_STATUSES])
                )
            )
            or 0
        )
        return IncidentList(items=items, total=total, open=open_count, generated_at=now)

    async def _counts(self, session: AsyncSession, ids: list[int]) -> dict[int, dict[str, int]]:
        if not ids:
            return {}
        result: dict[int, dict[str, int]] = {incident_id: {} for incident_id in ids}
        for name, model in (
            ("events", IncidentEvent),
            ("evidence", Evidence),
            ("suspects", Suspect),
        ):
            rows = await session.execute(
                select(model.incident_id, func.count(model.id))
                .where(model.incident_id.in_(ids))
                .group_by(model.incident_id)
            )
            for incident_id, count in rows.all():
                result.setdefault(incident_id, {})[name] = int(count)
        return result

    async def get(self, session: AsyncSession, ref: str) -> Incident:
        incident: Incident | None = None
        if ref.isdigit():
            incident = await session.get(Incident, int(ref))
        if incident is None:
            incident = await session.scalar(select(Incident).where(Incident.public_id == ref))
        if incident is None:
            raise ApiError(404, "incident_not_found", f"Incident '{ref}' does not exist")
        return incident

    async def detail(self, session: AsyncSession, incident: Incident) -> IncidentDetail:
        events = (
            (
                await session.execute(
                    select(IncidentEvent)
                    .where(IncidentEvent.incident_id == incident.id)
                    .order_by(IncidentEvent.seq)
                )
            )
            .scalars()
            .all()
        )
        evidence = (
            (
                await session.execute(
                    select(Evidence)
                    .where(Evidence.incident_id == incident.id)
                    .order_by(Evidence.id)
                )
            )
            .scalars()
            .all()
        )
        suspects = (
            (
                await session.execute(
                    select(Suspect).where(Suspect.incident_id == incident.id).order_by(Suspect.rank)
                )
            )
            .scalars()
            .all()
        )
        opened = _utc(incident.opened_at)
        until = _utc(incident.closed_at) if incident.closed_at else datetime.now(UTC)
        faults = (
            (
                await session.execute(
                    select(Fault).where(Fault.injected_at <= until).order_by(Fault.id)
                )
            )
            .scalars()
            .all()
        )
        overlapping = [
            FaultContext(
                id=fault.id,
                fault_id=fault.type,
                target=fault.target,
                params=dict(fault.params or {}),
                status=fault.status,
                injected_at=_utc(fault.injected_at),
                reset_at=_utc(fault.reset_at) if fault.reset_at else None,
            )
            for fault in faults
            if fault.reset_at is None or _utc(fault.reset_at) >= opened
        ]
        summary = _summary(incident)
        summary.event_count = len(events)
        summary.evidence_count = len(evidence)
        summary.suspect_count = len(suspects)
        return IncidentDetail(
            **summary.model_dump(),
            trigger=dict(incident.trigger or {}),
            events=[_event_view(event) for event in events],
            evidence=[
                EvidenceView(
                    id=item.id,
                    code=item.code,
                    type=item.type,
                    source=item.source,
                    service=item.service,
                    at=_utc(item.at),
                    data=dict(item.data or {}),
                    hash=item.hash,
                )
                for item in evidence
            ],
            suspects=[
                SuspectView(
                    service=item.service,
                    score=item.score,
                    rank=item.rank,
                    signals=dict(item.signals or {}),
                )
                for item in suspects
            ],
            faults=overlapping,
        )

    async def events_after(
        self, session: AsyncSession, incident: Incident, after_seq: int
    ) -> list[IncidentEventView]:
        rows = (
            (
                await session.execute(
                    select(IncidentEvent)
                    .where(
                        IncidentEvent.incident_id == incident.id,
                        IncidentEvent.seq > after_seq,
                    )
                    .order_by(IncidentEvent.seq)
                )
            )
            .scalars()
            .all()
        )
        return [_event_view(row) for row in rows]

    async def latest_open(self, session: AsyncSession) -> Incident | None:
        return await session.scalar(
            select(Incident)
            .where(Incident.status.in_([item.value for item in ACTIVE_STATUSES]))
            .order_by(Incident.id.desc())
            .limit(1)
        )

    async def recently_closed(
        self, session: AsyncSession, *, signature: str, within_s: int
    ) -> Incident | None:
        cutoff = datetime.now(UTC).timestamp() - within_s
        rows = (
            (
                await session.execute(
                    select(Incident)
                    .where(
                        Incident.signature == signature,
                        Incident.closed_at.is_not(None),
                    )
                    .order_by(Incident.id.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.closed_at and _utc(row.closed_at).timestamp() >= cutoff:
                return row
        return None


service = IncidentService()

__all__ = [
    "EvidenceView",
    "FaultContext",
    "IncidentDetail",
    "IncidentEventView",
    "IncidentList",
    "IncidentService",
    "IncidentSummary",
    "SuspectView",
    "service",
]
