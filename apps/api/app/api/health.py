"""Health and version routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app import __version__
from app.config import settings
from app.health import HealthReport, clear_health_cache, get_health_report

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthReport,
    summary="Liveness plus per-dependency status",
    responses={
        200: {"description": "All dependencies reachable"},
        503: {"model": HealthReport, "description": "A critical dependency is down"},
    },
)
async def health(
    response: Response,
    refresh: Annotated[bool, Query(description="Bypass the 2 s probe cache")] = False,
) -> HealthReport:
    if refresh:
        clear_health_cache()
    report = await get_health_report()
    if report.status == "down":
        response.status_code = 503
    return report


@router.get("/version", summary="Build identity")
async def version() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
    }


__all__ = ["router"]
