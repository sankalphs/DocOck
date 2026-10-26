# Progress

Single source of truth for phase state. Every phase ends with a commit and an
entry here. If a later phase changes an earlier assumption, update the earlier
line rather than deleting history.

Legend: `TODO` · `WIP` · `DONE` · `BLOCKED`

| Phase | Scope | Status | Commit |
|---|---|---|---|
| P0 | Repo skeleton, tooling, API health, bootstrap script | DONE | `2fd8062` |
| P1 | Compose layering, ports, pgvector, up/down/reset, health | DONE | `b0efae0` |
| P2 | API skeleton + DB + migrations | DONE | `f5840f0` |
| P3 | Dashboard shell | DONE | `9db0dd3` |
| P4 | Telemetry adapters | DONE | `c23726f` |
| P5 | Overview + Telemetry UI | DONE | `4f644b9` |
| P6 | Fault controller | DONE | `5e410fd` |
| P7 | Fault Lab UI | DONE | `29bc7a2` |
| P8 | Incident engine + detection | DONE | `6f6d69d` |
| P9 | Deterministic analysis + evidence | DONE | `f2f8946` |
| P10 | Model gateway + embeddings | DONE | `75227b0` |
| P11 | Agent orchestrator | TODO | — |
| P12 | Memory + retrieval | TODO | — |
| P13 | Agent UI | TODO | — |
| P14 | Remediation + approval | TODO | — |
| P15 | Verification + reports | TODO | — |
| P16 | Hardening + evaluation | TODO | — |

## Milestones

| Milestone | Phases | Gate met |
|---|---|---|
| M1 Fault → observe → recover | P0–P7 | yes (P7 `29bc7a2`) |
| M2 Deterministic suspects | P8–P9 | yes (7/8 top-1, P9) |
| M3 Agent diagnosis | P10–P13 | no |
| M4 Approval → verify → report | P14–P16 | no |

## Verification log

### P0 — 2026-10-24

- `uv sync --all-groups` on Python 3.11: 39 packages resolved.
- `ruff check`, `ruff format --check`, `mypy app tests`: clean.
- `pytest`: 3 passed.
- `git status`: clean after commit; the 8.6 GB Docker data disk is untracked
  and ignored (it was staged by the initial `git add`).

### P1 — 2026-10-24

- `scripts\health.ps1` → **10/10 checks OK**: demo frontend, Prometheus ready,
  Prometheus span metrics, Jaeger API, OpenSearch cluster, OpenSearch logs
  index, flagd UI, load generator, Postgres 5433, flagd OFREP 8016.
- Prometheus: 354 metric names; `traces_span_metrics_calls_total` and
  `traces_span_metrics_duration_milliseconds_bucket` present, labelled
  `service_name`. Confirmed with a live `sum by (service_name) (rate(...[5m]))`.
- Jaeger: 18 services via `/jaeger/ui/api/v3/services` (v3 API, not `/api/*`).
- OpenSearch: index `otel-logs-2026-10-24`, 173 docs.
- Postgres: 18.6, extensions `vector 0.8.7` and `pgcrypto 1.4`.
- All demo containers healthy (`checkout` and `product-catalog` after the probe
  fix).
- flagd hot reload verified end to end: `paymentFailure` `off → 100% → off`
  with no flagd restart.
- Cold start validated: `scripts\down.ps1` → `scripts\up.ps1 -SkipPull`
  recreated all 25 demo containers plus Postgres, and `health.ps1` returned
  10/10 with exit 0.
- All 25 demo containers carry `com.docker.compose.project=docock-demo`; P6
  should use that label (not a network name) as the safety boundary.

Fixes discovered during the phase:

1. Postgres 18 requires the volume at `/var/lib/postgresql`, not `.../data`.
2. flagd's upstream ports are unpinned (Docker assigned 8693/8692); pinned to
   8013/8016 in the override.
3. Jaeger v2 serves the query API under `<base_path>/api/v3/*`.
4. `grpc_health_probe` in the Go services has no deadline and exceeds Docker's
   5 s timeout; explicit `-connect-timeout=2s -rpc-timeout=2s`.
5. The frontend caches a failed gRPC address; restart it after recreating a
   backend.
6. flagd rejects UTF-8 BOM and ignores a flag file whose mtime moved backwards.

### P2 — 2026-10-24

- `scripts\check.ps1` (ruff, ruff format, mypy, pytest): **26 tests passed**.
- `GET http://127.0.0.1:8001/docs` → 200; `/openapi.json` lists
  `/api/health`, `/api/version`, `/api/events`.
- `/api/health` against the live stack returns `status: ok` with six real
  probes: postgres (`PostgreSQL 18.6 | pgvector 0.8.7 | 16 tables`), docker
  (`29.9.2 | 25/25 docock-demo containers running`), prometheus
  (`ready | 139 span-metric series`), jaeger (`18 services`), opensearch
  (`cluster yellow | 1 index | 206 docs`), llm (`unconfigured`, no key in
  `.env` — reported, not faked).
