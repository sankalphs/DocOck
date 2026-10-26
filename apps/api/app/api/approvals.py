"""Remediation approvals (P14): plan, list, decide, dry-run, audit.

No response carries an approval token or its hash: the token is minted on
approve and handed to the executor in memory.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Query

from app.remediation.approval import approvals
from app.remediation.audit import ActionAuditEntry
from app.remediation.schemas import (
    ApprovalList,
    ApprovalView,
    DecisionRequest,
    DryRunResult,
    PlanRequest,
    PlanResult,
)

router = APIRouter(tags=["approvals"])

StatusFilter = Literal["all", "pending", "decided"]


@router.get("/approvals", response_model=ApprovalList, summary="Approvals, newest first")
async def list_approvals(
    status: StatusFilter = "all", limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> ApprovalList:
    return await approvals.list(status=status, limit=limit)


@router.get(
    "/approvals/audit",
    response_model=list[ActionAuditEntry],
    summary="Remediation audit trail (runtime/audit/actions.jsonl)",
)
async def approval_audit(
    incident: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ActionAuditEntry]:
    return await approvals.audit.tail(limit, incident=incident)


@router.get("/approvals/{approval_id}", response_model=ApprovalView, summary="One approval")
async def get_approval(approval_id: int) -> ApprovalView:
    return await approvals.get(approval_id)


@router.get(
    "/incidents/{ref}/approvals", response_model=ApprovalList, summary="Approvals of one incident"
)
async def incident_approvals(
    ref: str, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> ApprovalList:
    return await approvals.list(incident_ref=ref, limit=limit)


@router.post(
    "/incidents/{ref}/approvals",
    response_model=PlanResult,
    status_code=201,
    summary="Propose a remediation for a diagnosed incident",
    description=(
        "Empty body = the diagnosis' recommended action. An explicit `action_id` "
        "+ `target` overrides it but passes the same catalog, safety-boundary and "
        "precondition checks. `escalate_human` escalates instead of planning."
    ),
)
async def plan_remediation(
    ref: str, request: Annotated[PlanRequest | None, Body()] = None
) -> PlanResult:
    return await approvals.plan(ref, request or PlanRequest())


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalView,
    summary="Approve: the server issues a single-use token and executes",
    description="`wait=true` returns after the execution finished (at most `timeout_s`).",
)
async def approve(
    approval_id: int,
    request: Annotated[DecisionRequest | None, Body()] = None,
    wait: bool = False,
    timeout_s: Annotated[float, Query(ge=1, le=300)] = 120,
) -> ApprovalView:
    decision = request or DecisionRequest()
    view = await approvals.approve(approval_id, actor=decision.actor, note=decision.note)
    if wait:
        await approvals.executor.wait(approval_id, timeout_s)
        view = await approvals.get(approval_id)
    return view


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=ApprovalView,
    summary="Reject: nothing executes and the incident escalates",
)
async def reject(
    approval_id: int, request: Annotated[DecisionRequest | None, Body()] = None
) -> ApprovalView:
    decision = request or DecisionRequest()
    return await approvals.reject(approval_id, actor=decision.actor, note=decision.note)


@router.post(
    "/approvals/{approval_id}/dry-run",
    response_model=DryRunResult,
    summary="What approving would do now (read-only)",
)
async def dry_run(
    approval_id: int, actor: Annotated[str, Query(max_length=64)] = "operator"
) -> DryRunResult:
    return await approvals.dry_run(approval_id, actor=actor)


__all__ = ["router"]
