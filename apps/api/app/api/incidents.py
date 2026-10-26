"""Incident API.

`{ref}` accepts either the numeric id or `INC-00042`, so links are readable.
The detail aggregate is what the Incidents screen and the agent both consume;
evidence and suspects are empty until P9 analyses the incident.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.incidents.service import (
    EvidenceView,
    IncidentDetail,
    IncidentEventView,
    IncidentList,
    service,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])

StatusFilter = Literal["all", "open", "closed"]


@router.get("", response_model=IncidentList, summary="List incidents, newest first")
async def list_incidents(
    status: StatusFilter = "all",
    service_name: Annotated[str | None, Query(alias="service")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> IncidentList:
    return await service.list_incidents(
        session, status=status, service=service_name, limit=limit, offset=offset
    )


@router.get("/{ref}", response_model=IncidentDetail, summary="Incident aggregate")
async def get_incident(ref: str, session: AsyncSession = Depends(get_session)) -> IncidentDetail:
    incident = await service.get(session, ref)
    return await service.detail(session, incident)


@router.get(
    "/{ref}/events",
    response_model=list[IncidentEventView],
    summary="Incident timeline; replay with after_seq",
)
async def incident_events(
    ref: str,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> list[IncidentEventView]:
    incident = await service.get(session, ref)
    return await service.events_after(session, incident, after_seq)


@router.get(
    "/{ref}/evidence",
    response_model=list[EvidenceView],
    summary="Evidence rows for this incident",
)
async def incident_evidence(
    ref: str, session: AsyncSession = Depends(get_session)
) -> list[EvidenceView]:
    incident = await service.get(session, ref)
    detail = await service.detail(session, incident)
    return detail.evidence


@router.post("/{ref}/close", response_model=IncidentDetail, summary="Manually close")
async def close_incident(
    ref: str,
    actor: Annotated[str, Query(max_length=64)] = "operator",
    note: Annotated[str, Query(max_length=500)] = "",
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident = await service.get(session, ref)
    incident = await service.close_manually(session, incident, actor=actor, note=note)
    return await service.detail(session, incident)


__all__ = ["router"]