- `scripts\db-check.ps1` on a **new empty database**: `alembic upgrade head`
  created 16 tables (15 domain + `alembic_version`), `downgrade base` left only
  `alembic_version`, scratch DB dropped. Exit 0.
- Live schema check via `psql`: `memories.embedding` is `vector(1024)` with an
  `hnsw (embedding vector_cosine_ops)` index; all 15 §7 tables present.
- `GET /api/events` streams `text/event-stream`, replays
  `id: 1 / event: system.ready / data: {...}` on connect, and sets `retry: 3000`.
- Error envelope confirmed by `pytest`: 404 → `not_found`, bad query param →
  `validation_error` with `details[].loc`, and `X-Request-ID` is echoed when
  supplied.

Fixes discovered during the phase:

1. The `vector` extension is created only in `$POSTGRES_DB`, so the baseline
   migration now runs `CREATE EXTENSION IF NOT EXISTS vector` itself — that is
   what makes "migrations apply on an empty database" true.
2. Docker Desktop's loopback proxy costs ~0.5 s per new TCP connection; health
   probes use one keep-alive client per dependency, which took a warm
   `/api/health` from ~2.1 s to ~50 ms.
3. `docker version` / `docker ps` spawns a large CLI (~0.5 s); the probe caches
   the server version for 60 s and the container summary for 10 s.
4. The request-id middleware must look up the incoming header as lowercase
   **bytes** (`scope["headers"]`), not a str.

### P3 — 2026-10-24

- `scripts\check.ps1` (now including the dashboard) exits 0: API ruff/mypy/
  26 tests, dashboard eslint, **18 vitest tests**, and a `next build` that
  type-checks and prerenders 12 routes.
- All ten sections answer 200 with the right H1: `/`, `/faults`, `/telemetry`,
  `/incidents`, `/agent`, `/approvals`, `/reports`, `/memory`, `/models`,
  `/settings`.
- Playwright against the live servers (1440×900 and 390×844): the header shows
  `Stack ok 6/7`, `live` SSE, and seven dependency chips with real latencies
  (`postgres 60 ms`, `docker 13 ms`, `demo 51 ms`, `prometheus 56 ms`,
  `jaeger 46 ms`, `opensearch 57 ms`, `llm <1 ms`); the cards read `v0.1.0`,
  `25/25 containers`, `18 services`, `16 tables`; the live panel shows
  `#1 system.ready` with its payload.
- Empty states verified on `/faults`: they name the phase and the capabilities,
  and explicitly state that no simulated data is rendered.
- Mobile: 390 px viewport has no horizontal overflow (`scrollWidth == 375`), the
  drawer opens at `left: 0, width: 256`, and nav clicks close it.
- Evidence: `docs/evidence/p3-overview.png`, `p3-overview-1440.png`,
  `p3-telemetry.png`, `p3-faults-empty-state.png`, `p3-mobile.png`,
  `p3-mobile-drawer.png`.
- `pnpm gen:api` generated `lib/api/schema.d.ts` from the running
  `/openapi.json`; the client types health, error envelopes, and paths from it.

Fixes discovered during the phase:

1. Next 16 + React 19 lint rejects `setState` inside an effect; the mobile
   drawer now closes from the nav link's `onClick` instead.
2. A CSS grid child with `min-width: auto` let the dependency table stretch the
   page to 427 px on mobile. `min-w-0` on the grid items and the table wrapper
   restored `scrollWidth == viewport`.
3. The sidebar is `h-dvh` with `min-h-0` on the scrolling nav; without it the
   footer was clipped below the fold.
4. pnpm 11 blocks postinstall scripts by default; `unrs-resolver` (used by the
   ESLint resolver) needs `allowBuilds: true` in `pnpm-workspace.yaml`.
5. `openapi-typescript` must run against the live API, so it stays a dev-time
   step; the generated schema is committed so builds stay offline-safe.

### P4 — 2026-10-24

- **Metric names confirmed live** (348 names at the time; P1 saw 354 — it
  fluctuates): `traces_span_metrics_calls_total` and
  `traces_span_metrics_duration_milliseconds_bucket`, labels `service_name`,
  `status_code`, `span_kind`, `span_name`, `le`. Container metrics
  (`container_cpu_utilization_ratio`, `container_memory_usage_total_bytes`) are
  labelled by `container_name`, not `service_name`.
- **Jaeger parameter casing** verified by experiment: `query.numTraces` is
  silently ignored (server returns its default 100 traces, a 6.2 MB response);
  `query.num_traces` returns exactly N. `startTimeMin`/`startTimeMax` are
  camelCase and required.
