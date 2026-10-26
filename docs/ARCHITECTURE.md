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

## Fault controller (P6)

`app/faults/` owns every write. The catalog (`catalog.py`) declares each fault's
mechanism, target, params and **symptom**; the API validates against it and the
dashboard generates its forms from the same response.

### The safety boundary

A target must pass all four checks, in this order, before any mutation:

1. name ∈ the catalog allowlist (checked without touching Docker),
2. a container labelled `com.docker.compose.project=docock-demo` exists for it,
3. its `com.docker.compose.service` label equals the requested name,
4. it is attached to exactly one network, and that network is
   `opentelemetry-demo`.

Failure is a `403 unsafe_target` with the allowlist in `details`. The catalog
allowlist deliberately excludes our Postgres, flagd, the collector, Prometheus,
Jaeger, OpenSearch, Grafana and the demo's database: stopping any of them would
blind or break the lab rather than inject a service fault.

### Status semantics

| Status | Meaning |
|---|---|
| `PROPAGATING` | flagd is written and OFREP confirms it; the service has not shown the symptom yet (the demo's SDK clients take 30–75 s) |
| `ACTIVE` | mechanism confirmed: for direct mechanisms the container/load-gen read-back; for flagd the first observed symptom |
| `REVERTED` / `EXPIRED` / `FAILED` | terminal |

Direct mechanisms (stop, pause, partition, limits, load) are `ACTIVE`
immediately after read-back because the state change *is* the observation.
Only flagd needs the propagating phase, which is why the UI must not claim the
service changed when OFREP does.

### Write protocol and crash safety

The `faults` row is inserted **before** the side effect, with a pre-image that is
sufficient to revert it (flag variant, container state, network + aliases,
limits, locust users). Consequences:

- a crash between insert and write leaves a row whose injection is absent;
  startup reconciliation marks it `REVERTED` without a command;
- a crash after the write leaves a row whose pre-image is durable; the watchdog
  reverts it at expiry, even if the API was down for the whole window
  (`scripts/fault-kill-test.ps1` proves both);
- every write is serialised behind one asyncio lock, so two operators cannot
  interleave a flag write.

`runtime/audit/faults.jsonl` is the durable audit trail (actor, action,
before/after, outcome). The database row is the queryable copy.

### Verified behaviours that shaped the code

- **`docker update --cpus 0` does not clear a CPU limit.** The Engine keeps the
  old `NanoCPUs`. Reverting a container that had no limit therefore sets the cap
  to `docker info` NCPU (24 here), which is the effective unlimited value for
  one container. The revert records `cpu_reset: host-cores (24)`.
- **`cartFailure` only fails `EmptyCart`** (`src/cart/src/services/CartService.cs:82`).
  The load generator rarely calls it, so that fault can stay `PROPAGATING` for
  its whole TTL; the catalog notes say so. The P9 cart scenario uses
  `readiness_failure` instead.
- **`paymentUnreachable` is evaluated by checkout**, which dials
  `badAddress:50051`; payment never receives the call. The fault targets
  payment but its symptom is observed on checkout.
- **`imageSlowLoad` is evaluated by the frontend** (`ProductCard.tsx` sets
  `x-envoy-fault-delay-request`), so its target is frontend, not image-provider
  as PLAN §8 listed.

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

## Incident detector (P8)

The detector is a 10 s asyncio loop (`app/detection/runtime.py`). One cycle reads
error rate, absolute error count, p95, throughput (one 2-minute rate window
each), and Docker container state; updates per-service baselines; debounces three
consecutive breaches; then correlates.

Why 10 s and not PLAN's 15 s: the collector flushes span metrics every 10 s, so
15 s would sample the same point twice and cannot meet the "incident in <= 90 s"
DoD. The debounce (3 cycles = 30 s) still removes one-sample noise.

### Baselines

`error_rate`: **20th percentile** of the last 15 minutes, `p95_latency`: **80th
percentile**, `throughput`: **median**. The low percentile is what makes a
fault-tolerant baseline: an error rate only deviates upward, so the healthy level
survives even when a fault occupies most of the window. Latency is the opposite
kind of noise — shipping's healthy p95 ranges 48–1925 ms at constant load — so its
baseline is the top of the normal band (`p80`) and an alert must exceed twice it.

Samples taken while a service is breaching never feed its baseline, and a service
with no baseline cannot alert (that is the warm-up, expressed as data
availability rather than a timer). Persisted baselines are skipped for breaching
services and rejected on load when implausible (`> 25 %` errors, `> 60 s` p95),
otherwise a restart during a long fault would teach the detector that the fault is
normal — this actually happened while testing P9 and produced a silent detector.

### Thresholds

| Signal | Rule |
|---|---|
| errors | `rate > max(2 %, 3 x baseline)` **and** `errors >= 2` — unless the rate is >= 50 %, where one failed request out of two is not noise |
| latency | `p95 > max(2 x baseline, 500 ms)`; for a baseline >= 100 ms an absolute `+1.5 s` is also required (checkout's healthy p95 is 0.4–15 s) |
| container | not running / paused / `unhealthy`; health alone needs **6** consecutive samples because the demo's probes flap, stopped/paused needs 3 |
| resource | CPU or memory limit **below the limit observed at startup**; this is the only observable symptom of `cpu_pressure` on `ad`, which uses 1.3 % CPU even when capped at 0.25 cores |
| throughput | `> 2 x baseline` (baseline >= 0.25 req/s); the catalog itself declares `traffic_flood`'s symptom as a throughput doubling |

A cap equal to the host core count is normalized to "unlimited": Docker cannot
clear a CPU limit, so that is what a reverted container carries.

### Correlation and recovery

One incident per fault: when several services breach in the same cycle the
earliest completion-order onset wins, ties broken by deviation from the service's
own norm, then by throughput. Other breaching services attach as `signal` events.
The 5-minute per-signature cooldown is bypassed while any fault is `PROPAGATING`
or `ACTIVE`; without that, re-injecting the same fault to run a scenario step was
silently swallowed.

`OPEN -> RESOLVED` is the detector's recovery edge and the only automatic close:
an incident that recovers before anyone investigates closes itself after four
healthy samples. Once a human or the agent has moved it past `OPEN`, recovery is
evidence for that workflow, not a reason to close it behind their back.

