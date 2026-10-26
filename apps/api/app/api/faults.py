"""Fault Lab API.

Every endpoint goes through the controller: the router never calls Docker,
flagd, or the load generator itself. A bad target is rejected by the catalog and
the safety guard before any of that is reached.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.faults.audit import AuditEntry
from app.faults.catalog import FaultCatalog, FaultRequest
from app.faults.controller import (
    FaultList,
    FaultView,
    InjectionResult,
    ResetReport,
    get_controller,
)

router = APIRouter(prefix="/faults", tags=["faults"])

FaultStateFilter = Literal[
    "all", "open", "closed", "PROPAGATING", "ACTIVE", "REVERTED", "EXPIRED", "FAILED"
]


@router.get(
    "/catalog",
    response_model=FaultCatalog,
    summary="Schema-driven fault catalog",
    description=(
        "One definition per v1 fault, including the declared flagd variants and "
        "the JSON Schema for POST /api/faults. The dashboard generates its forms "
        "from this response."
    ),
)
async def fault_catalog() -> FaultCatalog:
    return get_controller().catalog()


@router.get(
    "/audit",
    response_model=list[AuditEntry],
    summary="Append-only fault audit trail",
)
async def fault_audit(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[AuditEntry]:
    return await get_controller().audit.tail(limit)


@router.post(
    "/reset-all",
    response_model=ResetReport,
    summary="Panic reset: revert every open fault and reset flag drift",
)
async def reset_all(
    actor: Annotated[str, Query(max_length=64)] = "operator",
) -> ResetReport:
    return await get_controller().reset_all(actor=actor)


@router.get("", response_model=FaultList, summary="List faults, newest first")
async def list_faults(
    status: FaultStateFilter = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FaultList:
    return await get_controller().list_faults(status=status, limit=limit)


@router.post(
    "",
    response_model=InjectionResult,
    status_code=201,
    summary="Inject a fault (or dry-run one)",
    description=(
        "Validates against the catalog, enforces the demo-project label and name "
        "allowlist, injects, writes the pre-image, and emits `fault.injected`. "
        "Application faults start as PROPAGATING until the service observes the "
        "flag; infrastructure faults are ACTIVE after read-back."
    ),
)
async def inject_fault(request: FaultRequest) -> InjectionResult:
    return await get_controller().inject(request)


@router.get("/{fault_id}", response_model=FaultView, summary="One fault")
async def get_fault(fault_id: int) -> FaultView:
    return await get_controller().get(fault_id)


@router.delete(
    "/{fault_id}",
    response_model=FaultView,
    summary="Revert a fault (idempotent, verified by read-back)",
)
async def revert_fault(
    fault_id: int,
    actor: Annotated[str, Query(max_length=64)] = "operator",
) -> FaultView:
    return await get_controller().revert(fault_id, actor=actor)


__all__ = ["router"]
