"""Dependency health.

`/api/health` is not a static 200: every dependency is probed for real, in
parallel, with a hard timeout. The dashboard header renders exactly this list,
so a broken dependency is visible before a fault is injected rather than during
an incident.

Probe contract: never raise. A failure is a `status` value plus a one-line
`detail` an operator can act on.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from app import __version__
from app.config import settings
from app.db.session import engine
from app.models.gateway import gateway
from app.telemetry.docker import DockerError, docker_json, server_version
from app.telemetry.http import shared_client

STARTED_AT = time.monotonic()

DependencyState = Literal["ok", "degraded", "down", "unconfigured"]
OverallState = Literal["ok", "degraded", "down"]


class DependencyProbe(BaseModel):
    name: str
    status: DependencyState
    critical: bool = False
    detail: str
    latency_ms: float
    meta: dict[str, Any] = Field(default_factory=dict)


class HealthReport(BaseModel):
    status: OverallState
    app: str
    version: str
    environment: str
    checked_at: datetime
    uptime_s: float
    dependencies: list[DependencyProbe]


async def _timed(
    name: str,
    fn: Any,
    *,
    critical: bool = False,
) -> DependencyProbe:
    started = time.perf_counter()
    try:
        detail, meta = await asyncio.wait_for(fn(), timeout=settings.health_timeout_s)
    except TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        return DependencyProbe(
            name=name,
            status="down",
            critical=critical,
            detail=f"timed out after {settings.health_timeout_s:g}s",
            latency_ms=round(elapsed, 1),
        )
    except Exception as exc:  # a probe must never take the endpoint down
        elapsed = (time.perf_counter() - started) * 1000
        return DependencyProbe(
            name=name,
            status="down",
            critical=critical,
            detail=f"{type(exc).__name__}: {exc}"[:240],
            latency_ms=round(elapsed, 1),
        )
    elapsed = (time.perf_counter() - started) * 1000
    return DependencyProbe(
        name=name,
        status="ok",
        critical=critical,
        detail=detail,
        latency_ms=round(elapsed, 1),
        meta=meta or {},
    )


async def _probe_postgres() -> tuple[str, dict[str, Any]]:
    async with engine.connect() as conn:
        raw = await conn.scalar(text("select version()"))
        extension = await conn.scalar(
            text("select extversion from pg_extension where extname = 'vector'")
        )
        tables = await conn.scalar(
            text("select count(*) from information_schema.tables where table_schema = 'public'")
        )
    version = str(raw)
    match = re.search(r"PostgreSQL\s+([\d.]+)", version)
    short = match.group(1) if match else "unknown"
    detail = f"PostgreSQL {short} | pgvector {extension or 'missing'} | {tables} tables"
    return detail, {"version": short, "pgvector": extension, "tables": tables}


async def _probe_docker() -> tuple[str, dict[str, Any]]:
    cached = _docker_state_cache.get("value")
    if cached is not None and time.monotonic() - cached[0] < DOCKER_STATE_TTL_S:
        return cached[1], cached[2]

    version, containers = await asyncio.gather(
        _docker_server_version_cached(),
        docker_json(
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={settings.demo_compose_project}",
            "--format",
            "{{json .}}",
            timeout_s=8.0,
        ),
    )
    running = sum(1 for item in containers if item.get("State") == "running")
    total = len(containers)
    detail = (
        f"Docker {version} | {running}/{total} {settings.demo_compose_project} containers running"
    )
    meta = {"server_version": version, "running": running, "total": total}
    _docker_state_cache["value"] = (time.monotonic(), detail, meta)
    return detail, meta


DOCKER_STATE_TTL_S = 10.0
_docker_state_cache: dict[str, tuple[float, str, dict[str, Any]]] = {}
_docker_version_cache: tuple[float, str] | None = None


async def _docker_server_version_cached() -> str:
    """`docker version` spawns a 70 MB CLI; cache the answer for a minute."""
    global _docker_version_cache
    now = time.monotonic()
    if _docker_version_cache is not None and now - _docker_version_cache[0] < 60.0:
        return _docker_version_cache[1]
    version = await server_version()
    _docker_version_cache = (now, version)
    return version


async def _probe_prometheus() -> tuple[str, dict[str, Any]]:
    client = shared_client(
        "health:prometheus",
        base_url=settings.prometheus_url,
        timeout_s=settings.health_timeout_s,
        retries=0,
    )
    ready = (await client.get_text("/-/ready")).strip()
    payload = await client.get_json(
        "/api/v1/query", params={"query": "count(traces_span_metrics_calls_total)"}
    )
    results = payload["data"]["result"]
    series = int(float(results[0]["value"][1])) if results else 0
    return (
        f"{'ready' if 'Ready' in ready else ready[:40]} | {series} span-metric series",
        {"span_metric_series": series},
    )


async def _probe_jaeger() -> tuple[str, dict[str, Any]]:
    client = shared_client(
        "health:jaeger",
        base_url=settings.jaeger_api_url,
        timeout_s=settings.health_timeout_s,
        retries=0,
    )
    payload = await client.get_json("/api/v3/services")
    services = payload.get("services") or []
    return f"{len(services)} services reported", {"services": len(services)}


async def _probe_opensearch() -> tuple[str, dict[str, Any]]:
    client = shared_client(
        "health:opensearch",
        base_url=settings.opensearch_url,
        timeout_s=settings.health_timeout_s,
        retries=0,
    )
    health = await client.get_json("/_cluster/health")
    indices = await client.get_json(
        f"/_cat/indices/{settings.opensearch_index}", params={"format": "json"}
    )
    docs = sum(int(item.get("docs.count") or 0) for item in indices)
    return (
        f"cluster {health.get('status')} | {len(indices)} indices | {docs} docs",
        {"cluster_status": health.get("status"), "indices": len(indices), "docs": docs},
    )


async def _probe_demo() -> tuple[str, dict[str, Any]]:
    """Storefront reachability — the one URL a human actually looks at."""
    client = shared_client(
        "health:demo",
        base_url=settings.demo_frontend_url,
        timeout_s=settings.health_timeout_s,
        retries=0,
    )
    started = time.perf_counter()
    response = await client.request("GET", "/")
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code >= 400:
        raise RuntimeError(f"storefront returned HTTP {response.status_code}")
    return (
        f"storefront HTTP {response.status_code} in {elapsed_ms:.0f} ms",
        {"status": response.status_code, "latency_ms": round(elapsed_ms, 1)},
    )


async def _probe_llm() -> tuple[str, dict[str, Any]]:
    # Config-only probe: never spends tokens on a health check. The connection
    # test (POST /api/settings/models/test) is the one that calls the model.
    cfg = gateway.config.llm
    model = gateway.llm().model
    if cfg.provider == "stub":
        detail = f"stub | {model} | offline, deterministic"
    elif gateway.llm_ready:
        detail = f"{cfg.provider} | {model} | key configured"
    else:
        detail = f"{cfg.provider} | {model} | no API key configured"
    return detail, {
        "provider": cfg.provider,
        "model": model,
        "configured": gateway.llm_ready,
        "embedding": f"{gateway.config.embedding.provider}:{gateway.config.embedding.model}",
    }


async def probe_all() -> list[DependencyProbe]:
    """Probe every dependency concurrently."""
    llm_probe = await _timed("llm", _probe_llm)
    if llm_probe.status == "ok" and not gateway.llm_ready:
        llm_probe = llm_probe.model_copy(update={"status": "unconfigured"})

    others = await asyncio.gather(
        _timed("postgres", _probe_postgres, critical=True),
        _timed("docker", _probe_docker),
        _timed("demo", _probe_demo),
        _timed("prometheus", _probe_prometheus),
        _timed("jaeger", _probe_jaeger),
        _timed("opensearch", _probe_opensearch),
    )
    order = {
        "postgres": 0,
        "docker": 1,
        "demo": 2,
        "prometheus": 3,
        "jaeger": 4,
        "opensearch": 5,
        "llm": 6,
    }
    return sorted([*others, llm_probe], key=lambda item: order.get(item.name, 99))


def overall_status(dependencies: list[DependencyProbe]) -> OverallState:
    if any(dep.critical and dep.status == "down" for dep in dependencies):
        return "down"
    if any(dep.status == "down" for dep in dependencies):
        return "degraded"
    return "ok"


_cache: tuple[float, HealthReport] | None = None


async def get_health_report(*, force: bool = False) -> HealthReport:
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < settings.health_cache_ttl_s:
        return _cache[1]

    dependencies = await probe_all()
    report = HealthReport(
        status=overall_status(dependencies),
        app=settings.app_name,
        version=__version__,
        environment=settings.environment,
        checked_at=datetime.now(UTC),
        uptime_s=round(time.monotonic() - STARTED_AT, 1),
        dependencies=dependencies,
    )
    _cache = (now, report)
    return report


def clear_health_cache() -> None:
    global _cache
    _cache = None


__all__ = [
    "DependencyProbe",
    "DockerError",
    "HealthReport",
    "clear_health_cache",
    "get_health_report",
    "overall_status",
    "probe_all",
]
