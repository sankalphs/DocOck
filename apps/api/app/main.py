"""DocOck API entry point.

App factory, lifespan, middleware, error envelope, and routers. Run with:

    uv run uvicorn app.main:app --reload --port 8001
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.config import settings
from app.db.session import dispose_engine
from app.errors import register_error_handlers
from app.events.bus import bus
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware
from app.telemetry.http import close_shared_clients

log = get_logger(__name__)

TAGS = [
    {"name": "system", "description": "Health, version, and dependency probes."},
    {"name": "events", "description": "Server-sent events for live updates."},
    {"name": "telemetry", "description": "Prometheus, Jaeger, OpenSearch, Docker."},
    {"name": "faults", "description": "Fault catalog and injection."},
    {"name": "incidents", "description": "Incidents, evidence, and reports."},
    {"name": "agent", "description": "Investigation runs and diagnoses."},
    {"name": "approvals", "description": "Human approval of remediation actions."},
    {"name": "memory", "description": "Resolved-incident retrieval."},
    {"name": "settings", "description": "Runtime model and detector settings."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "api_start",
        version=__version__,
        environment=settings.environment,
        api_port=settings.api_port,
        database=settings.database_url.rsplit("@", 1)[-1],
    )
    bus.publish(
        "system.ready",
        {"version": __version__, "environment": settings.environment},
    )
    try:
        yield
    finally:
        await dispose_engine()
        await close_shared_clients()
        log.info("api_stop")


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description=(
            "Local incident-response lab control plane. Every telemetry read and "
            "every fault write goes through this service; the dashboard holds no "
            "Docker access and no model keys."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time-ms"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)
    app.include_router(api_router)

    return app


app = create_app()
