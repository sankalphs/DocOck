"""API routers.

Routers are added when their phase lands; there are no placeholder endpoints
returning fabricated data.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent,
    detection,
    events,
    faults,
    health,
    incidents,
    memory,
    settings,
    telemetry,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(events.router)
api_router.include_router(telemetry.router)
api_router.include_router(faults.router)
api_router.include_router(incidents.router)
api_router.include_router(detection.router)
api_router.include_router(settings.router)
api_router.include_router(agent.router)
api_router.include_router(memory.router)

__all__ = ["api_router"]
