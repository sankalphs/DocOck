"""Incident API.

`{ref}` accepts either the numeric id or `INC-00042`, so links are readable.
The detail aggregate is what the Incidents screen and the agent both consume;
evidence and suspects are empty until P9 analyses the incident.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.engine import AnalysisReport, analyzer
from app.db.session import get_session
from app.errors import ApiError
from app.incidents.service import (
    EvidenceView,
    IncidentDetail,
    IncidentEventView,
    IncidentList,
    IncidentSummary,
    NoteRequest,
    service,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])

StatusFilter = Literal["all", "open", "closed"]


@router.get("", response_model=IncidentList, summary="List incidents, newest first")
async def list_incidents(
    status: StatusFilter = "all",
    service_name: Annotated[str | None, Query(alias="service", max_length=64)] = None,
    q: Annotated[
        str | None,
        Query(max_length=100, description="Substring of id, title, service or signature"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> IncidentList:
    return await service.list_incidents(
        session, status=status, service=service_name, q=q, limit=limit, offset=offset
    )


CSV_COLUMNS = (
    "public_id",
    "status",
    "severity",
    "primary_service",
    "title",
    "signature",
    "opened_at",
    "closed_at",
    "duration_s",
    "services",
    "event_count",
    "evidence_count",
    "suspect_count",
)


def csv_cell(value: object) -> str:
    """One CSV cell. Text that a spreadsheet would run as a formula is quoted
    with a leading apostrophe (OWASP CSV injection)."""
    if value is None:
        return ""
    if isinstance(value, list):
        value = ";".join(str(item) for item in value)
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


def incidents_csv(items: list[IncidentSummary]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for item in items:
        row = item.model_dump()
        writer.writerow([csv_cell(row[column]) for column in CSV_COLUMNS])
    return buffer.getvalue()


@router.get(
    "/export.csv",
    summary="Incidents as CSV (same filters as the list)",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_incidents(
    status: StatusFilter = "all",
    service_name: Annotated[str | None, Query(alias="service", max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    session: AsyncSession = Depends(get_session),
) -> Response:
    listing = await service.list_incidents(
        session, status=status, service=service_name, q=q, limit=limit
    )
    stamp = listing.generated_at.strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=incidents_csv(listing.items),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="docock-incidents-{stamp}.csv"'},
    )


@router.get("/{ref}", response_model=IncidentDetail, summary="Incident aggregate")
async def get_incident(ref: str, session: AsyncSession = Depends(get_session)) -> IncidentDetail:
    incident = await service.get(session, ref)
    return await service.detail(session, incident)


@router.post(
    "/{ref}/analyze",
    response_model=AnalysisReport,
    summary="Run the deterministic analysis and store evidence + suspects",
)
async def analyze_incident(ref: str) -> AnalysisReport:
    return await analyzer.analyze(ref)


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


@router.post(
    "/{ref}/notes",
    response_model=IncidentEventView,
    status_code=201,
    summary="Add an operator note to the timeline",
    description=(
        "Notes are allowed in every status, including after the incident closed "
        "(postmortem annotations). They appear in the timeline and the report."
    ),
)
async def add_note(
    ref: str, request: NoteRequest, session: AsyncSession = Depends(get_session)
) -> IncidentEventView:
    text = request.text.strip()
    if not text:
        raise ApiError(422, "note_empty", "a note needs some text")
    incident = await service.get(session, ref)
    view = await service.add_note(session, incident, author=request.author.strip(), text=text)
    await session.commit()
    return view


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
