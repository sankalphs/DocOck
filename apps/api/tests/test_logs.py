from __future__ import annotations

import httpx
import pytest

from app.errors import ApiError
from app.telemetry.http import AsyncHttpClient
from app.telemetry.logs import LogsAdapter
from tests.conftest import FixtureFactory, FixtureTransport, load_fixture


def _adapter(transport: httpx.AsyncBaseTransport, ttl_s: float = 30.0) -> LogsAdapter:
    http = AsyncHttpClient(
        base_url="http://opensearch.test",
        retries=0,
        dependency="opensearch",
        transport=transport,  # type: ignore[arg-type]
    )
    return LogsAdapter(http=http, ttl_s=ttl_s)


def test_query_uses_only_filters_for_allowed_fields() -> None:
    adapter = _adapter(FixtureTransport({}))
    body = adapter._build_query(
        service="payment",
        level="error",
        text="charge failed",
        trace_id="0123456789abcdef0123456789abcdef",
        lookback_s=900,
        limit=25,
    )

    assert body["size"] == 25
    assert body["track_total_hits"] is True
    assert body["sort"] == [{"@timestamp": {"order": "desc"}}]
    filters = body["query"]["bool"]["filter"]
    assert {"range": {"@timestamp": {"gte": "now-900s", "lte": "now"}}} in filters
    assert {"term": {"resource.service.name": "payment"}} in filters
    assert {"term": {"traceId": "0123456789abcdef0123456789abcdef"}} in filters
    assert {"terms": {"severity.text": ["error", "ERROR"]}} in filters
    assert body["query"]["bool"]["must"] == [{"match_phrase": {"body": "charge failed"}}]


def test_query_rejects_bad_input() -> None:
    adapter = _adapter(FixtureTransport({}))

    with pytest.raises(ApiError) as service:
        adapter._build_query(
            service="payment OR *",
            level=None,
            text=None,
            trace_id=None,
            lookback_s=60,
            limit=10,
        )
    assert service.value.code == "invalid_service"

    with pytest.raises(ApiError) as level:
        adapter._build_query(
            service=None, level="verbose", text=None, trace_id=None, lookback_s=60, limit=10
        )
    assert level.value.code == "invalid_level"

    with pytest.raises(ApiError) as trace:
        adapter._build_query(
            service=None, level=None, text=None, trace_id="XYZ", lookback_s=60, limit=10
        )
    assert trace.value.code == "invalid_trace_id"

    with pytest.raises(ApiError) as text:
        adapter._build_query(
            service=None, level=None, text="x" * 201, trace_id=None, lookback_s=60, limit=10
        )
    assert text.value.code == "invalid_query"


async def test_search_parses_recorded_hits(fixture_transport: FixtureFactory) -> None:
    payload = load_fixture("opensearch_logs.json")
    adapter = _adapter(fixture_transport({"/otel-logs": payload}))

    result = await adapter.search(limit=5)

    assert result.total >= result.returned
    assert result.returned == len(result.records) > 0
    record = result.records[0]
    assert record.timestamp.tzinfo is not None
    assert record.service
    assert record.severity
    assert isinstance(record.body, str)
    assert record.trace_id is None or len(record.trace_id) == 32


async def test_trace_linked_logs_keep_the_trace_id(fixture_transport: FixtureFactory) -> None:
    payload = load_fixture("opensearch_logs.json")
    trace_ids = [
        hit["_source"].get("traceId")
        for hit in payload["hits"]["hits"]
        if hit["_source"].get("traceId")
    ]
    adapter = _adapter(fixture_transport({"/otel-logs": payload}))

    result = await adapter.search(trace_id=trace_ids[0] if trace_ids else "0" * 32)

    assert result.records
    assert any(record.trace_id for record in result.records) or not trace_ids


async def test_opensearch_error_is_a_502() -> None:
    import httpx

    class ErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"reason": "index_not_found_exception"}},
                request=request,
            )

    adapter = _adapter(ErrorTransport())

    with pytest.raises(ApiError) as caught:
        await adapter.search(limit=1)
    assert caught.value.status_code == 502
    assert caught.value.code == "opensearch_error"