- **OpenSearch** document shape recorded from the live index: `@timestamp`,
  `body`, `severity.{text,number}`, `resource.service.name`, `traceId`,
  `spanId`, `attributes`.
- **Docker**: `docker inspect` returns a JSON array (not NDJSON) — parsing it
  line-by-line was a real bug caught by the live test.
- **Metric freshness fixed at the source**: the upstream `span_metrics` flush is
  60 s, which makes `rate[1m]` empty and a 15 s detector impossible.
  `infra/otel-collector/extras.yaml` sets `metrics_flush_interval: 10s` through
  the upstream extras seam. Measured after the change: value changes every
  10 s over a 120 s range.
- `scripts\check.ps1 -Live`: **64 unit tests + 10 live contract tests pass**
  (ruff, format, mypy clean).
- Endpoints exercised against the stack: `/api/telemetry/metrics` → 18 series;
  `/traces?service=cart` → 2 full traces; `/logs` → 11,551 hits, 3 returned;
  `/services` → 27 rows joined across metrics/traces/Docker (`payment`:
  `error_rate 0`, `p95 3.85 ms`, `memory 71.2 MB / 140 MB`, 3 sources);
  `/containers` → 25 containers.
- Fixtures recorded in `apps/api/tests/fixtures/` (12 files) by
  `scripts/record-telemetry-fixtures.py`; unit tests replay them through
  `httpx.MockTransport`, so parsing is tested without the stack.

Findings that change later phases:

1. Under the demo's default load the checkout path is genuinely slow (mean
   SERVER span: `checkout` ~25 s, `recommendation` ~11 s, `product-catalog`
   ~9 s at 94% of its 20 MB limit), while storefront pages answer in
   90–230 ms. `frontend`/`checkout` p95 sits at the top finite bucket
   (15 000 ms) when healthy, so **P8 must use per-service baselines**, never an
   absolute latency threshold. Recorded in `docs/ARCHITECTURE.md`.
2. `histogram_quantile` returns `NaN` for empty buckets; serialising it would
   produce invalid JSON. The adapter drops non-finite values.
3. `container_cpu_utilization_ratio` is a percentage of one core (0–100+), not
   a 0–1 ratio; the catalog labels it `%`.
4. Docker Desktop serialises new loopback connections, so a cold
   `/api/health` is ~2.5 s and a warm one ~50 ms. Health timeouts were raised to
   5 s after the first post-restart probe timed out on a cold Postgres connect.

### P5 — 2026-10-24

- `scripts\check.ps1 -Live` exits 0: API ruff/mypy/**66 tests** + **10 live
  contract tests**, dashboard eslint/**27 vitest tests**/`next build`.
- Overview against the live stack: KPI row (`worst error rate`, `worst p95`,
  throughput, services, containers), three Recharts panes over a 15 m / 30 s
  range, a 27-row service health table with error rate, p95, throughput, CPU,
  memory, sources, and the live SSE feed. `docs/evidence/p5-overview.png`.
- Telemetry page: Traces (service/lookback/min-duration/errors-only filters,
  trace list), Logs (service/severity/contains/lookback), Services tab with
  inventory KPIs and the sortable table. `p5-traces.png`, `p5-services.png`.
- Trace waterfall: 66 spans with depth indentation, per-span bars, error spans
  highlighted, selectable span attributes, and a Jaeger deep link.
  `p5-waterfall.png`, `p5-fault-waterfall.png`.
- **Trace ↔ log navigation verified in the browser**: the waterfall's `Logs`
  button produced
  `/api/telemetry/logs?…&trace_id=9876bad5…` with the trace chip in the pane;
  clicking a trace id in the log row switched to Traces with that trace
  selected. `p5-trace-to-logs.png`, `p5-log-to-trace.png`.
- **DoD measurement** (`scripts/verify-observability.py`, `paymentFailure`
  `off → 100%`, OFREP confirms in 0.5 s):
  - run 1: metrics visible at **23 s**, traces **4 s**, logs **4 s** — pass;
  - run 2 on a clean 60 s lookback: logs **61 s**, traces/metrics **74 s** —
    over the 60 s line. Root cause measured separately: the demo's flagd SDK
    clients take **30–45 s** to observe the file change (the payment
    EventStream span is itself ERROR, i.e. likely polling), plus payment's
    ~10 s request cadence and a 1-minute rate window.
  - **Verdict: the dashboard itself renders any signal within one poll
    (10–15 s); end-to-end from a flag write to a low-traffic service is
    60–75 s.** Recorded in ARCHITECTURE.md and handed to P6, which owns
    injection UX and must show a fault as "propagating" until observed.
- Revert verified: `paymentFailure` → `off` brought payment error rate from
  100 % to 0 % in **41 s**; both flags are `off` and `scripts\health.ps1`
  returns 10/10.

