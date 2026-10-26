"""OpenSearch log adapter.

Index pattern is `otel-logs*`; one index per day (`otel-logs-2026-10-24`).
Document shape confirmed on this host:

    {
      "@timestamp": "2026-10-24T05:54:54.794Z",
      "observedTimestamp": "...",
      "body": "Receive ListRecommendations for product ids:[...]",
      "severity": {"text": "info", "number": 9},
      "resource": {"service.name": "recommendation", ...},
      "traceId": "...", "spanId": "...",
      "attributes": {"data_stream": {...}, ...}
    }

`body` is mapped as text (with a keyword sub-field in some templates), so search
uses `match_phrase`, not a term query.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.errors import ApiError
from app.telemetry.cache import TTLCache
from app.telemetry.dto import LogRecord, LogSearchResult
from app.telemetry.http import AsyncHttpClient, shared_client

SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
LEVELS = ("trace", "debug", "info", "warn", "error", "fatal")
MAX_LOOKBACK_S = 24 * 3600
MAX_QUERY_LEN = 200


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        # OpenSearch stores nanoseconds; fromisoformat on 3.11 accepts 9 digits?
        # It does not, so trim to microseconds.
        match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{1,9})(.*)$", text)
        if match:
            fraction = (match.group(2) + "000000")[:6]
            text = f"{match.group(1)}.{fraction}{match.group(3)}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _flatten_attributes(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "data_stream":
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)[:200]
        if len(out) >= 12:
            break
    return out


def _parse_hit(source: dict[str, Any]) -> LogRecord:
    resource = source.get("resource") or {}
    severity = source.get("severity") or {}
    return LogRecord(
        timestamp=_parse_timestamp(source.get("@timestamp") or source.get("observedTimestamp")),
        service=str(resource.get("service.name") or "unknown"),
        severity=str(severity.get("text") or "info"),
        severity_number=severity.get("number"),
        body=str(source.get("body") or ""),
        trace_id=source.get("traceId"),
        span_id=source.get("spanId"),
        attributes=_flatten_attributes(source.get("attributes")),
    )


class LogsAdapter:
    def __init__(
        self,
        http: AsyncHttpClient | None = None,
        ttl_s: float | None = None,
    ) -> None:
        self._http = http or shared_client(
            "opensearch", base_url=settings.opensearch_url, timeout_s=settings.http_timeout_s
        )
        self._index = settings.opensearch_index
        self._cache = TTLCache(ttl_s if ttl_s is not None else settings.telemetry_cache_ttl_s)

    def _build_query(
        self,
        *,
        service: str | None,
        level: str | None,
        text: str | None,
        trace_id: str | None,
        lookback_s: int,
        limit: int,
    ) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": f"now-{lookback_s}s", "lte": "now"}}}
        ]
        must: list[dict[str, Any]] = []

        if service:
            if not SERVICE_RE.match(service):
                raise ApiError(422, "invalid_service", f"Invalid service name '{service}'")
            filters.append({"term": {"resource.service.name": service}})

        if trace_id:
            if not re.match(r"^[0-9a-f]{16,32}$", trace_id):
                raise ApiError(
                    422, "invalid_trace_id", "Trace id must be 16-32 lowercase hex chars"
                )
            filters.append({"term": {"traceId": trace_id}})

        if level:
            normalized = level.lower()
            if normalized not in LEVELS:
                raise ApiError(422, "invalid_level", f"level must be one of {', '.join(LEVELS)}")
            filters.append({"terms": {"severity.text": [normalized, normalized.upper()]}})

        if text:
            if len(text) > MAX_QUERY_LEN:
                raise ApiError(422, "invalid_query", f"query is limited to {MAX_QUERY_LEN} chars")
            must.append({"match_phrase": {"body": text}})

        return {
            "size": limit,
            "track_total_hits": True,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": filters, "must": must}},
        }

    async def search(
        self,
        *,
        service: str | None = None,
        level: str | None = None,
        text: str | None = None,
        trace_id: str | None = None,
        lookback_s: int = 900,
        limit: int = 50,
    ) -> LogSearchResult:
        lookback = max(60, min(MAX_LOOKBACK_S, lookback_s))
        limit = max(1, min(200, limit))
        body = self._build_query(
            service=service,
            level=level,
            text=text,
            trace_id=trace_id,
            lookback_s=lookback,
            limit=limit,
        )

        async def load() -> LogSearchResult:
            payload = await self._http.request("POST", f"/{self._index}/_search", json=body)
            if payload.status_code >= 400:
                detail = payload.text[:300]
                raise ApiError(
                    502, "opensearch_error", f"OpenSearch returned {payload.status_code}: {detail}"
                )
            result = payload.json()
            hits = result.get("hits") or {}
            total: Any = hits.get("total")
            if isinstance(total, dict):
                total = total.get("value", 0)
            records = [_parse_hit(hit.get("_source") or {}) for hit in hits.get("hits") or []]
            return LogSearchResult(
                total=int(total or 0),
                returned=len(records),
                took_ms=int(result.get("took") or 0),
                index=self._index,
                lookback_s=lookback,
                records=records,
            )

        key = ("logs", service, level, text, trace_id, lookback, limit)
        return await self._cache.get(key, load)


__all__ = ["LEVELS", "LogsAdapter"]
