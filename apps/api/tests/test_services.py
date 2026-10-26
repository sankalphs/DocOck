from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.telemetry.dto import (
    ContainerInfo,
    MetricPoint,
    MetricResult,
    MetricSeries,
    ServiceInventory,
)
from app.telemetry.services import ServiceInventoryBuilder, window_seconds


def _metric(metric_id: str, values: dict[str, float]) -> MetricResult:
    return MetricResult(
        metric=metric_id,
        label=metric_id,
        unit="x",
        dimension="service",
        query="q",
        result_type="instant",
        series=[
            MetricSeries(
                labels={"service_name": name}, points=[MetricPoint(t=datetime.now(UTC), v=value)]
            )
            for name, value in values.items()
        ],
    )


class StubPrometheus:
    def __init__(self) -> None:
        self.values = {
            "error_rate": {"payment": 0.42, "cart": 1.8},
            "p95_latency": {"payment": 41.2},
            "throughput": {"payment": 2.5, "cart": 0.5},
            "container_cpu": {"payment": 0.31},
            "container_memory": {"payment": 92.6},
        }

    async def service_names(self) -> list[str]:
        return ["payment", "cart"]

    async def metric(self, metric: str, **_kwargs: Any) -> MetricResult:
        return _metric(metric, self.values[metric])


class StubJaeger:
    async def services(self) -> list[str]:
        return ["payment", "frontend"]


class StubDocker:
    async def containers(self, *, include_stats: bool = True) -> list[ContainerInfo]:
        return [
            ContainerInfo(
                name="payment",
                service="payment",
                image="payment:latest",
                state="running",
                health="healthy",
                memory_limit_mb=140,
                cpu_limit_cores=1.5,
                restart_count=0,
            )
        ]


async def test_inventory_joins_all_three_sources() -> None:
    builder = ServiceInventoryBuilder(
        prometheus=StubPrometheus(),  # type: ignore[arg-type]
        jaeger=StubJaeger(),  # type: ignore[arg-type]
        docker=StubDocker(),  # type: ignore[arg-type]
        ttl_s=1,
    )

    inventory = await builder.build("5m")
    by_name = {service.name: service for service in inventory.services}

    assert inventory.window_s == 300
    assert inventory.containers_total == 1
    assert inventory.containers_running == 1

    payment = by_name["payment"]
    assert payment.sources == ["traces", "metrics", "docker"]
    assert payment.container == "payment"
    assert payment.health == "healthy"
    assert payment.p95_ms == pytest.approx(41.2)
    assert payment.memory_limit_mb == 140
    assert payment.cpu_limit_cores == 1.5

    # cart only reports metrics; it has no container in this stub.
    cart = by_name["cart"]
    assert cart.sources == ["metrics"]
    assert cart.state == "no-container"
    # Error rates are clamped to [0, 1] — a noisy upstream cannot claim 180%.
    assert cart.error_rate == 1.0

    # frontend only appears in traces.
    frontend = by_name["frontend"]
    assert frontend.sources == ["traces"]
    assert frontend.error_rate is None


async def test_every_source_failing_is_an_error() -> None:
    class Boom:
        async def service_names(self) -> list[str]:
            raise RuntimeError("prometheus down")

        async def services(self) -> list[str]:
            raise RuntimeError("jaeger down")

        async def containers(self, *, include_stats: bool = True) -> list[ContainerInfo]:
            raise RuntimeError("docker down")

        async def metric(self, *_args: Any, **_kwargs: Any) -> MetricResult:
            raise RuntimeError("prometheus down")

    builder = ServiceInventoryBuilder(
        prometheus=Boom(),  # type: ignore[arg-type]
        jaeger=Boom(),  # type: ignore[arg-type]
        docker=Boom(),  # type: ignore[arg-type]
        ttl_s=1,
    )

    with pytest.raises(RuntimeError):
        await builder.build("5m")


async def test_partial_failure_still_returns_rows() -> None:
    class HalfProm(StubPrometheus):
        async def metric(self, metric: str, **_kwargs: Any) -> MetricResult:
            if metric == "error_rate":
                raise RuntimeError("query timeout")
            return await super().metric(metric, **_kwargs)

    builder = ServiceInventoryBuilder(
        prometheus=HalfProm(),  # type: ignore[arg-type]
        jaeger=StubJaeger(),  # type: ignore[arg-type]
        docker=StubDocker(),  # type: ignore[arg-type]
        ttl_s=1,
    )

    inventory = await builder.build("5m")
    payment = next(service for service in inventory.services if service.name == "payment")

    assert payment.error_rate is None
    assert payment.p95_ms == pytest.approx(41.2)


def test_window_seconds_parses_supported_windows() -> None:
    assert window_seconds("5m") == 300
    assert window_seconds("1h") == 3600
    assert window_seconds("30s") == 30
    assert window_seconds("nonsense") == 300


async def test_inventory_is_cached() -> None:
    prometheus = StubPrometheus()
    builder = ServiceInventoryBuilder(
        prometheus=prometheus,  # type: ignore[arg-type]
        jaeger=StubJaeger(),  # type: ignore[arg-type]
        docker=StubDocker(),  # type: ignore[arg-type]
        ttl_s=30,
    )

    first: ServiceInventory = await builder.build("5m")
    second: ServiceInventory = await builder.build("5m")

    assert first is second