Fixes discovered during the phase:

1. Jaeger v3 answers **404** with `"No traces found"` for an empty search and
   for a missing trace. The adapter now treats search-404 as an empty result and
   trace-404 as `trace_not_found` — an empty window used to surface as a 502 in
   the UI.
2. Error rate over **all** spans was unusable: the demo marks `flagd` client
   spans as errors, so `payment` read 100 % failed from one event-stream span.
   The metric is now inbound (`SPAN_KIND_SERVER`) spans only; propagated
   failures still show in the caller's SERVER spans.
3. `histogram_quantile` returns 0 for some empty-bucket steps between real
   samples; a zero p95 is not a measurement, so those points become gaps.
4. Recharts' numeric `scale="time"` axis rendered a 9-hour tick range for
   15 minutes of data. The chart now uses an explicit local `HH:MM:SS` category
   label per row.
5. KPI "error rate" as a throughput-weighted average hid a 100 % failure of a
   low-volume service (payment). It is now the **worst** service rate with the
   service name; the weighted value is shown as secondary context.
6. `scripts\down.ps1` / `up.ps1` aborted on Compose's stderr progress under
   `$ErrorActionPreference = "Stop"`; both now check `$LASTEXITCODE` instead.
7. The demo stack had drifted into a degraded state (checkout 39 % server
   errors, 15 s median at the load generator) after hours of load. A
   `down.ps1` + `up.ps1 -SkipPull` cycle restored a clean baseline: every
   service at 0 % error, 25/25 containers healthy.

### P6 — 2026-10-24

- `scripts\check.ps1` exits 0: API ruff/mypy/**93 tests**, dashboard
  eslint/**27 vitest tests**/`next build` (13 routes).
- **Catalog**: `GET /api/faults/catalog` returns 19 definitions
  (13 application + 6 infrastructure), the declared variant sets from
  `runtime/flagd/demo.flagd.json`, per-fault params with min/max, the
  `SymptomSpec`, and the `FaultRequest` JSON Schema. `POST /api/faults`
  validates against the same objects; `variant: "37%"` → 422
  `invalid_variant`, `cpus: 8` → 422, unknown fault → 404.
- **Safety**: `POST /api/faults {"fault_id":"service_down","target":"postgres"}`
  → **403 `unsafe_target`** with the allowlist and **no fault row written**.
  Unit tests also pin the label check (a container with
  `com.docker.compose.project=other-project` → 403), a non-demo network → 403,
  and the detached-container exception used only for a partition revert.
- **Every v1 fault injects and reverts** (`scripts/verify-faults.py`,
  **134/134 checks**): each of the 19 faults is injected, read back, reverted,
  and reverted a second time (idempotent). Flag faults start `PROPAGATING`,
  infra faults are `ACTIVE` after read-back. Measured:
  - `payment_failure` (100 %) → `PROPAGATING` → **ACTIVE in 25 s**
    (`payment error_rate=0.5`, baseline 0) → revert → OFREP `off` in 1.7 s;
  - TTL watchdog: `cart_failure` with `ttl_s=20` → **EXPIRED at 24.2 s**, flag
    `off`;
  - `service_down`/`service_freeze`/`network_partition` on `quote` each
    reverted to a read-back-verified state (running / unpaused / reattached
    with its aliases);
  - `load_spike` 8 users → 5 restored; `memory_pressure` 200 MB → 250 MB
    restored; `cpu_pressure` 0.5 → NCPU restored.
- **API kill mid-fault** (`scripts\fault-kill-test.ps1`): injected
  `payment_failure` (TTL 45 s), killed the API process (OFREP still read 100 %),
  waited 55 s with the API down, restarted it → startup reconciliation marked
  the fault `EXPIRED` and wrote `off`; then `cart_failure` (TTL 300 s) survived
  a kill/restart as `PROPAGATING` and reverted normally. Final state: 0 open
  faults, `health=ok`.
- Audit trail: `runtime/audit/faults.jsonl` contains the before/after image,
  actor and outcome for every inject/observe/revert (213 entries after the
  run); `GET /api/faults/audit` serves the same records.
- `scripts/health.ps1` after the run: all services healthy; every catalog flag
  reads `off`.

Behaviour discovered during the phase (recorded in ARCHITECTURE.md):

1. `docker update --cpus 0` is a **no-op** — the Engine keeps the previous
   `NanoCPUs`. The revert now sets the cap to `docker info` NCPU (24) and
   records `cpu_reset: host-cores (24)`; the verification asserts that value.
2. `cartFailure` only fails `EmptyCart` (`CartService.cs:82`), which the load
   generator rarely calls. A 100 % injection produced no measurable cart
   symptom in 60 s, so the fault can legitimately stay `PROPAGATING`; the
   catalog note says so and P9 will use `readiness_failure` for the cart
   scenario.
