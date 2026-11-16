"""Autopilot and desired-state drift.

Read endpoints show what the autonomous loop is doing and how far the lab is
from its desired state. The write endpoints only trigger the same sweep the
loop runs on its own, or re-learn the desired state from the current (healthy)
lab; neither mutates a container.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.autopilot.service import autopilot
from app.remediation.drift import DesiredState, DriftReport, scanner

router = APIRouter(tags=["autopilot"])


@router.get("/autopilot", summary="Autopilot policy, loop state and recent decisions")
async def autopilot_status() -> dict[str, Any]:
    return autopilot.status()


@router.post("/autopilot/sweep", summary="Run one autopilot sweep now")
async def autopilot_sweep() -> dict[str, Any]:
    if autopilot.enabled:
        await autopilot.sweep()
    return autopilot.status()


@router.get(
    "/remediation/drift",
    response_model=DriftReport,
    summary="Live drift from the desired state (containers, dependencies, flags, load)",
)
async def drift() -> DriftReport:
    return await scanner.scan()


@router.get(
    "/remediation/desired",
    response_model=DesiredState,
    summary="The desired state the drift scanner compares against",
)
async def desired() -> DesiredState:
    return scanner.store.load()


@router.post(
    "/remediation/desired/learn",
    response_model=DriftReport,
    summary="Fold the current healthy state into the desired state, then scan",
)
async def learn() -> DriftReport:
    return await scanner.scan(learn=True)
