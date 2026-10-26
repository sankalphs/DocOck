"""Runtime model settings and the provider connection test.

Keys are never returned: the view reports `configured` and a masked tail only.
PATCH accepts provider/model/threshold fields; base URLs and keys are `.env`
only and are rejected with 422 (see `app/models/gateway.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.gateway import (
    ModelSettingsPatch,
    ModelSettingsView,
    ModelTestReport,
    ModelTestRequest,
    gateway,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models", response_model=ModelSettingsView, summary="Effective model settings")
async def get_model_settings() -> ModelSettingsView:
    return gateway.view()


@router.patch(
    "/models",
    response_model=ModelSettingsView,
    summary="Override provider/model/thresholds at runtime (no secrets)",
)
async def patch_model_settings(
    patch: ModelSettingsPatch, session: AsyncSession = Depends(get_session)
) -> ModelSettingsView:
    await gateway.update(session, patch)
    return gateway.view()


@router.post(
    "/models/test",
    response_model=ModelTestReport,
    summary="Smoke-test the configured LLM and/or embedding provider",
)
async def test_models(request: ModelTestRequest | None = None) -> ModelTestReport:
    return await gateway.test((request or ModelTestRequest()).target)


__all__ = ["router"]
