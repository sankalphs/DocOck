"""Memory (P12): status, debug retrieval and the stored summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.schemas import FAULT_CLASSES
from app.db.models import Incident, Memory
from app.db.session import SessionFactory
from app.errors import ApiError
from app.memory.service import MemorySearchResult, MemoryStatus, memory

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryView(BaseModel):
    id: int
    incident_ref: str | None
    root_cause: str
    fault_class: str
    summary: str
    services: list[str]
    seed: bool
    created_at: datetime


class MemoryList(BaseModel):
    items: list[MemoryView]
    total: int


@router.get("/status", response_model=MemoryStatus, summary="Dimension guard and store state")
async def status() -> MemoryStatus:
    return await memory.status()


@router.get(
    "/search",
    response_model=MemorySearchResult,
    summary="Debug retrieval",
    description=(
        "Either `incident` (build the query from an incident, excluding itself) or "
        "free text `q`. Results are hypotheses, never evidence."
    ),
)
async def search(
    q: Annotated[str | None, Query(max_length=600)] = None,
    incident: Annotated[str | None, Query(max_length=32)] = None,
    fault_class: Annotated[str | None, Query(max_length=32)] = None,
    k: Annotated[int | None, Query(ge=1, le=20)] = None,
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> MemorySearchResult:
    if fault_class is not None and fault_class not in FAULT_CLASSES:
        raise ApiError(422, "bad_fault_class", f"fault_class must be one of {FAULT_CLASSES}")
    if incident:
        return await memory.search_incident(incident, top_k=k)
    if not q:
        raise ApiError(422, "query_required", "pass q or incident")
    return await memory.search(q, fault_class=fault_class, top_k=k, min_score=min_score)


@router.get("", response_model=MemoryList, summary="Stored memories, newest first")
async def list_memories(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> MemoryList:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Memory, Incident.public_id)
                .outerjoin(Incident, Incident.id == Memory.incident_id)
                .order_by(Memory.id.desc())
                .limit(limit)
            )
        ).all()
    items = [
        MemoryView(
            id=row.id,
            incident_ref=ref,
            root_cause=row.root_cause,
            fault_class=row.fault_class,
            summary=row.summary,
            services=list(row.services or []),
            seed=row.incident_id is None,
            created_at=row.created_at
            if row.created_at.tzinfo
            else row.created_at.replace(tzinfo=UTC),
        )
        for row, ref in rows
    ]
    return MemoryList(items=items, total=len(items))


@router.post(
    "/incidents/{ref}",
    response_model=MemoryView,
    summary="Store (or refresh) the memory of one resolved incident",
)
async def remember(ref: str, force: bool = False) -> MemoryView:
    row = await memory.remember(ref, force=force)
    if row is None:
        raise ApiError(409, "not_memorable", f"{ref} is not RESOLVED with a valid diagnosis")
    return MemoryView(
        id=row.id,
        incident_ref=ref,
        root_cause=row.root_cause,
        fault_class=row.fault_class,
        summary=row.summary,
        services=list(row.services or []),
        seed=False,
        created_at=row.created_at,
    )


__all__ = ["router"]
