# Architecture

Living document. Decisions live in [`ADR/`](ADR/); phase status and evidence in
[`PROGRESS.md`](PROGRESS.md); the target design in [`PLAN.md`](PLAN.md).

## Trust boundary

```
browser ──HTTP/SSE──► DocOck API ──► Prometheus / Jaeger / OpenSearch
   :3001                  :8001  ──► Docker CLI (read; writes from P6)
                                     └► Postgres 18 + pgvector :5433
```

The dashboard has no Docker socket, no telemetry connection, and no model key.
The API is the only process that reads infrastructure, and (from P6) the only
one that writes to it.

## Processes

| Piece | How it runs | Notes |
|---|---|---|
| `apps/api` | `uv run uvicorn app.main:app --port 8001` | one asyncio loop; keep-alive clients per dependency |
| `apps/dashboard` | `pnpm dev` on `127.0.0.1:3001` | Next.js 16 App Router, all telemetry fetched client-side |
| demo + observability | Compose project `docock-demo` | 25 containers, label `com.docker.compose.project=docock-demo` |
| Postgres/pgvector | Compose project `docock-lab` | Alembic owns the schema |

## Telemetry contracts

These are the **verified** shapes as of 2026-10-24. Fixtures under
`apps/api/tests/fixtures/` are recordings of these responses; the live contract
tests (`pytest -m live`) fail loudly if a demo upgrade changes them.

### Prometheus (`127.0.0.1:9090`)

Span metrics come from the collector's `span_metrics` connector and are pushed
over OTLP — there is no scrape of the services.

| Metric | Labels used |
|---|---|
| `traces_span_metrics_calls_total` | `service_name`, `status_code`, `span_kind`, `span_name` |
| `traces_span_metrics_duration_milliseconds_bucket` | same, plus `le` |
| `container_cpu_utilization_ratio` | `container_name` (percent of one core, 0–100+) |
| `container_memory_usage_total_bytes` | `container_name` |

`status_code` values: `STATUS_CODE_ERROR`, `STATUS_CODE_OK`, `STATUS_CODE_UNSET`
(the demo emits mostly `UNSET`; the detector counts only `ERROR`).
`span_kind` values: `SPAN_KIND_SERVER`, `SPAN_KIND_CLIENT`, `SPAN_KIND_INTERNAL`.

Queries the API exposes (never raw PromQL from the browser):

- error rate — `sum by (service_name) (rate(calls{status=ERROR,
  span_kind="SPAN_KIND_SERVER"}[w])) / clamp_min(sum by (service_name)
  (rate(calls{span_kind="SPAN_KIND_SERVER"}[w])), 0.0001)`, `or`-ed with a zero
  series so services without errors report `0` instead of disappearing.
  **Inbound spans only**: the demo marks `flagd` client spans as errors, so an
  all-span ratio reads 100% for low-traffic services like `payment`.
- p95 latency — `histogram_quantile(0.95, sum by (le, service_name)
  (rate(duration_bucket{span_kind="SPAN_KIND_SERVER"}[w])))`, falling back to
  all spans for services with no SERVER spans. `> 0` drops `NaN` before the
  fallback.
- throughput — `sum by (service_name) (rate(calls[w]))`.

**Freshness (important).** The upstream connector flushes every 60 s, which
makes `rate(...[1m])` unreliable and hides a fresh fault for up to a minute.
`infra/otel-collector/extras.yaml` (the upstream "extras" seam, mounted by
`lab.override.yaml`) lowers `metrics_flush_interval` to **10 s**. Measured:
counter values now advance every 10 s. The catalog's smallest window is `2m`;
queries below that return sparse/empty vectors.

`histogram_quantile` returns `NaN` for empty bucket sets and the highest finite
bound (15000 ms) when the quantile falls in `+Inf`. The adapter **drops NaN**
(invalid JSON) and passes the finite bound through unchanged.

### Jaeger (`127.0.0.1:16686/jaeger/ui/api/v3`)

