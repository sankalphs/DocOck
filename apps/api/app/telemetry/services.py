"""Service inventory: one row per service, joined across all four sources.

The join is deliberate rather than clever: Prometheus knows error rate and
latency, Jaeger knows whether the service is emitting traces, Docker knows
whether its container is running and how close it is to its memory limit. A
service missing from one source is still useful (and `sources` says which stores
actually reported it).

Partial failure policy: if every source fails, the endpoint fails with 502 so an
outage is never rendered as an empty service list. If only some fail, the
inventory is returned from the rest and the missing source simply produces no
rows.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import cast

from app.config import settings
from app.telemetry.cache import TTLCache
from app.telemetry.docker import DockerAdapter
from app.telemetry.dto import (
    ContainerInfo,
    MetricResult,
    MetricSeries,
    ServiceInfo,
    ServiceInventory,
)
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.prometheus import ALLOWED_WINDOWS, PrometheusAdapter

_WINDOW_RE = re.compile(r"^(\d+)([smh])$")


def window_seconds(window: str) -> int:
    match = _WINDOW_RE.match(window)
    if not match:
        return 300
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"s": 1, "m": 60, "h": 3600}[unit]


def _latest(series: list[MetricSeries]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in series:
        key = item.labels.get("service_name") or item.labels.get("container_name")
        if key and item.points:
            out[key] = item.points[-1].v
    return out


class ServiceInventoryBuilder:
    def __init__(
        self,
        prometheus: PrometheusAdapter | None = None,
        jaeger: JaegerAdapter | None = None,
        docker: DockerAdapter | None = None,
        ttl_s: float | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusAdapter()
        self.jaeger = jaeger or JaegerAdapter()
        self.docker = docker or DockerAdapter()
        self._cache = TTLCache(ttl_s if ttl_s is not None else settings.telemetry_cache_ttl_s)

    async def build(self, window: str = "5m") -> ServiceInventory:
        if window not in ALLOWED_WINDOWS:
            window = "5m"

        async def load() -> ServiceInventory:
            gathered = await asyncio.gather(
                self.prometheus.service_names(),
                self.jaeger.services(),
                self.docker.containers(include_stats=True),
                self.prometheus.metric("error_rate", window=window),
                self.prometheus.metric("p95_latency", window=window),
                self.prometheus.metric("throughput", window=window),
                self.prometheus.metric("container_cpu", window=window),
                self.prometheus.metric("container_memory", window=window),
                return_exceptions=True,
            )
            (
                names_result,
                traces_result,
                containers_result,
                error_result,
                p95_result,
                throughput_result,
                cpu_result,
                memory_result,
            ) = gathered

            failures = [item for item in gathered if isinstance(item, BaseException)]
            if len(failures) == len(gathered):
                raise failures[0]

            names: list[str] = (
                [] if isinstance(names_result, BaseException) else cast(list[str], names_result)
            )
            traces: list[str] = (
                [] if isinstance(traces_result, BaseException) else cast(list[str], traces_result)
            )
            containers: list[ContainerInfo] = (
                []
                if isinstance(containers_result, BaseException)
                else cast(list[ContainerInfo], containers_result)
            )

            def points(result: object) -> list[MetricSeries]:
                if isinstance(result, BaseException):
                    return []
                return cast(MetricResult, result).series

            error_map = _latest(points(error_result))
            p95_map = _latest(points(p95_result))
            throughput_map = _latest(points(throughput_result))
            cpu_map = _latest(points(cpu_result))
            memory_map = _latest(points(memory_result))

            metric_names: set[str] = set(names)
            trace_names: set[str] = set(traces)
            by_service = {container.service: container for container in containers}

            services: list[ServiceInfo] = []
            for name in sorted(metric_names | trace_names | set(by_service)):
                container = by_service.get(name)
                sources = []
                if name in trace_names:
                    sources.append("traces")
                if name in metric_names:
                    sources.append("metrics")
                if container is not None:
                    sources.append("docker")

                error_value = error_map.get(name)
                services.append(
                    ServiceInfo(
                        name=name,
                        container=container.name if container else None,
                        state=container.state if container else "no-container",
                        health=container.health if container else "none",
                        restart_count=container.restart_count if container else 0,
                        error_rate=round(min(1.0, max(0.0, error_value)), 4)
                        if error_value is not None
                        else None,
                        p95_ms=round(p95_map[name], 2) if name in p95_map else None,
                        throughput_rps=round(throughput_map[name], 3)
                        if name in throughput_map
                        else None,
                        cpu_percent=round(cpu_map[name], 4) if name in cpu_map else None,
                        memory_mb=round(memory_map[name], 1) if name in memory_map else None,
                        cpu_limit_cores=container.cpu_limit_cores if container else None,
                        memory_limit_mb=container.memory_limit_mb if container else None,
                        sources=sources,
                    )
                )

            running = sum(1 for container in containers if container.state == "running")
            return ServiceInventory(
                services=services,
                generated_at=datetime.now(UTC),
                window_s=window_seconds(window),
                containers_total=len(containers),
                containers_running=running,
            )

        return await self._cache.get(("inventory", window), load)


__all__ = ["ServiceInventoryBuilder", "window_seconds"]