3. Rapid consecutive flag writes (the 19-fault sweep) make the demo's SDK
   clients back off: a payment injection right after the sweep took >150 s to
   show. Waiting 20 s before the propagation check restored the 25 s figure.
   The controller itself is unaffected; the scenarios in P9 space their
   injections.
4. `paymentUnreachable` is enforced in checkout (`badAddress:50051`), so the
   fault targets payment but its symptom is observed on checkout.
   `imageSlowLoad` is enforced in the frontend (`x-envoy-fault-delay-request`),
   so its target is frontend rather than the PLAN's image-provider.

### P7 — 2026-10-24

- `scripts\check.ps1` exits 0: API ruff/mypy/**93 tests**, dashboard
  eslint/**33 vitest tests**/`next build`.
- **DoD run in the browser, zero CLI**: `/faults` → select `payment_failure`,
  variant `100%` → **Dry run** (plan shows `container payment`, project
  `docock-demo`, network `opentelemetry-demo`, TTL 900 s, the full pre-image
  `{flag: paymentFailure, before: off, after: 100%}`, baseline 0, and states
  that nothing was written) → **Inject fault** → the card appears as
  `propagating` with a live `14:39` countdown and “waiting for payment inbound
  error rate above 2%” → becomes **active** with
  `observed: payment error_rate=1 ratio` → Overview shows **100.00 % worst
  error rate** and the chart spike (`p7-overview-broken.png`) → **Revert** on
  the card → status `reverted`, OFREP `paymentFailure=off`, and the 1-minute
  checkout error rate returns to **0**; the Overview KPI decays to the demo's
  normal low-volume noise (`p7-recovered.png`).
- Screenshots: `docs/evidence/p7-fault-lab.png`, `p7-dry-run.png`,
  `p7-propagating.png`, `p7-active-fault.png`, `p7-overview-broken.png`,
  `p7-recovered.png`, `p7-history.png`.
- The form is generated from the catalog: 19 faults in two optgroups, a fixed
  target chip or a target select (infra), variant selects for flagd, number
  inputs with the catalog's min/max (and `unit`), and a TTL field bounded by
  `max_ttl_s`. The description, mechanism, risk, flag key, observed symptom and
  the upstream caveats (`cartFailure` only fails EmptyCart; `payment_unreachable`
  is observed at checkout) are rendered from the catalog, not hardcoded.
- **Reset all** is two-step: the first click arms `Confirm reset all`, the
  second calls `POST /api/faults/reset-all` and reports reverted / flag-drift /
  failed counts. Active cards poll every 4 s and refetch immediately on any SSE
  `fault.*` event; the countdown ticks locally at 1 s and is clamped at
  `expired`.
- History shows status, injected clock, held duration, params, and its own
  actor; the audit panel reads `/api/faults/audit` (newest first) and shows the
  before/after detail from `runtime/audit/faults.jsonl`.
- Fixes discovered during the phase:
  1. The `cartFailure` 25 % KPI earlier in the run was invoked as a reminder
     that low-volume services make a 2-minute error rate jumpy: checkout shows
     25 % from a single failed order. P8 must require more than one error in
     the window (recorded for the detector).
  2. Direct mechanisms briefly existed as `PROPAGATING` between the row insert
     and the read-back, so the observer logged a redundant `observe` for them.
     The observer now only promotes flagd faults.
  3. `AuditLog.tail` returns file order; the UI reverses it so the newest write
     is first.

### P8 — 2026-10-24

- `scripts\check.ps1` exits 0: API ruff/mypy/**107 tests**, dashboard
  eslint/**37 vitest tests**/`next build` (13 routes).

- **Detector DoD** (`scripts/verify-incidents.py`, **all 23 checks passed**):
  - `payment_failure` 100 % → incident **`INC-…` in 59 s** (DoD ≤ 90 s), exactly
    **one** open incident, primary the origin or its caller, timeline starting
    with `opened`, and the trigger carrying `error_rate`;
  - a second fault (`readiness_failure` on cart) **attached as a signal** to the
    same incident — no second incident was opened;
  - reverting both faults closed the incident as **`RESOLVED` after 274 s**
    with a `recovered` event on the timeline (four healthy samples + the 2-minute
    rate window draining);
  - an SSE client reconnecting with `Last-Event-ID` replayed **10 events**,
    including the `incident.opened`, all ordered after the anchor;
  - final state: 0 open faults, every catalog flag `off`, `health=ok`.
- **UI**: `/incidents` lists incidents (filter all/open/closed, 5 s poll +
  immediate refetch on any `incident.*` SSE event) and shows the aggregate —
  trigger, per-signal value/baseline/threshold, append-only timeline with
  transitions, and the faults that overlapped the window. Evidence and suspects
  render honest empty states until P9. Screenshot:
  `docs/evidence/p8-incident-open.png` (`INC-00004`, payment, 3 signals:
  payment + checkout + email).