| Endpoint | Shape |
|---|---|
| `GET /services` | `{"services": [...]}` |
| `GET /traces?query.service_name=…&query.startTimeMin=…&query.startTimeMax=…&query.num_traces=…` | `{"result": {"resourceSpans": [...]}}` |
| `GET /traces/{trace_id}` | same envelope, one trace |

Parameter casing is mixed and only these names work: `startTimeMin` /
`startTimeMax` (camelCase, **required**) and `num_traces` (snake_case). Passing
`numTraces` is silently ignored and the server returns its default 100 traces.

Search returns **whole traces**, so a search for `cart` yields the frontend root
span and every downstream service in `services[]`. The adapter summarises by
trace id, computes duration from the root, counts `status.code == 2` as errors,
and builds a depth-ordered waterfall for the detail view.

Retention is in-memory (25k traces). Anything that must outlive it is
snapshotted into Postgres by the evidence engine (P9).

### OpenSearch (`127.0.0.1:9200`, index `otel-logs*`)

One index per day. Verified document shape: `@timestamp`, `observedTimestamp`,
`body` (text), `severity.{text,number}`, `resource.service.name`, `traceId`,
`spanId`, `attributes`.

Search is a fixed DSL: a time range plus optional `term` filters
(`resource.service.name`, `traceId`, `severity.text`) and a `match_phrase` on
`body`. The browser passes values, never DSL.

### Docker

`docker ps -a --filter label=com.docker.compose.project=docock-demo`, then one
`docker inspect` for all names (the command returns a JSON **array**, not
NDJSON), plus `docker stats --no-stream`. `ps` supplies state/health; `inspect`
supplies restart count, OOM flag, limits, networks, start time.

The same label is the fault-controller boundary in P6: a container that is not
in `docock-demo` is never a target.

## Caching and timeouts

- One `TTLCache` per adapter (5–10 s), with single-flight loading.
- One keep-alive `AsyncHttpClient` per dependency, timeouts from `HTTP_TIMEOUT_S`
  (8 s), two retries with exponential backoff on transport errors and
  429/502/503/504.
- `DependencyError` → `502 upstream_error` / `504 upstream_timeout` through the
  shared error envelope; `/api/health` still answers with per-dependency status.
- Docker CLI calls are bounded by `asyncio.wait_for` so a wedged daemon cannot
  hang a request.

## Observed baseline quirk
Under the demo's default load the storefront pages are fast (~90–230 ms) but the
synthetic checkout path is slow: mean SERVER span duration is ~25 s for
`checkout`, ~11 s for `recommendation`, and ~9 s for `product-catalog`
(limits: 20 MB for product-catalog, which runs at ~94% memory). `frontend` and
`checkout` p95 therefore sits at the top finite bucket (15 000 ms) even when
healthy. Consequences:

- the detector (P8) must compare against **per-service baselines**, never an
  absolute latency threshold;
- a latency alert for `checkout` is only meaningful if it exceeds its own noisy
  baseline;
- error rate remains the cleaner signal for the scripted scenarios.

## Fault propagation latency (measured 2026-10-24, P5)

Writing the runtime flag file is instant and OFREP reflects the new variant in
~0.5 s (P1 verified this and it still holds), but a **service** only starts
failing 30–45 s later. Measured on `paymentFailure` (`off → 100%`): the first
failed charge appeared ~38 s after the write, the first error trace ~49 s, and
the 1-minute error-rate series crossed 20 % at ~62 s.

The delay is in the demo's flagd SDK clients (the payment service's
`flagd.evaluation.v2.Service/EventStream` span is itself marked ERROR, so it is
likely falling back to polling), not in the file write or the API. Combined with
payment's ~10 s request cadence and a 1-minute rate window, end-to-end
"flag write → visible in three panes" is ~60–75 s for a low-traffic service.

Consequences for later phases:

- P6 must show an injected fault as *propagating* until the first symptom is
  observed, and must not assume the service has changed the moment OFREP does.
- P8's detector already waits for symptoms, so this delay is absorbed there.
- A high-traffic target (frontend-proxy, cart) reacts sooner than payment.

