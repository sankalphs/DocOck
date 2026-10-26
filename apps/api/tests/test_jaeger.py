from __future__ import annotations

import copy
from datetime import UTC, datetime

import httpx
import pytest

from app.errors import ApiError
from app.telemetry.http import AsyncHttpClient
from app.telemetry.jaeger import JaegerAdapter, _detail, _extract_spans, _group_by_trace, _summary
from tests.conftest import FixtureFactory, FixtureTransport, load_fixture


def _adapter(transport: httpx.AsyncBaseTransport, ttl_s: float = 30.0) -> JaegerAdapter:
    http = AsyncHttpClient(
        base_url="http://jaeger.test/jaeger/ui",
        retries=0,
        dependency="jaeger",
        transport=transport,  # type: ignore[arg-type]
    )
    return JaegerAdapter(http=http, ttl_s=ttl_s)


def _spans(name: str = "jaeger_traces.json"):  # type: ignore[no-untyped-def]
    payload = load_fixture(name)
    return _extract_spans(payload["result"])


def test_extract_spans_reads_service_and_status() -> None:
    spans = _spans()
    assert spans
    assert all(span.trace_id and span.span_id for span in spans)
    assert all(span.service for span in spans)
    assert {span.kind for span in spans} <= {
        "unspecified",
        "internal",
        "server",
        "client",
        "producer",
        "consumer",
    }


def test_summary_reports_root_duration_and_services() -> None:
    grouped = _group_by_trace(_spans())
    assert len(grouped) == 1
    summary = _summary(next(iter(grouped.values())))

    assert summary.span_count == len(next(iter(grouped.values())))
    assert summary.duration_ms > 0
    # Jaeger returns whole traces: searching for `cart` still yields the
    # frontend/browser root span, and `cart` appears in `services`.
    assert summary.root_service
    assert "cart" in summary.services
    assert summary.error_count == 0
    assert summary.started_at.tzinfo is not None


def test_detail_orders_parents_before_children() -> None:
    spans = _spans("jaeger_trace.json")
    detail = _detail(spans)

    assert len(detail.spans) == len(spans)
    assert detail.spans[0].depth == 0
    assert detail.spans[0].parent_span_id is None
    assert detail.spans[0].start_ms == 0.0

    positions = {node.span_id: index for index, node in enumerate(detail.spans)}
    for node in detail.spans:
        if node.parent_span_id in positions:
            assert positions[node.parent_span_id] < positions[node.span_id]
            assert (
                node.depth
                == next(
                    parent.depth for parent in detail.spans if parent.span_id == node.parent_span_id
                )
                + 1
            )
    assert all(node.duration_ms >= 0 for node in detail.spans)


async def test_search_returns_summaries(fixture_transport: FixtureFactory) -> None:
    adapter = _adapter(fixture_transport({"api/v3/traces": load_fixture("jaeger_traces.json")}))

    result = await adapter.search(service="cart", limit=5)

    assert result.lookback_s == 900
    assert len(result.traces) == 1
    trace = result.traces[0]
    assert trace.trace_id
    assert "cart" in trace.services
    assert trace.span_count >= 1


async def test_search_can_filter_to_errors(fixture_transport: FixtureFactory) -> None:
    payload = copy.deepcopy(load_fixture("jaeger_traces.json"))
    # Mark one span as STATUS_CODE_ERROR.
    span = payload["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["status"] = {"code": 2, "message": "boom"}

    adapter = _adapter(fixture_transport({"api/v3/traces": payload}))
    errors = await adapter.search(service="cart", errors_only=True)
    everything = await adapter.search(service="cart", errors_only=False)

    assert len(errors.traces) == 1
    assert errors.traces[0].error_count >= 1
    assert everything.traces[0].error_count >= 1


async def test_trace_lookup_returns_waterfall(fixture_transport: FixtureFactory) -> None:
    trace_id = load_fixture("jaeger_trace_id.json")["trace_id"]
    adapter = _adapter(
        fixture_transport({f"api/v3/traces/{trace_id}": load_fixture("jaeger_trace.json")})
    )

    detail = await adapter.trace(trace_id)

    assert detail.trace_id == trace_id
    assert detail.spans
    assert detail.spans[0].depth == 0


async def test_invalid_trace_id_is_rejected_before_any_request() -> None:
    adapter = _adapter(FixtureTransport({}))  # never used
    with pytest.raises(ApiError) as caught:
        await adapter.trace("not-a-trace-id")
    assert caught.value.status_code == 422


async def test_unknown_trace_maps_to_404(fixture_transport: FixtureFactory) -> None:
    adapter = _adapter(fixture_transport({"api/v3/traces/": {"result": {"resourceSpans": []}}}))
    with pytest.raises(ApiError) as caught:
        await adapter.trace("0123456789abcdef0123456789abcdef")
    assert caught.value.status_code == 404
    assert caught.value.code == "trace_not_found"


async def test_empty_search_is_not_an_error() -> None:
    """Jaeger v3 returns 404 for 'No traces found'; that is an empty result."""

    class EmptySearch(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"httpCode": 404, "message": "No traces found"}},
                request=request,
            )

    adapter = _adapter(EmptySearch())

    result = await adapter.search(service="payment", lookback_s=300)

    assert result.traces == []
    assert result.scanned == 0


async def test_search_can_use_a_fixed_window() -> None:
    """Re-analysing a closed incident must query that incident's window."""
    seen: list[httpx.QueryParams] = []

    class Recording(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params)
            return httpx.Response(404, json={"message": "No traces found"}, request=request)

    adapter = _adapter(Recording())
    start = datetime(2026, 10, 25, 19, 0, tzinfo=UTC)
    end = datetime(2026, 10, 25, 19, 10, tzinfo=UTC)

    await adapter.search(service="payment", start=start, end=end)

    assert seen[0]["query.startTimeMin"].startswith("2026-10-25T19:00:00")
    assert seen[0]["query.startTimeMax"].startswith("2026-10-25T19:10:00")


async def test_jaeger_404_on_trace_is_not_found() -> None:
    class Missing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"httpCode": 404, "message": "trace not found"}},
                request=request,
            )

    adapter = _adapter(Missing())

    with pytest.raises(ApiError) as caught:
        await adapter.trace("0123456789abcdef0123456789abcdef")
    assert caught.value.status_code == 404


async def test_services_are_sorted(fixture_transport: FixtureFactory) -> None:
    adapter = _adapter(fixture_transport({"api/v3/services": load_fixture("jaeger_services.json")}))

    services = await adapter.services()

    assert services == sorted(services)
    assert "payment" in services
