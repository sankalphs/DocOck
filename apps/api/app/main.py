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
from app.agent.orchestrator import orchestrator as agent_orchestrator
from app.api import api_router
from app.config import settings
from app.db.session import SessionFactory, dispose_engine
from app.detection.runtime import runtime as detection_runtime
from app.errors import register_error_handlers
from app.events.bus import bus
from app.faults.runtime import FaultRuntime
from app.logging import configure_logging, get_logger
from app.memory.service import memory
from app.memory.tool import register_memory_tool
from app.middleware import RequestContextMiddleware
from app.models.gateway import gateway as model_gateway
from app.remediation.approval import approvals
from app.remediation.executor import executor
from app.reports.service import reports
from app.telemetry.http import close_shared_clients
from app.verification.verifier import verifier

log = get_logger(__name__)

TAGS = [
    {"name": "system", "description": "Health, version, and dependency probes."},
    {"name": "events", "description": "Server-sent events for live updates."},
    {"name": "telemetry", "description": "Prometheus, Jaeger, OpenSearch, Docker."},
    {"name": "faults", "description": "Fault catalog and injection."},
    {"name": "incidents", "description": "Incidents, evidence, and reports."},
    {"name": "detection", "description": "Deterministic detector status and cycles."},
    {"name": "agent", "description": "Investigation runs and diagnoses."},
    {"name": "approvals", "description": "Human approval of remediation actions."},
    {"name": "reports", "description": "Verification verdicts and incident reports."},
    {"name": "stats", "description": "Cost, latency, outcome and rate-limit statistics."},
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
        async with SessionFactory() as session:
            await model_gateway.load(session)
    except Exception as exc:  # the DB probe reports this; models fall back to .env
        log.warning("model_settings_load_failed", error=str(exc))
    # P12: memory is advisory context for the agent (hypotheses, never evidence).
    register_memory_tool()
    agent_orchestrator.hypotheses = memory.hypotheses
    await memory.start()
    try:
        stale = await agent_orchestrator.recover_stale()
        if stale:
            log.warning("agent_runs_interrupted", runs=stale)
    except Exception as exc:
        log.warning("agent_recover_failed", error=str(exc))
    # P14: a gated diagnosis proposes its action; approvals expire on a sweep.
    agent_orchestrator.on_diagnosed = approvals.auto_plan
    await approvals.start()
    # P15: every executed action is verified against the detector; the verdict
    # closes the incident and stores its report.
    executor.on_executed = verifier.hook
    verifier.on_verified = reports.generate
    runtime = FaultRuntime()
    await runtime.start()
    await detection_runtime.start()
    try:
        await verifier.start()
    except Exception as exc:
        log.warning("verification_resume_failed", error=str(exc))
    try:
        yield
    finally:
        await agent_orchestrator.shutdown()
        await verifier.shutdown()
        await approvals.stop()
        await memory.stop()
        await detection_runtime.stop()
        await runtime.stop()
        await model_gateway.aclose()
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
