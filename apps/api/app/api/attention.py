"""What needs a human right now: one cheap read for the console chrome.

The sidebar badges and the browser-tab title poll this instead of fetching the
incident, approval and fault lists on every page. Counts only; every number
links to a screen that has the detail.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, Fault, Incident
from app.db.session import SessionFactory
from app.domain.state import ACTIVE_STATUSES
from app.faults.catalog import OPEN_STATUSES
from app.remediation.schemas import ApprovalStatus

router = APIRouter(tags=["system"])

SEVERITY_ORDER = ("sev1", "sev2", "sev3", "sev4")


class OpenIncidentRef(BaseModel):
    public_id: str
    status: str
    severity: str
    title: str
    opened_at: datetime


class Attention(BaseModel):
    open_incidents: int
    pending_approvals: int
    active_faults: int
    worst_severity: str | None
    latest_incident: OpenIncidentRef | None
    generated_at: datetime


def worst(severities: list[str]) -> str | None:
    """The most severe of `sev1`..`sev4`; unknown labels rank last."""
    known = [item for item in severities if item in SEVERITY_ORDER]
    if not known:
        return severities[0] if severities else None
    return min(known, key=SEVERITY_ORDER.index)


async def collect(session: AsyncSession, *, now: datetime | None = None) -> Attention:
    now = now or datetime.now(UTC)
    active = [item.value for item in ACTIVE_STATUSES]
    open_rows = (
        await session.execute(
            select(Incident)
            .where(Incident.status.in_(active))
            .order_by(Incident.id.desc())
            .limit(50)
        )
    ).scalars()
    incidents = list(open_rows)
    open_count = int(
        await session.scalar(select(func.count(Incident.id)).where(Incident.status.in_(active)))
        or 0
    )
    # An approval past its expiry is not decidable even before the sweep marks
    # it EXPIRED, so it must not light up the badge.
    pending = int(
        await session.scalar(
            select(func.count(Approval.id)).where(
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at > now,
            )
        )
        or 0
    )
    faults = int(
        await session.scalar(
            select(func.count(Fault.id)).where(
                Fault.status.in_([item.value for item in OPEN_STATUSES])
            )
        )
        or 0
    )
    latest = incidents[0] if incidents else None
    return Attention(
        open_incidents=open_count,
        pending_approvals=pending,
        active_faults=faults,
        worst_severity=worst([row.severity for row in incidents]),
        latest_incident=(
            OpenIncidentRef(
                public_id=latest.public_id,
                status=latest.status,
                severity=latest.severity,
                title=latest.title,
                opened_at=(
                    latest.opened_at
                    if latest.opened_at.tzinfo
                    else latest.opened_at.replace(tzinfo=UTC)
                ),
            )
            if latest is not None
            else None
        ),
        generated_at=now,
    )


@router.get(
    "/attention",
    response_model=Attention,
    summary="Open incidents, pending approvals and active faults (counts)",
)
async def attention() -> Attention:
    async with SessionFactory() as session:
        return await collect(session)


__all__ = ["Attention", "OpenIncidentRef", "collect", "router", "worst"]
