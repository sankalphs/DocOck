"""Telemetry read API.

Every endpoint is a thin, typed projection over an adapter. No raw query string
reaches Prometheus, and no OpenSearch DSL comes from the browser: the shape of
the request is the allowlist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.telemetry.docker import DockerAdapter
from app.telemetry.dto import (
    ContainerInfo,
    LogSearchResult,
    MetricResult,
    MetricSpec,
    ServiceInventory,
    TraceDetail,
    TraceSearchResult,
)
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.logs import LogsAdapter
from app.telemetry.prometheus import PrometheusAdapter, catalog
from app.telemetry.services import ServiceInventoryBuilder

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

_prometheus = PrometheusAdapter()
_jaeger = JaegerAdapter()
_logs = LogsAdapter()
_docker = DockerAdapter()
_inventory = ServiceInventoryBuilder(_prometheus, _jaeger, _docker)


def get_prometheus() -> PrometheusAdapter:
    return _prometheus


def get_jaeger() -> JaegerAdapter:
    return _jaeger


def get_logs() -> LogsAdapter:
    return _logs


def get_docker() -> DockerAdapter:
    return _docker


def get_inventory() -> ServiceInventoryBuilder:
    return _inventory


@router.get(
    "/metrics/catalog",
    response_model=list[MetricSpec],
    summary="Whitelisted metric catalog",
)
async def metric_catalog() -> list[MetricSpec]:
    return catalog()


@router.get(
    "/metrics",
    response_model=MetricResult,
    summary="Query one allowlisted metric",
    description=(
        "Pass a catalog id, not PromQL. Omit start/end for an instant query; "
        "provide both for a range query (step defaults to ~120 points)."
    ),
)
async def metrics(
    metric: Annotated[str, Query(description="Catalog id, e.g. error_rate")],
    service: Annotated[str | None, Query(description="Exact service_name")] = None,
    window: Annotated[str | None, Query(description="Rate window, e.g. 5m")] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    step: Annotated[int | None, Query(ge=5, le=300)] = None,
    adapter: PrometheusAdapter = Depends(get_prometheus),
) -> MetricResult:
    return await adapter.metric(
        metric,
        service=service,
        window=window,
        start=start,
        end=end,
        step_s=step,
    )


@router.get(
    "/traces",
    response_model=TraceSearchResult,
    summary="Search recent traces",
)
async def traces(
    service: str | None = None,
    operation: str | None = None,
    lookback_s: Annotated[int, Query(ge=60, le=3600)] = 900,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    errors_only: bool = False,
    min_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    adapter: JaegerAdapter = Depends(get_jaeger),
) -> TraceSearchResult:
    return await adapter.search(
        service=service,
        operation=operation,
        lookback_s=lookback_s,
        limit=limit,
        errors_only=errors_only,
        min_duration_ms=min_duration_ms,
    )


@router.get(
    "/traces/{trace_id}",
    response_model=TraceDetail,
    summary="One trace as a flat, depth-ordered waterfall",
)
async def trace(trace_id: str, adapter: JaegerAdapter = Depends(get_jaeger)) -> TraceDetail:
    return await adapter.trace(trace_id)


@router.get(
    "/logs",
    response_model=LogSearchResult,
    summary="Search the otel-logs index",
)
async def logs(
    service: str | None = None,
    level: str | None = None,
    q: Annotated[str | None, Query(description="Phrase matched against the log body")] = None,
    trace_id: str | None = None,
    lookback_s: Annotated[int, Query(ge=60, le=86400)] = 900,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    adapter: LogsAdapter = Depends(get_logs),
) -> LogSearchResult:
    return await adapter.search(
        service=service,
        level=level,
        text=q,
        trace_id=trace_id,
        lookback_s=lookback_s,
        limit=limit,
    )


@router.get(
    "/services",
    response_model=ServiceInventory,
    summary="Service inventory joined from metrics, traces, and Docker",
)
async def services(
    window: Annotated[str, Query(description="Rate window for the KPI columns")] = "5m",
    builder: ServiceInventoryBuilder = Depends(get_inventory),
) -> ServiceInventory:
    return await builder.build(window)


@router.get(
    "/containers",
    response_model=list[ContainerInfo],
    summary="Docker state for the demo Compose project",
)
async def containers(
    include_stats: bool = True,
    adapter: DockerAdapter = Depends(get_docker),
) -> list[ContainerInfo]:
    return await adapter.containers(include_stats=include_stats)


__all__ = ["router"]
