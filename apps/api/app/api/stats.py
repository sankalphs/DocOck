"""Operating statistics: agent cost/latency, outcomes, rate limits (P16)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.db.session import SessionFactory
from app.stats import Stats, collect

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=Stats, summary="Cost, latency, outcomes and guardrails")
async def stats(window_h: Annotated[int, Query(ge=1, le=24 * 90)] = 168) -> Stats:
    async with SessionFactory() as session:
        return await collect(session, window_h=window_h)