- **Detector rules** (unit-tested in `tests/test_detection.py`, 13 tests):
  error `> max(2 %, 3 × p20 baseline)` with `errors >= 2`; latency
  `> max(2 × p80 baseline, 500 ms)` plus `+1.5 s` for slow services; container
  state/health with 6 samples for health-only; **resource limits below the
  startup value**; **throughput > 2 × median**. Correlation: earliest
  completion-order onset, one incident per fault, other breachers attach.
- Deviations from PLAN §15/§10, with reasons in `docs/ARCHITECTURE.md`:
  1. cadence **10 s**, not 15 s — the collector flushes span metrics every 10 s,
     so 15 s would resample the same point and cannot meet the ≤ 90 s DoD;
  2. baselines are **p20 errors / median latency / median throughput**, not one
     statistic for everything — error rates only deviate upward (p20 survives
     noise) while healthy latency is bimodal (a few hundred ms, then the 15 s
     top bucket), so its median plus an absolute `+1.5 s` delta is what makes a
     slowdown visible without firing on the demo's own queuing;
  3. baseline samples taken **while breaching** are never recorded, persisted
     seeds above 25 % errors are rejected, and `persist` skips breaching
     services — a restart during a fault had produced a 50 % "healthy" payment
     baseline that silently disabled the detector;
  4. the 5-minute alert cooldown is **bypassed while a fault is open**, because a
     deliberate re-injection is not an alert storm;
  5. `OPEN → RESOLVED` added as the detector's recovery edge (only an
     un-investigated incident auto-closes);
  6. two extra signals the PLAN's starter weights/rules did not cover:
     `container_resource` (the only observable for `cpu_pressure` on `ad`, which
     uses 1.3 % CPU at a 0.25-core cap) and `throughput` (the catalog's own
     symptom for `traffic_flood`).
- `error_count` was added to the Prometheus catalog so the detector can require
  two failed requests instead of trusting a 2-minute rate (one failed order in
  payment reads as 25 %).
- Jaeger/OpenSearch/Prometheus/Docker are read with `ttl_s=0` inside the
  detector: a cached sample would make the same breach count three times.

### P9 — 2026-10-26

- `scripts\check.ps1` exits 0: API ruff/mypy/**134 tests**, dashboard
  eslint/**38 vitest tests**/`next build`.
- **Gate (PLAN §15, M2)**: `scripts/run-scenarios.py` on the live stack scored
  **7/8 top-1** in run 5 and again in run 6 (`runtime/scenarios-run6.json`):

  | # | Fault → expected | Run 6 incident | Detected | Top suspect | Score |
  |---|---|---|---|---|---|
  | S1 | payment_failure → payment | INC-00054 | 66 s | payment | 0.496 |
  | S2 | catalog_lock_contention → product-catalog | INC-00056 | 135 s | product-catalog | 0.342 |
  | S3 | payment_unreachable → payment | INC-00061 | 98 s | payment | 0.383 |
  | S4 | image_slow_load → frontend | INC-00063 | 155 s | frontend-proxy ✗ | 0.642 |
  | S5 | service_down email → email | INC-00064 | 31 s | email | 1.000 |
  | S6 | cpu_pressure ad → ad | INC-00065 | 34 s | ad | 0.388 |
  | S7 | network_partition payment → payment | INC-00066 | 30 s | payment | 1.000 |
  | S8 | traffic_flood → frontend | INC-00068 | 87 s | frontend | 0.916 |

  S4 is fixed after run 6 (deviation 17). A live S4 rerun with the fix
  (`--only S4`, INC-00069): **PASS**, detected 142 s, frontend 0.554 vs
  frontend-proxy 0.190. The fix only changes DI/FI spans at the edge proxy, which
  no other scenario produces, so S1–S3 and S5–S8 from run 6 still stand.
- UI: `docs/evidence/p9-suspects.png` — incident detail with the Analyze
  button, stable `E####` evidence rows and the suspect ranking with each
  signal's contribution.
- **Evidence store**: `E0001…` per incident, deduped by a hash of
  `(type, source, service, data)` that excludes the timestamp. Re-running the
  analyzer on the payment incident added **0 rows** and returned the same codes
  (63 evidence rows, 11 types: metric anomaly, onset, error origin, trace sample,
  healthy control, log template, log cluster, container state, flag state,
  topology, suspect ranking).
- **Suspect ranking** on that incident: payment **0.4166** vs checkout 0.1296,
  frontend 0.1296, frontend-proxy 0.1296, recommendation 0.0648, each row
  carrying all six signal values, weights and contributions. One analyzer run
  takes **6.4 s**.
