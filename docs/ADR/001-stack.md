# ADR 001 — Stack and phase-0 corrections

**Status:** accepted · 2026-10-24

## Context

We are building a local incident-response lab around the OpenTelemetry Astronomy
Shop. The source design proposed Next.js, FastAPI, Postgres + pgvector, Jaeger,
Prometheus, OpenSearch, a ZenMux-hosted reasoning model, and a local Qwen3
embedding model. Before committing, the machine and the demo source were
inspected.

## Decisions

| Area | Decision | Why |
|---|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui | Source design; SSR not required but App Router routing is convenient |
| Charts | Recharts | Fast to build; swap later if it becomes the bottleneck |
| API | FastAPI + Pydantic v2, Python 3.11 | 3.14 has no ML wheels; 3.11 is the safe floor for later ONNX work |
| Packaging | `uv` for Python, `pnpm` for Node | Both already installed; lockfiles committed |
| DB | Postgres 18 + pgvector (single instance) | One store for incidents, evidence, and embeddings; no separate vector DB |
| Transport | SSE for server→client | One-way, replayable, no proxy upgrade issues; WebSocket only if needed |
| Faults (app) | flagd flag variants | Demo ships 21 flags; patching services would fork upstream |
| Faults (infra) | Docker stop/pause/limits/network-disconnect | Portable on Windows; `tc`/netem deferred |
| Embeddings | Qwen3-Embedding-0.6B, ONNX int8, CPU | No CUDA on this machine (Radeon 890M) |
| LLM | OpenAI-compatible adapter, ZenMux default (`meta/muse-spark-1.3-contributor`) | Verified available at $0.10/$0.20 per M tokens; provider-swappable |

## Consequences

- Python 3.11 is pinned in `apps/api/.python-version`; do not "upgrade" it before
  ONNX/torch compatibility is re-checked.
- The demo checkout is **not** committed. `scripts/bootstrap-demo.ps1` pins it.
- Observability containers are reached over published **host** ports, so the API
  can run on the host during development and later move into Compose unchanged.
- Adding a new fault class means either a new flag variant in the runtime flag
  file or a new adapter — never a service patch.
- Grafana is kept running (it is a `depends_on` target of `frontend-proxy`);
  removing it would require weakening the upstream dependency graph for ~450 MB.

## Alternatives rejected

- **Kubernetes**: adds an operator surface we do not need for one host.
- **A dedicated vector database**: pgvector is sufficient at lab scale.
- **Celery/Redis workers**: the workload is a single long-running detector loop
  plus one agent run per incident; asyncio tasks are enough.
- **Celery-style fault CLI**: replaced by the dashboard API.
