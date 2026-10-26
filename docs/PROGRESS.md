# Progress

Single source of truth for phase state. Every phase ends with a commit and an
entry here. If a later phase changes an earlier assumption, update the earlier
line rather than deleting history.

Legend: `TODO` · `WIP` · `DONE` · `BLOCKED`

| Phase | Scope | Status | Commit |
|---|---|---|---|
| P0 | Repo skeleton, tooling, API health, bootstrap script | DONE | `2fd8062` |
| P1 | Compose layering, ports, pgvector, up/down/reset, health | DONE | `b0efae0` |
| P2 | API skeleton + DB + migrations | TODO | — |
| P3 | Dashboard shell | TODO | — |
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
