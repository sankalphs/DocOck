"""Prometheus adapter.

Only a fixed catalog of queries is executable. The endpoint takes a metric id,
not PromQL, so a caller cannot smuggle an expensive query or a selector the
allowlist does not cover, and a demo upgrade that renames a metric fails one
named query instead of silently emptying a chart.

Confirmed against the live stack (2026-10-24):

- `traces_span_metrics_calls_total{service_name, status_code, span_kind, span_name}`
  where `status_code` is `STATUS_CODE_ERROR | STATUS_CODE_OK | STATUS_CODE_UNSET`.
- `traces_span_metrics_duration_milliseconds_bucket{..., le}`.
- `container_cpu_utilization_ratio` / `container_memory_usage_total_bytes`
  labelled by `container_name` (no `service_name`).
- Span metrics are OTLP-pushed, not scraped; freshness is the collector's
  `metrics_flush_interval`, lowered to 10 s by `infra/otel-collector/extras.yaml`.
  A 1-minute `rate()` window is therefore unreliable; the catalog's floor is 2m.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.errors import ApiError
from app.telemetry.cache import TTLCache
from app.telemetry.dto import MetricPoint, MetricResult, MetricSeries, MetricSpec
from app.telemetry.http import AsyncHttpClient, shared_client

CALLS = "traces_span_metrics_calls_total"
DURATION_BUCKETS = "traces_span_metrics_duration_milliseconds_bucket"
CPU = "container_cpu_utilization_ratio"
MEMORY = "container_memory_usage_total_bytes"

SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
ALLOWED_WINDOWS = ("2m", "5m", "10m", "15m", "30m", "1h")
DEFAULT_WINDOW = "5m"


def _selector(service: str | None, extra: dict[str, str] | None = None) -> str:
    parts = [f'{key}="{value}"' for key, value in (extra or {}).items()]
    if service:
        parts.append(f'service_name="{service}"')
    return "{" + ",".join(parts) + "}"


def error_rate_query(service: str | None, window: str) -> str:
    errors = _selector(service, {"status_code": "STATUS_CODE_ERROR"})
    calls = _selector(service)
    numerator = f"sum by (service_name) (rate({CALLS}{errors}[{window}]))"
    denominator = f"clamp_min(sum by (service_name) (rate({CALLS}{calls}[{window}])), 0.0001)"
    zeros = f"0 * sum by (service_name) (rate({CALLS}{calls}[{window}]))"
    # The `or` keeps services with traffic but no errors at 0 instead of blank.
    return f"({numerator} / {denominator}) or on(service_name) ({zeros})"


def p95_query(service: str | None, window: str) -> str:
    """Inbound (SERVER) latency, falling back to all spans.

    Client spans carry downstream and DNS latency, which is useful for tracing
    but not a service's own p95. The fallback keeps services that emit no SERVER
    spans (load-generator, flagd) visible. `> 0` drops the NaN that
    `histogram_quantile` returns for an empty bucket set, so the fallback is not
    shadowed by a NaN series.
    """
    server = _selector(service, {"span_kind": "SPAN_KIND_SERVER"})
    all_spans = _selector(service)
    server_q = (
        "histogram_quantile(0.95, "
        f"sum by (le, service_name) (rate({DURATION_BUCKETS}{server}[{window}])))"
    )
    all_q = (
        "histogram_quantile(0.95, "
        f"sum by (le, service_name) (rate({DURATION_BUCKETS}{all_spans}[{window}])))"
    )
    return f"({server_q} > 0) or {all_q}"


def throughput_query(service: str | None, window: str) -> str:
    return f"sum by (service_name) (rate({CALLS}{_selector(service)}[{window}]))"


def container_cpu_query(service: str | None, _window: str) -> str:
    selector = f'{{container_name="{service}"}}' if service else ""
    return f"sum by (container_name) ({CPU}{selector})"


def container_memory_query(service: str | None, _window: str) -> str:
    selector = f'{{container_name="{service}"}}' if service else ""
    return f"sum by (container_name) ({MEMORY}{selector}) / 1024 / 1024"


_BUILDERS = {
    "error_rate": error_rate_query,
    "p95_latency": p95_query,
    "throughput": throughput_query,
    "container_cpu": container_cpu_query,
    "container_memory": container_memory_query,
}

CATALOG: dict[str, MetricSpec] = {
    "error_rate": MetricSpec(
        id="error_rate",
        label="Error rate",
        description="Share of spans with STATUS_CODE_ERROR, per service.",
        unit="ratio",
        dimension="service",
        default_window=DEFAULT_WINDOW,
    ),
    "p95_latency": MetricSpec(
        id="p95_latency",
        label="p95 latency",
        description="95th percentile span duration, per service.",
        unit="ms",
        dimension="service",
        default_window=DEFAULT_WINDOW,
    ),
    "throughput": MetricSpec(
        id="throughput",
        label="Throughput",
        description="Span calls per second, per service.",
        unit="rps",
        dimension="service",
        default_window=DEFAULT_WINDOW,
    ),
    "container_cpu": MetricSpec(
        id="container_cpu",
        label="Container CPU",
        description="CPU utilisation as a percentage of one core (docker_stats receiver).",
        unit="%",
        dimension="container",
        default_window=DEFAULT_WINDOW,
    ),
    "container_memory": MetricSpec(
        id="container_memory",
        label="Container memory",
        description="Resident memory in MB reported by the docker_stats receiver.",
        unit="MB",
        dimension="container",
        default_window=DEFAULT_WINDOW,
    ),
}


def build_query(metric: str, service: str | None, window: str) -> tuple[MetricSpec, str]:
    spec = CATALOG.get(metric)
    if spec is None:
        raise ApiError(404, "unknown_metric", f"Unknown metric '{metric}'")
    if window not in ALLOWED_WINDOWS:
        raise ApiError(
            422,
            "invalid_window",
            f"window must be one of {', '.join(ALLOWED_WINDOWS)}",
        )
    if service is not None and not SERVICE_NAME_RE.match(service):
        raise ApiError(422, "invalid_service", f"Invalid service name '{service}'")
    return spec, _BUILDERS[metric](service, window)


def _parse_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class PrometheusAdapter:
    def __init__(
        self,
        http: AsyncHttpClient | None = None,
        ttl_s: float | None = None,
    ) -> None:
        self._http = http or shared_client(
            "prometheus", base_url=settings.prometheus_url, timeout_s=settings.http_timeout_s
        )
        self._cache = TTLCache(ttl_s if ttl_s is not None else settings.telemetry_cache_ttl_s)

    async def metric_names(self) -> list[str]:
        async def load() -> list[str]:
            payload = await self._http.get_json("/api/v1/label/__name__/values")
            return list(payload["data"])

        return await self._cache.get(("names",), load)

    async def label_values(self, label: str) -> list[str]:
        async def load() -> list[str]:
            payload = await self._http.get_json(f"/api/v1/label/{label}/values")
            return list(payload["data"])

        return await self._cache.get(("label", label), load)

    async def service_names(self) -> list[str]:
        names = await self.label_values("service_name")
        return sorted(name for name in names if SERVICE_NAME_RE.match(name))

    async def _query(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = await self._http.get_json(path, params=params)
        if payload.get("status") != "success":
            raise ApiError(502, "prometheus_error", str(payload.get("error") or "query failed"))
        return payload["data"]

    async def instant(self, query: str) -> list[MetricSeries]:
        data = await self._query("/api/v1/query", {"query": query})
        return self._parse(data, ranged=False)

    async def range(
        self, query: str, start: datetime, end: datetime, step_s: int
    ) -> list[MetricSeries]:
        data = await self._query(
            "/api/v1/query_range",
            {
                "query": query,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": str(step_s),
            },
        )
        return self._parse(data, ranged=True)

    @staticmethod
    def _parse(data: dict[str, Any], *, ranged: bool) -> list[MetricSeries]:
        series: list[MetricSeries] = []
        for item in data.get("result", []):
            labels = {
                key: value for key, value in (item.get("metric") or {}).items() if key != "__name__"
            }
            if ranged:
                points = []
                for ts, raw in item.get("values") or []:
                    value = _finite(raw)
                    if value is not None:
                        points.append(MetricPoint(t=_parse_timestamp(float(ts)), v=value))
            else:
                ts, raw = item.get("value") or (None, None)
                value = _finite(raw)
                points = (
                    [MetricPoint(t=_parse_timestamp(float(ts)), v=value)]
                    if value is not None and ts is not None
                    else []
                )
            if points:
                series.append(MetricSeries(labels=labels, points=points))
        return series

    async def metric(
        self,
        metric: str,
        *,
        service: str | None = None,
        window: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        step_s: int | None = None,
    ) -> MetricResult:
        effective_window = window or DEFAULT_WINDOW
        spec, query = build_query(metric, service, effective_window)

        if (start is None) != (end is None):
            raise ApiError(422, "invalid_range", "start and end must be provided together")

        if start is not None and end is not None:
            if end <= start:
                raise ApiError(422, "invalid_range", "end must be after start")
            span = (end - start).total_seconds()
            if span > 24 * 3600:
                raise ApiError(422, "invalid_range", "range is limited to 24 hours")
            step = step_s or max(5, min(300, int(span / 120) or 5))
            if not 5 <= step <= 300:
                raise ApiError(422, "invalid_step", "step must be between 5 and 300 seconds")

            async def load_range() -> list[MetricSeries]:
                return await self.range(query, start, end, step)

            series = await self._cache.get(
                ("range", metric, service, effective_window, start, end, step), load_range
            )
            return MetricResult(
                metric=spec.id,
                label=spec.label,
                unit=spec.unit,
                dimension=spec.dimension,
                query=query,
                result_type="range",
                step_s=step,
                series=series,
            )

        async def load_instant() -> list[MetricSeries]:
            return await self.instant(query)

        series = await self._cache.get(("instant", metric, service, effective_window), load_instant)
        return MetricResult(
            metric=spec.id,
            label=spec.label,
            unit=spec.unit,
            dimension=spec.dimension,
            query=query,
            result_type="instant",
            series=series,
        )


def catalog() -> list[MetricSpec]:
    return list(CATALOG.values())


__all__: list[str] = [
    "CATALOG",
    "MetricSpec",
    "PrometheusAdapter",
    "build_query",
    "catalog",
]
