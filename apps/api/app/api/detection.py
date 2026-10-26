"""Detector status and a manual cycle.

The manual cycle exists so the DoD and the scenario runner can advance the
detector without waiting for the cadence; it runs the same code path as the
background loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.detection.runtime import runtime

router = APIRouter(prefix="/detection", tags=["detection"])


class BreachView(BaseModel):
    service: str
    signal: str
    signature: str
    value: float | None
    baseline: float | None
    threshold: float | None
    first_seen: datetime
    consecutive: int
    detail: str
    severity: str


class DetectionRunView(BaseModel):
    at: datetime
    services: int
    breaches: list[BreachView] = Field(default_factory=list)
    opened: str | None = None
    attached: int = 0
    closed: str | None = None
    skipped: str | None = None
    persisted: int = 0
    baselines: dict[str, dict[str, float | None]] = Field(default_factory=dict)


@router.get("/status", summary="Detector state, baselines, and last cycle")
async def detection_status() -> dict[str, Any]:
    return runtime.status()


@router.post("/run", response_model=DetectionRunView, summary="Run one detection cycle now")
async def detection_run() -> DetectionRunView:
    report = await runtime.detector.run_once(persist=True)
    return DetectionRunView(
        at=report.at,
        services=report.services,
        breaches=[
            BreachView(
                service=item.service,
                signal=item.signal,
                signature=item.signature,
                value=item.value,
                baseline=item.baseline,
                threshold=item.threshold,
                first_seen=item.first_seen,
                consecutive=item.consecutive,
                detail=item.detail,
                severity=item.severity,
            )
            for item in report.breaches
        ],
        opened=report.opened,
        attached=report.attached,
        closed=report.closed,
        skipped=report.skipped,
        persisted=report.persisted,
        baselines=report.baselines,
    )


__all__ = ["router"]
