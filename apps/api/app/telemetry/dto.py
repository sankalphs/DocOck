"""Typed wire models for every telemetry adapter.

The API never returns a raw upstream payload: Prometheus matrices, OTLP spans,
OpenSearch hits, and `docker inspect` blobs all get normalised here. The
dashboard's generated client is built from these models, so a shape change is a
type error rather than a blank chart.

Two rules encoded below:

- Timestamps are timezone-aware UTC.
- NaN/Inf are dropped, never serialised. `float('nan')` is invalid JSON and
  `histogram_quantile` emits it for empty buckets; a single NaN would break
  every `JSON.parse` in the browser.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MetricDimension = Literal["service", "container"]
SpanStatus = Literal["unset", "ok", "error"]


class MetricPoint(BaseModel):
    t: datetime
    v: float


class MetricSeries(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[MetricPoint] = Field(default_factory=list)


class MetricResult(BaseModel):
    metric: str
    label: str
    unit: str
    dimension: MetricDimension
    query: str
    result_type: Literal["instant", "range"]
    step_s: int | None = None
    series: list[MetricSeries] = Field(default_factory=list)


class MetricSpec(BaseModel):
    id: str
    label: str
    description: str
    unit: str
    dimension: MetricDimension
    default_window: str


class SpanNode(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    name: str
    service: str
    kind: str
    start_ms: float
    duration_ms: float
    status: SpanStatus
    depth: int = 0
    attributes: dict[str, str] = Field(default_factory=dict)


class TraceSummary(BaseModel):
    trace_id: str
    root_name: str
    root_service: str
    started_at: datetime
    duration_ms: float
    span_count: int
    services: list[str] = Field(default_factory=list)
    error_count: int = 0

    @property
    def has_error(self) -> bool:
        return self.error_count > 0


class TraceDetail(TraceSummary):
    spans: list[SpanNode] = Field(default_factory=list)


class TraceSearchResult(BaseModel):
    traces: list[TraceSummary] = Field(default_factory=list)
    lookback_s: int
    scanned: int = 0


class LogRecord(BaseModel):
    timestamp: datetime
    service: str
    severity: str
    severity_number: int | None = None
    body: str
    trace_id: str | None = None
    span_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LogSearchResult(BaseModel):
    total: int
    returned: int
    took_ms: int
    index: str
    lookback_s: int
    records: list[LogRecord] = Field(default_factory=list)


class ContainerInfo(BaseModel):
    name: str
    service: str
    image: str
    state: str
    health: str
    paused: bool = False
    oom_killed: bool = False
    restart_count: int = 0
    started_at: datetime | None = None
    cpu_limit_cores: float | None = None
    memory_limit_mb: int | None = None
    networks: list[str] = Field(default_factory=list)
    status_text: str = ""
    cpu_percent: float | None = None
    memory_used_mb: float | None = None
    memory_percent: float | None = None


class DockerEvent(BaseModel):
    at: datetime
    action: str
    container: str
    service: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class ServiceInfo(BaseModel):
    name: str
    container: str | None = None
    state: str = "unknown"
    health: str = "none"
    restart_count: int = 0
    error_rate: float | None = None
    p95_ms: float | None = None
    throughput_rps: float | None = None
    cpu_percent: float | None = None
    memory_mb: float | None = None
    cpu_limit_cores: float | None = None
    memory_limit_mb: int | None = None
    sources: list[str] = Field(default_factory=list)


class ServiceInventory(BaseModel):
    services: list[ServiceInfo] = Field(default_factory=list)
    generated_at: datetime
    window_s: int
    containers_total: int = 0
    containers_running: int = 0
