# DocOck

A standalone, local AI incident-response lab: a web dashboard injects faults into
the OpenTelemetry Astronomy Shop, watches real telemetry, detects and diagnoses
the incident, proposes a bounded remediation, waits for approval, verifies
recovery, and writes the report.

**Status:** in build. Phases P0–P3 are done; see [docs/PROGRESS.md](docs/PROGRESS.md)
for the live phase table and the evidence behind each one.

- [Implementation plan](docs/PLAN.md)
- [Architecture decisions](docs/ADR/)
- [Runbook](docs/RUNBOOK.md)
- [Handoff protocol](docs/HANDOFF.md)

## Quick orientation

```text
dashboard (Next.js :3001) → API (FastAPI :8001) → { Prometheus, Jaeger,
OpenSearch, Docker, Postgres+pgvector } → Astronomy Shop (:8080)
```

The dashboard and deterministic Python own the infrastructure. The LLM only
requests read-only tools and proposes actions from a fixed catalog — it never
gets Docker or shell access.

## Getting started

```powershell
scripts/bootstrap-demo.ps1   # clone the demo at a pinned ref (first run only)
scripts/up.ps1               # demo + observability + Postgres
scripts/health.ps1           # 10 checks against the live stack

cd apps\api
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8001

cd ..\dashboard
pnpm install
pnpm dev                     # http://127.0.0.1:3001
```

## Quality gate

```powershell
scripts\check.ps1            # API: ruff, mypy, pytest — dashboard: eslint, vitest, next build
scripts\check.ps1 -ApiOnly   # skip the dashboard build
scripts\db-check.ps1         # Alembic baseline against an empty scratch database
```

## Layout

| Path | What |
|---|---|
| `apps/api` | FastAPI control plane, SQLAlchemy 2 async models, Alembic migrations |
| `apps/dashboard` | Next.js App Router dashboard (Tailwind v4 + shadcn/ui) |
| `infra/compose` | Two Compose projects: `docock-demo` and `docock-lab` |
| `scripts` | up/down/reset/health/check/db-check, all PowerShell |
| `docs` | Plan, ADRs, runbook, phase evidence |
| `opentelemetry-demo/` | Pinned upstream checkout — gitignored |
