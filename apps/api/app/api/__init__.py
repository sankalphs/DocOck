"""API routers.

Routers are added when their phase lands; there are no placeholder endpoints
returning fabricated data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import (
    agent,
    approvals,
    detection,
    events,
    faults,
    health,
    incidents,
    memory,
    reports,
    settings,
    stats,
    telemetry,
)
from app.ratelimit import rate_limit

# Every route passes the rate limiter; it counts writes only (app/ratelimit.py).
api_router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])
api_router.include_router(health.router)
api_router.include_router(events.router)
api_router.include_router(telemetry.router)
api_router.include_router(faults.router)
api_router.include_router(incidents.router)
api_router.include_router(detection.router)
api_router.include_router(settings.router)
api_router.include_router(agent.router)
api_router.include_router(approvals.router)
api_router.include_router(memory.router)
api_router.include_router(reports.router)
api_router.include_router(stats.router)

__all__ = ["api_router"]
