# Progress

Single source of truth for phase state. Every phase ends with a commit and an
entry here. If a later phase changes an earlier assumption, update the earlier
line rather than deleting history.

Legend: `TODO` Â· `WIP` Â· `DONE` Â· `BLOCKED`

| Phase | Scope | Status | Commit |
|---|---|---|---|
| P0 | Repo skeleton, tooling, API health, bootstrap script | DONE | `2fd8062` |
| P1 | Compose layering, ports, pgvector, up/down/reset, health | DONE | `b0efae0` |
| P2 | API skeleton + DB + migrations | DONE | `f5840f0` |
| P3 | Dashboard shell | TODO | â€” |
| P4 | Telemetry adapters | TODO | â€” |
| P5 | Overview + Telemetry UI | TODO | â€” |
| P6 | Fault controller | TODO | â€” |
| P7 | Fault Lab UI | TODO | â€” |
| P8 | Incident engine + detection | TODO | â€” |
| P9 | Deterministic analysis + evidence | TODO | â€” |
| P10 | Model gateway + embeddings | TODO | â€” |
| P11 | Agent orchestrator | TODO | â€” |
| P12 | Memory + retrieval | TODO | â€” |
| P13 | Agent UI | TODO | â€” |
| P14 | Remediation + approval | TODO | â€” |
| P15 | Verification + reports | TODO | â€” |
| P16 | Hardening + evaluation | TODO | â€” |

## Milestones

| Milestone | Phases | Gate met |
|---|---|---|
| M1 Fault â†’ observe â†’ recover | P0â€“P7 | no |
| M2 Deterministic suspects | P8â€“P9 | no |
| M3 Agent diagnosis | P10â€“P13 | no |
| M4 Approval â†’ verify â†’ report | P14â€“P16 | no |

## Verification log

### P0 â€” 2026-10-24

- `uv sync --all-groups` on Python 3.11: 39 packages resolved.
- `ruff check`, `ruff format --check`, `mypy app tests`: clean.
- `pytest`: 3 passed.
- `git status`: clean after commit; the 8.6 GB Docker data disk is untracked
  and ignored (it was staged by the initial `git add`).

### P1 â€” 2026-10-24

- `scripts\health.ps1` â†’ **10/10 checks OK**: demo frontend, Prometheus ready,
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
- flagd hot reload verified end to end: `paymentFailure` `off â†’ 100% â†’ off`
  with no flagd restart.
- Cold start validated: `scripts\down.ps1` â†’ `scripts\up.ps1 -SkipPull`
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

### P2 â€” 2026-10-24

- `scripts\check.ps1` (ruff, ruff format, mypy, pytest): **26 tests passed**.
- `GET http://127.0.0.1:8001/docs` â†’ 200; `/openapi.json` lists
  `/api/health`, `/api/version`, `/api/events`.
- `/api/health` against the live stack returns `status: ok` with six real
  probes: postgres (`PostgreSQL 18.6 | pgvector 0.8.7 | 16 tables`), docker
  (`29.9.2 | 25/25 docock-demo containers running`), prometheus
  (`ready | 139 span-metric series`), jaeger (`18 services`), opensearch
  (`cluster yellow | 1 index | 206 docs`), llm (`unconfigured`, no key in
  `.env` â€” reported, not faked).
- `scripts\db-check.ps1` on a **new empty database**: `alembic upgrade head`
  created 16 tables (15 domain + `alembic_version`), `downgrade base` left only
  `alembic_version`, scratch DB dropped. Exit 0.
- Live schema check via `psql`: `memories.embedding` is `vector(1024)` with an
  `hnsw (embedding vector_cosine_ops)` index; all 15 Â§7 tables present.
- `GET /api/events` streams `text/event-stream`, replays
  `id: 1 / event: system.ready / data: {...}` on connect, and sets `retry: 3000`.
- Error envelope confirmed by `pytest`: 404 â†’ `not_found`, bad query param â†’
  `validation_error` with `details[].loc`, and `X-Request-ID` is echoed when
  supplied.

Fixes discovered during the phase:

1. The `vector` extension is created only in `$POSTGRES_DB`, so the baseline
   migration now runs `CREATE EXTENSION IF NOT EXISTS vector` itself â€” that is
   what makes "migrations apply on an empty database" true.
2. Docker Desktop's loopback proxy costs ~0.5 s per new TCP connection; health
   probes use one keep-alive client per dependency, which took a warm
   `/api/health` from ~2.1 s to ~50 ms.
3. `docker version` / `docker ps` spawns a large CLI (~0.5 s); the probe caches
   the server version for 60 s and the container summary for 10 s.
4. The request-id middleware must look up the incoming header as lowercase
   **bytes** (`scope["headers"]`), not a str.

