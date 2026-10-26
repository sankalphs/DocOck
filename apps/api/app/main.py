"""DocOck API entry point.

Phase 0/1: application factory, CORS for the local dashboard, and a health
endpoint. Dependency probes and the domain routers arrive in later phases.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import __version__
from app.config import settings


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    version: str
    environment: str


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Local incident-response lab control plane.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Liveness probe. Dependency checks are added in Phase 2."""
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            version=__version__,
            environment=settings.environment,
        )

    @app.get("/api/version", tags=["system"])
    def version() -> dict[str, str]:
        return {"name": settings.app_name, "version": __version__}

    return app


app = create_app()
