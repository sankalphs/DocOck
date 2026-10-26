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
| P6 | Fault controller | TODO | — |
| P7 | Fault Lab UI | TODO | — |
| P8 | Incident engine + detection | TODO | — |
| P9 | Deterministic analysis + evidence | TODO | — |
| P10 | Model gateway + embeddings | TODO | — |
| P11 | Agent orchestrator | TODO | — |
| P12 | Memory + retrieval | TODO | — |
| P13 | Agent UI | TODO | — |
| P14 | Remediation + approval | TODO | — |
| P15 | Verification + reports | TODO | — |
| P16 | Hardening + evaluation | TODO | — |

## Milestones

| Milestone | Phases | Gate met |
|---|---|---|
| M1 Fault → observe → recover | P0–P7 | no |
| M2 Deterministic suspects | P8–P9 | no |
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

