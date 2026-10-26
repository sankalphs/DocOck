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
| P4 | Telemetry adapters | TODO | — |
| P5 | Overview + Telemetry UI | TODO | — |
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

