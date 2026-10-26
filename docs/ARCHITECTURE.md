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

`error_rate`: **20th percentile** of the last 15 minutes, `p95_latency`: **median
(p50)**, `throughput`: **median**. The low percentile is what makes a
fault-tolerant baseline: an error rate only deviates upward, so the healthy level
survives. Latency in this demo is bimodal — a few hundred milliseconds, then the
15 s top bucket whenever the generator queues — so a p80 baseline of 15 s could
never be doubled; the median plus the absolute `+1.5 s` rule is what makes a
latency fault visible without firing on that noise.

Samples taken while a service is breaching never feed its baseline, and a service
with no baseline cannot alert (that is the warm-up, expressed as data
availability rather than a timer). While an incident is open, the services named
in it are excluded from baseline updates entirely: their readings are symptoms of
a known problem, and feeding them back is how a 15-minute p20 once learned that a
3-minute 100 % error window was normal. Persisted baselines are skipped for
breaching services and rejected on load when implausible (`> 25 %` errors,
`> 60 s` p95).

Throughput is the only metric that is not backfilled from Prometheus: after a
restart the generator ramps from ~1 req/s to 25 req/s, and a pre-restart baseline
would call that ramp a flood (it did). It is learned from live samples only, and
throughput breaches are suppressed for the first `DETECTOR_WARMUP_S` (300 s) after
the detector starts, because the ramp itself is a 10x step.

### Thresholds

