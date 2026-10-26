from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.errors import ApiError
from app.telemetry.http import AsyncHttpClient
from app.telemetry.prometheus import (
    CATALOG,
    PrometheusAdapter,
    build_query,
    error_rate_query,
    p95_query,
)
from tests.conftest import FixtureFactory, load_fixture


def _adapter(transport: httpx.AsyncBaseTransport, ttl_s: float = 30.0) -> PrometheusAdapter:
    http = AsyncHttpClient(
        base_url="http://prometheus.test",
        retries=0,
        dependency="prometheus",
        transport=transport,  # type: ignore[arg-type]
    )
    return PrometheusAdapter(http=http, ttl_s=ttl_s)


def test_error_rate_query_filters_on_status_code() -> None:
    query = error_rate_query("payment", "5m")
    assert 'status_code="STATUS_CODE_ERROR"' in query
    assert 'service_name="payment"' in query
    assert "or on(service_name)" in query  # services without errors report 0


def test_p95_query_uses_the_confirmed_bucket_metric() -> None:
    query = p95_query(None, "5m")
    assert "traces_span_metrics_duration_milliseconds_bucket" in query
    assert "histogram_quantile(0.95" in query
    assert "[5m]" in query


def test_build_query_rejects_unknown_metric() -> None:
    with pytest.raises(ApiError) as caught:
        build_query("raw_promql", None, "5m")
    assert caught.value.status_code == 404
    assert caught.value.code == "unknown_metric"


def test_build_query_rejects_bad_window_and_service() -> None:
    with pytest.raises(ApiError) as window:
        build_query("error_rate", None, "1s")
    assert window.value.code == "invalid_window"

    with pytest.raises(ApiError) as service:
        build_query("error_rate", 'payment"} or vector(1)', "5m")
    assert service.value.code == "invalid_service"


def test_catalog_covers_the_dimensions_the_ui_needs() -> None:
    assert set(CATALOG) == {
        "error_rate",
        "p95_latency",
        "throughput",
        "container_cpu",
        "container_memory",
    }
    assert CATALOG["container_cpu"].dimension == "container"
    assert CATALOG["error_rate"].dimension == "service"


async def test_instant_parses_a_recorded_vector(fixture_transport: FixtureFactory) -> None:
    payload = load_fixture("prometheus_instant.json")
    adapter = _adapter(fixture_transport({"api/v1/query": payload}))

    result = await adapter.metric("error_rate", window="5m")

    assert result.result_type == "instant"
    assert result.series, "expected at least one series from the fixture"
    first = result.series[0]
    assert first.labels["service_name"]
    assert len(first.points) == 1
    assert 0.0 <= first.points[0].v <= 1.0
    assert first.points[0].t.tzinfo is not None


async def test_range_parses_a_recorded_matrix(fixture_transport: FixtureFactory) -> None:
    payload = load_fixture("prometheus_range.json")
    adapter = _adapter(fixture_transport({"api/v1/query_range": payload}))
    end = datetime.now(UTC)
    start = end - timedelta(minutes=10)

    result = await adapter.metric("throughput", start=start, end=end, step_s=30)

    assert result.result_type == "range"
    assert result.step_s == 30
    assert result.series
    assert len(result.series[0].points) >= 2
    timestamps = [point.t for point in result.series[0].points]
    assert timestamps == sorted(timestamps)


async def test_histogram_nan_is_dropped(fixture_transport: FixtureFactory) -> None:
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"service_name": "flagd-ui"},
                    "value": [1792820927.4, "NaN"],
                },
                {
                    "metric": {"service_name": "cart"},
                    "value": [1792820927.4, "5.5"],
                },
            ],
        },
    }
    adapter = _adapter(fixture_transport({"api/v1/query": payload}))

    result = await adapter.metric("p95_latency", window="5m")

    assert [series.labels["service_name"] for series in result.series] == ["cart"]


async def test_metric_names_are_cached(fixture_transport: FixtureFactory) -> None:
    payload = load_fixture("prometheus_label_values.json")
    adapter = _adapter(fixture_transport({"api/v1/label/__name__/values": payload}), ttl_s=60)

    first = await adapter.metric_names()
    second = await adapter.metric_names()

    assert first is second
    assert "traces_span_metrics_calls_total" in first
    assert adapter._cache.hits >= 1
