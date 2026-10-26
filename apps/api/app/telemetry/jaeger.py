"""Jaeger v3 adapter.

The demo runs Jaeger v2 with `base_path: /jaeger/ui`, so the query API lives at
`/jaeger/ui/api/v3/*` — **not** `/api/*`. Confirmed on this host:

- `GET  /jaeger/ui/api/v3/services` → `{"services": [...]}`
- `GET  /jaeger/ui/api/v3/traces?query.service_name=…&query.startTimeMin=…
        &query.startTimeMax=…&query.num_traces=…`
  → OTLP JSON: `{"result": {"resourceSpans": [...]}}`. The parameter casing is
  mixed and only these names work: `startTimeMin`/`startTimeMax` (camelCase,
  both required) but `num_traces` (snake_case). Verified on this host:
  `query.numTraces` is silently ignored and the server returns its default 100
  traces.
- `GET  /jaeger/ui/api/v3/traces/{trace_id}` → the same envelope for one trace.

Jaeger stores spans in memory (25k traces), so this adapter is for recent
investigation, not history. Evidence that must outlive retention is snapshotted
into Postgres by the evidence engine (P9).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import settings
from app.errors import ApiError
from app.telemetry.cache import TTLCache
from app.telemetry.dto import SpanNode, TraceDetail, TraceSearchResult, TraceSummary
from app.telemetry.http import AsyncHttpClient, ensure_ok, shared_client

TRACE_ID_RE = re.compile(r"^[0-9a-f]{16,32}$")

KIND_NAMES = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}

MAX_LOOKBACK_S = 3600
MIN_LOOKBACK_S = 60

# Attributes worth showing in the waterfall; resource attributes are noise here.
_ATTRIBUTE_PREFIXES = (
    "http.",
    "rpc.",
    "db.",
    "messaging.",
    "error.",
    "exception.",
    "url.",
    "server.",
    "client.",
    "network.",
    "peer.",
    "code.",
    # Envoy's access-log flags: DI/FI mark a fault the proxy injected on request.
    "response_flags",
    "upstream_cluster",
)


def _attr_value(value: dict[str, Any]) -> str:
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in value:
            return str(value[key])
    if "arrayValue" in value:
        return json.dumps(value["arrayValue"], separators=(",", ":"))
    if "kvlistValue" in value:
        return json.dumps(value["kvlistValue"], separators=(",", ":"))
    return ""


def _attributes(raw: list[dict[str, Any]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw or []:
        key = str(item.get("key", ""))
        if not key:
            continue
        out[key] = _attr_value(item.get("value") or {})
    return out


def _service_name(resource: dict[str, Any] | None) -> str:
    for item in (resource or {}).get("attributes") or []:
        if item.get("key") == "service.name":
            return _attr_value(item.get("value") or {}) or "unknown"
    return "unknown"


@dataclass(slots=True)
class _RawSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    service: str
    kind: str
    start_ns: int
    end_ns: int
    status: str
    attributes: dict[str, str]

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_ns - self.start_ns) / 1_000_000)


def _status_name(status: dict[str, Any] | None) -> str:
    code = (status or {}).get("code")
    if code == 2:
        return "error"
    if code == 1:
        return "ok"
    # Some SDKs only set `message` on errors.
    if (status or {}).get("message"):
        return "error"
    return "unset"


def _extract_spans(result: dict[str, Any]) -> list[_RawSpan]:
    spans: list[_RawSpan] = []
    for resource_span in result.get("resourceSpans") or []:
        service = _service_name(resource_span.get("resource"))
        for scope_span in resource_span.get("scopeSpans") or []:
            for span in scope_span.get("spans") or []:
                trace_id = str(span.get("traceId") or "")
                span_id = str(span.get("spanId") or "")
                if not trace_id or not span_id:
                    continue
                parent = span.get("parentSpanId") or None
                spans.append(
                    _RawSpan(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=str(parent) if parent else None,
                        name=str(span.get("name") or "unnamed"),
                        service=service,
                        kind=KIND_NAMES.get(int(span.get("kind") or 0), "unspecified"),
                        start_ns=int(span.get("startTimeUnixNano") or 0),
                        end_ns=int(span.get("endTimeUnixNano") or 0),
                        status=_status_name(span.get("status")),
                        attributes=_attributes(span.get("attributes")),
                    )
                )
    return spans


def _group_by_trace(spans: list[_RawSpan]) -> dict[str, list[_RawSpan]]:
    grouped: dict[str, list[_RawSpan]] = {}
    for span in spans:
        grouped.setdefault(span.trace_id, []).append(span)
    return grouped


def _summary(spans: list[_RawSpan]) -> TraceSummary:
    ordered = sorted(spans, key=lambda span: (span.start_ns, span.span_id))
    root = next((span for span in ordered if span.parent_span_id is None), ordered[0])
    started_ns = ordered[0].start_ns
    duration_ms = max(span.duration_ms for span in ordered)
    services = sorted({span.service for span in ordered})
    return TraceSummary(
        trace_id=root.trace_id,
        root_name=root.name,
        root_service=root.service,
        started_at=datetime.fromtimestamp(started_ns / 1e9, UTC),
        duration_ms=round(duration_ms, 3),
        span_count=len(ordered),
        services=services,
        error_count=sum(1 for span in ordered if span.status == "error"),
    )


def _detail(spans: list[_RawSpan]) -> TraceDetail:
    summary = _summary(spans)
    ordered = sorted(spans, key=lambda span: (span.start_ns, span.span_id))
    start_ns = ordered[0].start_ns

    children: dict[str | None, list[_RawSpan]] = {}
    ids = {span.span_id for span in ordered}
    for span in ordered:
        parent = span.parent_span_id if span.parent_span_id in ids else None
        children.setdefault(parent, []).append(span)

    nodes: list[SpanNode] = []

    def walk(parent: str | None, depth: int) -> None:
        for span in sorted(children.get(parent, []), key=lambda item: item.start_ns):
            attrs = {
                key: value[:200]
                for key, value in span.attributes.items()
                if key.startswith(_ATTRIBUTE_PREFIXES)
            }
            nodes.append(
                SpanNode(
                    span_id=span.span_id,
                    parent_span_id=span.parent_span_id,
                    name=span.name,
                    service=span.service,
                    kind=span.kind,
                    start_ms=round((span.start_ns - start_ns) / 1_000_000, 3),
                    duration_ms=round(span.duration_ms, 3),
                    status=span.status,  # type: ignore[arg-type]
                    depth=depth,
                    attributes=dict(list(attrs.items())[:16]),
                )
            )
            walk(span.span_id, depth + 1)

    walk(None, 0)

    # Spans whose parent chain is broken still need to appear.
    seen = {node.span_id for node in nodes}
    for span in ordered:
        if span.span_id in seen:
            continue
        nodes.append(
            SpanNode(
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                name=span.name,
                service=span.service,
                kind=span.kind,
                start_ms=round((span.start_ns - start_ns) / 1_000_000, 3),
                duration_ms=round(span.duration_ms, 3),
                status=span.status,  # type: ignore[arg-type]
                depth=0,
            )
        )

    return TraceDetail(**summary.model_dump(), spans=nodes)


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JaegerAdapter:
    def __init__(
        self,
        http: AsyncHttpClient | None = None,
        ttl_s: float | None = None,
    ) -> None:
        self._http = http or shared_client(
            "jaeger",
            base_url=settings.jaeger_api_url,
            timeout_s=max(settings.http_timeout_s, 10.0),
        )
        self._cache = TTLCache(ttl_s if ttl_s is not None else settings.telemetry_cache_ttl_s)

    async def services(self) -> list[str]:
        async def load() -> list[str]:
            payload = await self._http.get_json("/api/v3/services")
            return sorted(payload.get("services") or [])

        return await self._cache.get(("services",), load)

    async def search(
        self,
        *,
        service: str | None = None,
        operation: str | None = None,
        lookback_s: int = 900,
        limit: int = 20,
        errors_only: bool = False,
        min_duration_ms: float | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> TraceSearchResult:
        """Search the last `lookback_s`, or the fixed `[start, end]` window.

        A fixed window makes a search repeatable: re-analysing a closed incident
        must see the traces of that incident, not whatever arrived since.
        """
        limit = max(1, min(100, limit))
        # Errors are sparse; fetch a wider slice before filtering client-side.
        fetch = min(100, limit * 4) if errors_only or min_duration_ms or operation else limit

        window = (_rfc3339(start), _rfc3339(end)) if start and end else None
        end = end or datetime.now(UTC)
        if start is None:
            lookback = max(MIN_LOOKBACK_S, min(MAX_LOOKBACK_S, lookback_s))
            start = end - timedelta(seconds=lookback)
        else:
            start = max(start, end - timedelta(seconds=MAX_LOOKBACK_S))
            lookback = int((end - start).total_seconds())
        params: dict[str, str] = {
            "query.startTimeMin": _rfc3339(start),
            "query.startTimeMax": _rfc3339(end),
            "query.num_traces": str(fetch),
        }
        if service:
            if not re.match(r"^[a-z0-9][a-z0-9._-]{0,62}$", service):
                raise ApiError(422, "invalid_service", f"Invalid service name '{service}'")
            params["query.service_name"] = service

        async def load() -> list[TraceSummary]:
            response = await self._http.request("GET", "/api/v3/traces", params=params)
            # Jaeger v3 answers 404 {"message": "No traces found"} for an empty
            # result — that is a normal search outcome, not a dependency failure.
            if response.status_code == 404:
                return []
            ensure_ok(response)
            payload = response.json()
            result = payload.get("result") or {}
            grouped = _group_by_trace(_extract_spans(result))
            return [_summary(spans) for spans in grouped.values()]

        key = (
            "search",
            service,
            operation,
            lookback,
            fetch,
            errors_only,
            min_duration_ms,
            window,
        )
        summaries = await self._cache.get(key, load)

        filtered = summaries
        if errors_only:
            filtered = [trace for trace in filtered if trace.error_count > 0]
        if min_duration_ms is not None:
            filtered = [trace for trace in filtered if trace.duration_ms >= min_duration_ms]
        if operation:
            needle = operation.lower()
            filtered = [trace for trace in filtered if needle in trace.root_name.lower()]
        filtered.sort(key=lambda trace: trace.started_at, reverse=True)

        return TraceSearchResult(
            traces=filtered[:limit],
            lookback_s=lookback,
            scanned=len(summaries),
        )

    async def trace(self, trace_id: str) -> TraceDetail:
        if not TRACE_ID_RE.match(trace_id):
            raise ApiError(422, "invalid_trace_id", "Trace id must be 16-32 lowercase hex chars")

        async def load() -> TraceDetail:
            response = await self._http.request("GET", f"/api/v3/traces/{trace_id}")
            if response.status_code == 404:
                raise ApiError(404, "trace_not_found", f"Trace {trace_id} not found")
            ensure_ok(response)
            payload = response.json()
            result = payload.get("result") or {}
            spans = _extract_spans(result)
            if not spans:
                raise ApiError(404, "trace_not_found", f"Trace {trace_id} not found")
            return _detail(spans)

        return await self._cache.get(("trace", trace_id), load)


__all__ = ["MAX_LOOKBACK_S", "MIN_LOOKBACK_S", "JaegerAdapter"]