| Signal | Rule |
|---|---|
| errors | `rate > max(2 %, 3 x baseline)` **and** `errors >= 2` — unless the rate is >= 50 %, where one failed request out of two is not noise |
| latency | `p95 > max(2 x baseline, 500 ms)`; for a baseline >= 100 ms an absolute `+1.5 s` is also required (checkout's healthy p95 is 0.4–15 s); needs **9** consecutive samples (`DETECTOR_LATENCY_BREACHES`, 90 s) because one slow PlaceOrder holds a low-traffic p95 up for the whole 2-minute window |
| container | not running / paused / `unhealthy`; health alone needs **6** consecutive samples because the demo's probes flap, stopped/paused needs 3 |
| resource | CPU or memory limit **below the limit observed at startup**; this is the only observable symptom of `cpu_pressure` on `ad`, which uses 1.3 % CPU even when capped at 0.25 cores |
| throughput | `> 2 x baseline` (baseline >= 1 req/s; image-provider swings 2.6x at 0.5 req/s); the catalog itself declares `traffic_flood`'s symptom as a throughput doubling |
| silence | **zero** spans in the rate window where the baseline expects >= 60 (~0.5/s). The floor is empirical: orders arrive in bursts, so checkout/payment/email see healthy 90 s gaps, while every service averaging >= 0.6/s never reached zero in 3 h of scenarios |
| network | a running container with **no network attached** (`network_partition`) is a container breach. Calls into it hang and emit no span until they end — no error, no latency — so Docker is the only prompt witness, as it is for stopped/paused |

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

## Deterministic analysis (P9)

`POST /api/incidents/{ref}/analyze` runs six analyses in a fixed order and stores
every observable as incident-scoped evidence. Re-running is idempotent: rows are
keyed by a hash of `(type, source, service, data)` that excludes the timestamp, so
an existing row keeps its `E####` code and only new observables append.

- **Metric anomalies** compare the incident window against the 5 minutes before
  it, per service and per metric. The "current" half starts 45 s *before* the
  incident opened, because the detector opens after three breaches — those
  samples are the symptom, and waiting for a post-open point would miss short
  faults.
- **Error origin** walks each error trace: a failing SERVER span with a failing
  child is *propagated*, without one it is the *origin*; a failing CLIENT span
  whose peer has no SERVER span at all (partition, stopped dependency, bad
  address) blames the peer. That "silent peer" case is what makes
  `network_partition` and `paymentUnreachable` attributable.
- **Onset** uses the earliest anomalous span *completion*, not start: a nested
  callee finishes its failing span before its caller, so the origin sorts first
  without any topology assumption.
- **Logs** are templated (UUIDs, IPs, hex ids, durations, numbers and quoted
  values become placeholders); only templates absent from the pre-incident window
  are reported, with a count and one example.
- **Container / flag state** are direct observations from Docker and from
  DocOck's own fault rows.

Suspect weights are configuration (`SUSPECT_WEIGHT_*`): error origin .30, onset
.20, local error ratio .20, latency .10, throughput .10, container .10. The
latency slot (`latency_anomaly`) also takes a container CPU or memory anomaly:
saturation is the cause of a slowdown, so it scores on the same log scale. The
starter weights in PLAN §10 had no throughput slot, which would leave
`traffic_flood` unattributable. Ties break on the throughput signal, so a flood
blames its entry point (frontend) rather than an equally slow downstream victim.
The lab's own machinery (`load-generator`, `flagd`, the observability stack) is
excluded from the ranking: an operator cannot remediate the injector.

Two *incident classes* change which signals are informative, because in both the
usual winners are consequences rather than causes:

- a **load** incident (primary signal = throughput) ignores error origin, onset
  and local error ratio: under a flood every service that the traffic reaches
  shows errors and latency, so only throughput/latency/container decide;
- a **latency** incident ignores error origin and the local error ratio: the only
  ERROR in a timeout trace is usually the proxy or caller that gave up, and the
  delay is attributed by walking the dominant child instead. A failing span with
  no failing child descends to the child that owns most of its duration (same
  service or callee) and blames where the time is spent; a chain that ends at a
  client span with no SERVER span for its peer is the silent-peer case. Onset for
  a latency incident is therefore the first *slow* span after that descent, not
  the first timeout.

Jaeger's global "latest N" search returns mostly frontend-proxy traces and misses
a low-traffic payment error entirely, so the trace analysis searches the
incident's focus services explicitly and merges the results. Jaeger filters on
trace *start*, so the search reaches 240 s before the incident opened: a call
into a partitioned payment starts minutes before it ends, and the detector itself
needs up to 90 s of debounce on a 2-minute window.

Rules found while validating the scenarios against the live stack:

- **Injected proxy faults blame the caller.** Envoy tags a span with
  `response_flags=DI` (delay injected) or `FI` (abort injected) when its fault
  filter acted on an `x-envoy-fault-*` request header. `imageSlowLoad` is exactly
  that: the frontend's ProductCard sends the header, the proxy sleeps 5 s. The
  proxy executed the delay; the caller asked for it. The browser often does not
  export its request span, so the proxy span's parent is missing; an injected
  fault at `frontend-proxy` with an untraced caller is attributed to
  `frontend-web`, the only client that sends these headers.
- **Telemetry names that are not deployables are folded.** `frontend-web` is the
  browser bundle the frontend container serves; it is scored as `frontend`.
- **A hung call's onset is when it went out.** A slow span that descends to a
  silent peer completes only when the peer returns, so completion order would
  put it last; it uses the call's start, as the silent-peer error case does.
- **Throughput is scaled by its share of the largest absolute change.** A flood
  enters where most requests appear; a 0.2 req/s service that also doubled is a
  fraction of that, not a tie.
- **A container Docker reports stopped/paused/unhealthy ranks first**, whatever
  the weighted scores say: it is a direct observation, not a statistic.

One analyzer run on the 63-evidence payment incident takes ~6.4 s.

## Model gateway (P10)

`app/models/gateway.py` turns configuration into two providers:

| Interface | Implementations | Default |
|---|---|---|
| `LLMProvider.chat(messages, tools, json_mode)` | `stub`, `zenmux`, `openai_compatible` | `stub` (offline, deterministic) |
| `EmbeddingProvider.embed(texts, kind)` | `onnx_local` (Qwen3-Embedding-0.6B int8), `remote`, `stub` (feature hashing) | `onnx_local` |

Effective settings are `.env` plus runtime overrides in `settings`
(`models.overrides`). Overrides cover provider, model, temperature, budgets,
prices, thresholds. **Base URLs and API keys are `.env` only**: the API is local
and unauthenticated, and a runtime base-URL change would send the configured key
to any host. Embedding dimensions are read-only because a width change is a
re-embed (P12).

Every `ChatResponse` carries usage, locally computed cost
(`LLM_PRICE_*_PER_M`), latency, attempts, and the model ID the server reported.
Retries cover transport errors, timeouts, 408/409/425/429/5xx with capped
exponential backoff (`Retry-After` honoured); auth errors are never retried.

The ONNX export needs an empty KV cache as input and its tokenizer already
appends the pooling token; see the module docstring of
`app/models/embeddings/onnx_local.py`.

## Agent orchestrator (P11)

`app/agent/` runs one investigation per incident as a background task:

```
analysis (if no suspects) → context → LLM tool loop → diagnosis JSON
   → validator (one repair turn) → confidence gate → DIAGNOSED | ESCALATED
```

| Part | Module | Rule |
|---|---|---|
| Context | `context.py` | One JSON document: trigger, top-5 suspects with the codes behind each signal, an evidence index (≤ 140 rows), topology edges, fault targets, the action catalog. Every listed code counts as *shown*. |
| Tools | `tools.py` | 11 read-only tools with `extra=forbid` JSON schemas. Service names must match `^[a-z0-9][a-z0-9-]{0,63}$` **and** a known service (Prometheus selectors are not escaped). Every live observation is stored as an evidence row under the store lock and returns its `E####` code, so the model can cite what it saw. |
| Budget | `budget.py` | ≤ 20 tool calls (the 21st is refused, not run), ≤ 90 s wall clock with a 25 s reserve for the final answer, ≤ tools + 4 model turns, and the daily USD cap summed from `agent_runs.cost_usd`. Running out of tools or time asks for the answer without tools; only the hard clock, the turn cap and the spend cap fail a run. |
| Validator | `validator.py` | PLAN §11 rules: codes exist **for this incident** and were shown (context or a tool result); root cause in the observed topology; action in the catalog with an allowed target (`app/remediation/catalog.py`); summary ≤ 1200 chars, no tables, every number traceable to a cited row; affected services known. Telemetry aliases fold (`frontend-web` → `frontend`). Validity and the confidence gate are separate. |
| Persistence | `orchestrator.py` | `agent_runs` (status, tokens, cost, latency, error) and ordered `agent_steps`; each step is also published as SSE `agent.step`, framed by `agent.run.started` / `agent.run.finished`. |

Incident state: a run started on `OPEN` moves it to `INVESTIGATING`, then to
`DIAGNOSED` (valid and ≥ `AGENT_CONFIDENCE_THRESHOLD`) or `ESCALATED` (invalid
after the repair turn, below the gate, failed, or cancelled). A rerun on a
`DIAGNOSED`/`ESCALATED` incident records a new run and diagnosis without moving
the incident. A run left `RUNNING` by an API restart is marked `FAILED` at
startup and its incident escalated.

Deviation from PLAN §7: `DIAGNOSED → RESOLVED` is allowed when the detector sees
the symptom clear. Without it, no incident could resolve before remediation
(P14), and P12 memory stores only resolved incidents.

**Offline reasoning.** With `LLM_PROVIDER=stub` the gateway uses
`app/agent/stub_policy.py`: a deterministic investigator that speaks the same
protocol (two tool rounds, then one JSON answer) and reasons only from the
context and tool results. The root is the top suspect unless a recorded
fault/flag change targets another top-3 suspect. The whole pipeline —
persistence, validation, gate, SSE, UI — is therefore exercised without a key.

