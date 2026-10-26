"""Live contract tests â€” opt-in, require the running stack.

    uv run pytest -m live -o addopts=-q

These are the tests that fail loudly when a demo upgrade renames a metric or
changes an API shape. They are excluded from the default run (`-m 'not live'`)
so `scripts\\check.ps1` stays offline-safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.telemetry.docker import DockerAdapter
from app.telemetry.http import AsyncHttpClient
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.logs import LogsAdapter
from app.telemetry.prometheus import PrometheusAdapter
from app.telemetry.services import ServiceInventoryBuilder

pytestmark = pytest.mark.live

# One client per test: pytest-asyncio gives every test its own event loop, and a
# cached keep-alive client cannot cross loops.
TIMEOUT_S = 20.0


@pytest.fixture
async def prometheus() -> AsyncIterator[PrometheusAdapter]:
    http = AsyncHttpClient(
        base_url="http://127.0.0.1:9090", timeout_s=TIMEOUT_S, retries=1, dependency="prometheus"
    )
    try:
        yield PrometheusAdapter(http=http, ttl_s=0)
    finally:
        await http.aclose()


@pytest.fixture
async def jaeger() -> AsyncIterator[JaegerAdapter]:
    http = AsyncHttpClient(
        base_url="http://127.0.0.1:16686/jaeger/ui",
        timeout_s=TIMEOUT_S,
        retries=1,
        dependency="jaeger",
    )
    try:
        yield JaegerAdapter(http=http, ttl_s=0)
    finally:
        await http.aclose()


@pytest.fixture
async def logs() -> AsyncIterator[LogsAdapter]:
    http = AsyncHttpClient(
        base_url="http://127.0.0.1:9200", timeout_s=TIMEOUT_S, retries=1, dependency="opensearch"
    )
    try:
        yield LogsAdapter(http=http, ttl_s=0)
    finally:
        await http.aclose()


@pytest.fixture
async def docker() -> AsyncIterator[DockerAdapter]:
    yield DockerAdapter(ttl_s=0)


async def test_confirmed_metric_names_still_exist(prometheus: PrometheusAdapter) -> None:
    names = await prometheus.metric_names()

    assert "traces_span_metrics_calls_total" in names
    assert "traces_span_metrics_duration_milliseconds_bucket" in names
    assert "container_cpu_utilization_ratio" in names


async def test_error_rate_returns_known_services(prometheus: PrometheusAdapter) -> None:
    result = await prometheus.metric("error_rate", window="5m")
    services = {series.labels.get("service_name") for series in result.series}

    assert {"payment", "checkout", "frontend"} <= services
    assert all(0.0 <= series.points[-1].v <= 1.0 for series in result.series)


async def test_p95_and_throughput_return_values(prometheus: PrometheusAdapter) -> None:
    p95 = await prometheus.metric("p95_latency", window="5m")
    throughput = await prometheus.metric("throughput", window="5m")

    assert p95.series
    assert throughput.series
    assert all(series.points[-1].v >= 0 for series in p95.series)
    assert all(series.points[-1].v >= 0 for series in throughput.series)


async def test_range_query_is_dense_and_fresh(prometheus: PrometheusAdapter) -> None:
    end = datetime.now(UTC)
    start = end - timedelta(minutes=5)
    result = await prometheus.metric("throughput", start=start, end=end, step_s=30)

    assert result.result_type == "range"
    assert len(result.series) >= 3
    # A freshly restarted demo has gaps for low-traffic services, so judge
    # density on the busiest series and freshness across all of them.
    densest = max(result.series, key=lambda item: len(item.points))
    assert len(densest.points) >= 5
    newest = max(point.t for series in result.series for point in series.points)
    newest_age_s = (end - newest).total_seconds()
    # The collector flushes every 10 s (infra/otel-collector/extras.yaml).
    assert newest_age_s < 120, f"metric sample is {newest_age_s:.0f}s old"


async def test_jaeger_services_and_trace_search(jaeger: JaegerAdapter) -> None:
    services = await jaeger.services()
    assert "payment" in services

    result = await jaeger.search(service="frontend", lookback_s=900, limit=5)
    assert result.traces, "expected recent frontend traces"
    summary = result.traces[0]
    assert summary.span_count >= 1
    assert summary.duration_ms >= 0
    assert summary.started_at <= datetime.now(UTC)

    detail = await jaeger.trace(summary.trace_id)
    assert detail.trace_id == summary.trace_id
    assert len(detail.spans) == summary.span_count
    assert detail.spans[0].depth == 0


async def test_jaeger_error_filter_is_consistent(jaeger: JaegerAdapter) -> None:
    result = await jaeger.search(lookback_s=900, limit=10, errors_only=True)

    assert all(trace.error_count > 0 for trace in result.traces)


async def test_logs_search_returns_demo_logs(logs: LogsAdapter) -> None:
    result = await logs.search(lookback_s=900, limit=20)

    assert result.returned > 0
    assert result.total >= result.returned
    assert result.index.startswith("otel-logs")
    assert any(record.service != "unknown" for record in result.records)
    assert all(record.timestamp.tzinfo is not None for record in result.records)


async def test_logs_are_trace_linkable(logs: LogsAdapter) -> None:
    recent = await logs.search(lookback_s=900, limit=50)
    linked = next((record for record in recent.records if record.trace_id), None)

    if linked is None:
        pytest.skip("no trace-linked log in the last 15 minutes")

    by_trace = await logs.search(trace_id=linked.trace_id, lookback_s=900, limit=20)
    assert by_trace.records
    assert all(record.trace_id == linked.trace_id for record in by_trace.records)


async def test_docker_sees_the_demo_project(docker: DockerAdapter) -> None:
    containers = await docker.containers()

    assert len(containers) >= 20
    payment = next(container for container in containers if container.service == "payment")
    assert payment.state == "running"
    assert payment.memory_limit_mb is not None
    assert payment.networks

    stats = [container for container in containers if container.cpu_percent is not None]
    assert stats, "expected docker stats to be merged in"


async def test_inventory_joins_live_sources() -> None:
    inventory = await ServiceInventoryBuilder().build("5m")
    by_name = {service.name: service for service in inventory.services}

    assert "payment" in by_name
    payment = by_name["payment"]
    assert "metrics" in payment.sources
    assert payment.container == "payment"
    assert payment.state == "running"
    assert inventory.containers_running >= 20
