import { JAEGER_UI_URL } from "./base-url";
import { request } from "./client";
import type { components } from "./schema";

/**
 * View types with no optional fields.
 *
 * The generated OpenAPI types mark every Pydantic field with a default as
 * optional (`series?: …`), even though the API always sends an array. Instead of
 * defending against that in every component, the fetchers normalise once here.
 */
export type MetricPoint = { t: string; v: number };
export type MetricSeries = { labels: Record<string, string>; points: MetricPoint[] };
export type MetricResult = {
  metric: string;
  label: string;
  unit: string;
  dimension: "service" | "container";
  query: string;
  result_type: "instant" | "range";
  step_s: number | null;
  series: MetricSeries[];
};
export type MetricSpec = {
  id: string;
  label: string;
  description: string;
  unit: string;
  dimension: "service" | "container";
  default_window: string;
};
export type SpanNode = {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  service: string;
  kind: string;
  start_ms: number;
  duration_ms: number;
  status: "unset" | "ok" | "error";
  depth: number;
  attributes: Record<string, string>;
};
export type TraceSummary = {
  trace_id: string;
  root_name: string;
  root_service: string;
  started_at: string;
  duration_ms: number;
  span_count: number;
  services: string[];
  error_count: number;
};
export type TraceDetail = TraceSummary & { spans: SpanNode[] };
export type TraceSearchResult = {
  traces: TraceSummary[];
  lookback_s: number;
  scanned: number;
};
export type LogRecord = {
  timestamp: string;
  service: string;
  severity: string;
  severity_number: number | null;
  body: string;
  trace_id: string | null;
  span_id: string | null;
  attributes: Record<string, unknown>;
};
export type LogSearchResult = {
  total: number;
  returned: number;
  took_ms: number;
  index: string;
  lookback_s: number;
  records: LogRecord[];
};
export type ServiceInfo = {
  name: string;
  container: string | null;
  state: string;
  health: string;
  restart_count: number;
  error_rate: number | null;
  p95_ms: number | null;
  throughput_rps: number | null;
  cpu_percent: number | null;
  memory_mb: number | null;
  cpu_limit_cores: number | null;
  memory_limit_mb: number | null;
  sources: string[];
};
export type ServiceInventory = {
  services: ServiceInfo[];
  generated_at: string;
  window_s: number;
  containers_total: number;
  containers_running: number;
};

type RawMetricResult = components["schemas"]["MetricResult"];
type RawTraceSearch = components["schemas"]["TraceSearchResult"];
type RawTraceDetail = components["schemas"]["TraceDetail"];
type RawLogSearch = components["schemas"]["LogSearchResult"];
type RawInventory = components["schemas"]["ServiceInventory"];

function mapSeries(item: { labels?: Record<string, string>; points?: MetricPoint[] }): MetricSeries {
  return { labels: item.labels ?? {}, points: item.points ?? [] };
}

function mapMetric(raw: RawMetricResult): MetricResult {
  return {
    metric: raw.metric,
    label: raw.label,
    unit: raw.unit,
    dimension: raw.dimension,
    query: raw.query,
    result_type: raw.result_type,
    step_s: raw.step_s ?? null,
    series: (raw.series ?? []).map(mapSeries),
  };
}

function mapTrace(raw: {
  trace_id: string;
  root_name: string;
  root_service: string;
  started_at: string;
  duration_ms: number;
  span_count: number;
  services?: string[];
  error_count: number;
}): TraceSummary {
  return {
    trace_id: raw.trace_id,
    root_name: raw.root_name,
    root_service: raw.root_service,
    started_at: raw.started_at,
    duration_ms: raw.duration_ms,
    span_count: raw.span_count,
    services: raw.services ?? [],
    error_count: raw.error_count ?? 0,
  };
}

function mapTraceDetail(raw: RawTraceDetail): TraceDetail {
  return {
    ...mapTrace(raw),
    spans: (raw.spans ?? []).map((span) => ({
      span_id: span.span_id,
      parent_span_id: span.parent_span_id ?? null,
      name: span.name,
      service: span.service,
      kind: span.kind,
      start_ms: span.start_ms,
      duration_ms: span.duration_ms,
      status: span.status,
      depth: span.depth ?? 0,
      attributes: span.attributes ?? {},
    })),
  };
}

