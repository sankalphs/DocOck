"""Agent investigations (P11): start, watch, cancel, and the validator check."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Response

from app.agent.orchestrator import orchestrator
from app.agent.schemas import (
    TERMINAL_RUN_STATUSES,
    AgentRunDetail,
    AgentRunList,
    AgentRunSummary,
    ValidationReport,
)
from app.errors import ApiError

router = APIRouter(tags=["agent"])


@router.post(
    "/incidents/{ref}/investigate",
    response_model=AgentRunDetail,
    status_code=202,
    summary="Start an agent investigation",
    description=(
        "Starts a background run. `wait=true` blocks until the run finishes "
        "(at most `timeout_s`) and returns the finished run with its steps."
    ),
)
async def investigate(
    ref: str,
    response: Response,
    actor: Annotated[str, Query(max_length=64)] = "operator",
    wait: bool = False,
    timeout_s: Annotated[float, Query(ge=1, le=600)] = 180,
) -> AgentRunDetail:
    started = await orchestrator.start(ref, actor=actor)
    if wait:
        await orchestrator.wait(started.id, timeout_s)
    detail = await orchestrator.get_run(started.id)
    if detail.status in TERMINAL_RUN_STATUSES:
        response.status_code = 200
    return detail


@router.get(
    "/incidents/{ref}/runs", response_model=AgentRunList, summary="Agent runs of one incident"
)
async def incident_runs(ref: str, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> AgentRunList:
    items = await orchestrator.list_runs(incident_ref=ref, limit=limit)
    return AgentRunList(items=items, total=len(items))


@router.post(
    "/incidents/{ref}/diagnosis/validate",
    response_model=ValidationReport,
    summary="Validate a diagnosis against an incident (read-only)",
    description=(
        "Runs the PLAN §11 validator on an arbitrary diagnosis object. Nothing is "
        "stored; this is how seeded bad outputs are checked."
    ),
)
async def validate_diagnosis(
    ref: str, diagnosis: Annotated[dict[str, Any], Body()]
) -> ValidationReport:
    return await orchestrator.validate_for(ref, diagnosis)


@router.get("/agent/runs", response_model=AgentRunList, summary="Recent agent runs")
async def runs(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> AgentRunList:
    items = await orchestrator.list_runs(limit=limit)
    return AgentRunList(items=items, total=len(items))


@router.get("/agent/runs/{run_id}", response_model=AgentRunDetail, summary="One run with steps")
async def run_detail(run_id: int) -> AgentRunDetail:
    return await orchestrator.get_run(run_id)


@router.post(
    "/agent/runs/{run_id}/cancel", response_model=AgentRunSummary, summary="Cancel a running run"
)
async def cancel(run_id: int) -> AgentRunSummary:
    detail = await orchestrator.get_run(run_id)
    if detail.status in TERMINAL_RUN_STATUSES:
        raise ApiError(409, "run_finished", f"run {run_id} already finished as {detail.status}")
    if not await orchestrator.cancel(run_id):
        raise ApiError(409, "run_not_active", f"run {run_id} is not active in this process")
    return AgentRunSummary(**(await orchestrator.get_run(run_id)).model_dump(exclude={"steps"}))