- Unit tests pin the weights (normalised, sum 1, error origin > latency), the
  evidence code format/round-trip, hash stability, log templating, peer
  extraction, origin-vs-propagated, completion-order onset, silent peer, the
  timeout descent and the class-conditional weight sets.
- Deviations and fixes found while validating (§17's list was written before the
  demo was measured):
  1. **Two PLAN scenarios are unobservable at the default load.**
     `cart_failure` only fails `EmptyCart` (called rarely), and the cart's
     `readiness_failure` flips a probe Docker never runs (`docker inspect cart`
     has no healthcheck) — 120 s produced no error, latency or health change.
     `recommendation_cache_failure` likewise: p95 fell from 960 ms to 300 ms and
     the error rate stayed 0. They are replaced by `payment_unreachable`
     (100 % checkout failures with a silent peer) and `image_slow_load`
     (a 5 s delay the frontend requests and the proxy executes), and the
     substitution is recorded in the runner. `shipping_slowdown` was considered
     and rejected: it only delays non-US addresses, and 1 of the load
     generator's 9 users is Canadian (~1 slow call per 90 s).
  2. PLAN §17 expected `frontend-proxy` for the flood; the catalog's target is
     `frontend` (the flag is evaluated by the frontend), and that is what is
     scored.
  3. **Edge timeouts were mis-attributed.** The only ERROR in a catalog-lock
     trace is the proxy's 15 s timeout; the delay is product-catalog's DB call.
     A failing span with no failing child now descends to the child that owns
     most of its duration, so the origin is where the time is spent. Unit tests
     pin both the timeout case and the silent-peer case.
  4. **Baselines were contaminated by the faults themselves**: a 3-minute 100 %
     error window inside a 15-minute p20 taught the detector that checkout's
     baseline was 30–50 %, and the *next* scenario was then undetectable. The
     detector now excludes services named by an open incident from baseline
     updates, and skips persisting breaching services.
  5. **Latency baselines use the median**: this demo's healthy p95 is bimodal
     (a few hundred ms, then the 15 s bucket when the generator queues), so a
     p80 baseline of 15 s could never be doubled.
  6. The runner now waits for the previous incident to be closed *and* for the
     breach counters to clear before injecting; a leftover incident silently
     absorbed the next fault's breaches as signals, which looked like a detector
     failure.
  7. `traffic_flood` is scored on load-shaped signals only (throughput, latency,
     container): under a flood every service on the path shows errors and
     latency, and those are consequences. The same applies to error origin for
     latency incidents.
  8. The lab's own machinery (`load-generator`, `flagd`, the observability
     stack) is excluded from the suspect ranking. On the first run the flood was
     attributed to the load generator — causally true, operationally useless.
  9. **The stack degraded under its own limits.** After ~2 h, product-catalog sat
     at 19.9/20 MiB (above `GOMEMLIMIT=16MiB`) burning 136 % CPU in GC; checkout's
     p95 went from ~0.4 s to ~4 s with 1 s gaps between its own spans and opened
     a latency incident every cooldown, which masked S4. `lab.override.yaml` now
     gives checkout and product-catalog 40M / `GOMEMLIMIT=32MiB`. Healthy
     baselines then fell to product-catalog p95 ~44 ms and checkout ~98 ms, and a
     5-minute idle watch after warm-up showed **0 breaches**.
  10. **Partitions were undetectable.** A hung call emits no span until it ends
      — no error, no latency — so `network_partition` on payment produced no
      breach in 240 s. A running container with **no network attached** is now
      a container breach (Docker is the only prompt witness, as for
      stopped/paused). A first attempt used a `silence` breach on payment's
      inbound rate (0 for 2.5 min vs ~13 calls per window); it fired on email
      during a healthy order lull, because orders arrive in bursts (90 s with
      zero orders on a healthy stack). `silence` stays, for services that
      average >= 0.5 spans/s (none of them reached zero in 3 h).
  11. **Hung calls also broke the analysis**: the trace search looked back 120 s
      from *now*, but a partitioned `Charge` call starts minutes before it ends.
      It now reaches 240 s before the incident opened, and a slow span that
      descends to a silent peer takes its onset from the call's start.
  12. **Envoy fault injection**: the image delay is executed by frontend-proxy's
      HTTP fault filter because the frontend's ProductCard sends
      `x-envoy-fault-delay-request`. Envoy tags those spans `response_flags=DI`;
      a DI/FI span now blames its caller. `frontend-web` (the browser bundle the
      frontend container serves) is scored as `frontend`. Proxy routes into lab
      machinery (`upstream_cluster` flagservice, jaeger, grafana, …) are
      skipped: the browser's flagd event stream is a 15 s POST that had won
      onset for the proxy.
  13. **Scorer bugs**: the comment said an observed stopped container ranks first
      but the sort key put the weighted score first, and the observed flag was
      skipped when a fault also targeted that container (S5 had blamed checkout).
      Throughput is now scaled by the service's share of the largest absolute
      change; email and payment had tied the flood's entry point at 1.0 because
      a 0.2 req/s service doubling clamps to the same value as 25 → 58 req/s.
  14. Latency breaches need **9** consecutive samples (90 s), errors still 3: one
      slow PlaceOrder holds checkout's low-traffic p95 for the whole 2-minute
      window. Throughput surges need a baseline >= 1 req/s.
  15. **Runner isolation**: it waits for the detector's 300 s warm-up after an
      API restart, and for **240 s of continuous quiet** (no breach, no open
      incident) before each injection, capped at 900 s. Without that, the
      email container stopped by S5 explained S6–S8, and a catalog-lock backlog
      opened five incidents in the next scenario's window.
  16. **Re-analysis was not idempotent for a closed incident**: the trace and
      log searches were relative to *now*, and container evidence carried live
      CPU % / memory, so re-running on INC-00053 an hour later added 20 rows.
      Jaeger and OpenSearch searches now take the incident's fixed window
      (240 s / 300 s before opening → end), and container evidence stores state,
      not gauges. Three consecutive runs on INC-00053: **+22, +0, +0** rows, the
      same 28 codes, the same ranking (frontend 0.915, as in the live run).
      Concurrent runs are serialised by a lock (they would collide on the next
      `E####` code).
  17. **S4 blamed the edge proxy in run 6**: the slow `frontend-proxy` spans
      carried `response_flags=DI,DC`, but their parent is the browser's span,
      which the browser never exported, so no caller was found. An injected
      fault at the edge proxy with an untraced caller is now attributed to
      `frontend-web` (the frontend's bundle). Re-scoring INC-00063 with the fix:
      frontend **0.616**, frontend-proxy 0.215.

### P10 — 2026-10-26

- `scripts\check.ps1` exits 0: API ruff/mypy/**149 tests**, dashboard
  eslint/**39 vitest tests**/`next build`. `pytest -m live
  tests/test_model_gateway.py` (ONNX on CPU) passes.
- **Provider swap is config-only**, shown on the live API:
  `PATCH /api/settings/models {"llm":{"provider":"zenmux"},"embedding":{"provider":"stub"}}`
  → the next `POST /api/settings/models/test` used `ZenMuxProvider` (reported
  `unconfigured: no API key configured`, no network call) and the stub embedder
  (1024-d); `/api/health` showed `llm unconfigured zenmux | …`;
  `{"reset":true}` restored `stub` + `onnx_local`. The same swap through `.env`
  (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`) is covered by unit tests.
- **Embeddings on CPU**: `onnx_local` (Qwen3-Embedding-0.6B, int8 ONNX export,
  614 MB in `runtime/models/`) → test endpoint `ok`, **1024-d**, L2 norm 1.0000;
  first call 6.4 s (model load), warm call **47 ms**.
- **Connection test reports latency and model ID**: stub →
  `model_reported: stub-deterministic-v1`, 0.08 ms, 14/1 tokens, $0.
- **Secrets**: `PATCH {"llm":{"api_key":…}}` → 422, `{"llm":{"base_url":…}}` → 422
  (a runtime base-URL change could send the env key to another host). Keys are
  reported as `configured` + `••••abcd` only; a unit test asserts the key never
  appears in the view, an error message, or a log line.
- **Offline**: the stub provider is the default (`LLM_PROVIDER=stub`); every
  unit test runs without network. The OpenAI-compatible adapter is covered with
  `httpx.MockTransport`: tool-call parsing (invalid argument JSON surfaced as
  `None`), usage → cost ($0.10/$0.20 per M), 429 retried with backoff, 401 not
  retried, timeout typed, JSON mode dropped once when a server rejects
  `response_format`.
- UI: `docs/evidence/p10-models.png` — Models page with effective config,
  override badges, masked key status, swap form, and the connection test.

Not verified (no key on this host): a real ZenMux completion. The adapter is the
documented OpenAI wire format; set `LLM_PROVIDER=zenmux` and `LLM_API_KEY` in
`.env`, restart, and press **Test connection** to verify.

Facts found while building:

1. The `onnx-community` export of Qwen3-Embedding is a decoder with a KV cache:
   besides `input_ids`/`attention_mask`/`position_ids` it requires 56
   `past_key_values.*` inputs. An empty cache (`past_sequence_length = 0`) is a
   plain forward pass.
2. Its `tokenizer.json` already appends `<|endoftext|>` (id 151643), the token
   last-token pooling reads; appending it again would shift every embedding.
3. Queries carry Qwen3's `Instruct: …
Query:` prefix; documents do not.