function mapInventory(raw: RawInventory): ServiceInventory {
  return {
    services: (raw.services ?? []).map((service) => ({
      name: service.name,
      container: service.container ?? null,
      state: service.state ?? "unknown",
      health: service.health ?? "none",
      restart_count: service.restart_count ?? 0,
      error_rate: service.error_rate ?? null,
      p95_ms: service.p95_ms ?? null,
      throughput_rps: service.throughput_rps ?? null,
      cpu_percent: service.cpu_percent ?? null,
      memory_mb: service.memory_mb ?? null,
      cpu_limit_cores: service.cpu_limit_cores ?? null,
      memory_limit_mb: service.memory_limit_mb ?? null,
      sources: service.sources ?? [],
    })),
    generated_at: raw.generated_at,
    window_s: raw.window_s,
    containers_total: raw.containers_total ?? 0,
    containers_running: raw.containers_running ?? 0,
  };
}

function mapLogs(raw: RawLogSearch): LogSearchResult {
  return {
    total: raw.total,
    returned: raw.returned,
    took_ms: raw.took_ms,
    index: raw.index,
    lookback_s: raw.lookback_s,
    records: (raw.records ?? []).map((record) => ({
      timestamp: record.timestamp,
      service: record.service,
      severity: record.severity,
      severity_number: record.severity_number ?? null,
      body: record.body,
      trace_id: record.trace_id ?? null,
      span_id: record.span_id ?? null,
      attributes: record.attributes ?? {},
    })),
  };
}

export type MetricQuery = {
  metric: string;
  service?: string;
  window?: string;
  rangeSeconds?: number;
  step?: number;
};

function isoSecondsAgo(seconds: number): string {
  return new Date(Date.now() - seconds * 1000).toISOString();
}

export async function fetchMetric(
  query: MetricQuery,
  signal?: AbortSignal,
): Promise<MetricResult> {
  const params = new URLSearchParams({ metric: query.metric });
  if (query.service) params.set("service", query.service);
  if (query.window) params.set("window", query.window);
  if (query.rangeSeconds) {
    params.set("start", isoSecondsAgo(query.rangeSeconds));
    params.set("end", new Date().toISOString());
    if (query.step) params.set("step", String(query.step));
  }
  const raw = await request<RawMetricResult>(`/api/telemetry/metrics?${params.toString()}`, {
    signal,
  });
  return mapMetric(raw);
}

export async function fetchServices(
  window = "5m",
  signal?: AbortSignal,
): Promise<ServiceInventory> {
  const raw = await request<RawInventory>(`/api/telemetry/services?window=${window}`, { signal });
  return mapInventory(raw);
}

export type TraceQuery = {
  service?: string;
  errorsOnly?: boolean;
  minDurationMs?: number;
  lookbackSeconds?: number;
  limit?: number;
};

export async function fetchTraces(
  query: TraceQuery,
  signal?: AbortSignal,
): Promise<TraceSearchResult> {
  const params = new URLSearchParams({
    lookback_s: String(query.lookbackSeconds ?? 900),
    limit: String(query.limit ?? 20),
  });
  if (query.service) params.set("service", query.service);
  if (query.errorsOnly) params.set("errors_only", "true");
  if (query.minDurationMs) params.set("min_duration_ms", String(query.minDurationMs));
  const raw = await request<RawTraceSearch>(`/api/telemetry/traces?${params.toString()}`, {
    signal,
  });
  return {
    traces: (raw.traces ?? []).map(mapTrace),
    lookback_s: raw.lookback_s,
    scanned: raw.scanned ?? 0,
  };
}

export async function fetchTrace(traceId: string, signal?: AbortSignal): Promise<TraceDetail> {
  const raw = await request<RawTraceDetail>(`/api/telemetry/traces/${traceId}`, { signal });
  return mapTraceDetail(raw);
}

export type LogQuery = {
  service?: string;
  level?: string;
  q?: string;
  traceId?: string;
  lookbackSeconds?: number;
  limit?: number;
};

export async function fetchLogs(
  query: LogQuery,
  signal?: AbortSignal,
): Promise<LogSearchResult> {
  const params = new URLSearchParams({
    lookback_s: String(query.lookbackSeconds ?? 900),
    limit: String(query.limit ?? 50),
  });
  if (query.service) params.set("service", query.service);
  if (query.level) params.set("level", query.level);
  if (query.q) params.set("q", query.q);
  if (query.traceId) params.set("trace_id", query.traceId);
  const raw = await request<RawLogSearch>(`/api/telemetry/logs?${params.toString()}`, { signal });
  return mapLogs(raw);
}

/** Link to the Jaeger UI trace view (read-only, for cross-checking). */
export function jaegerTraceUrl(traceId: string): string {
  return `${JAEGER_UI_URL}/trace/${traceId}`;
}
