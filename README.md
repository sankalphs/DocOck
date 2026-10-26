# DocOck

A standalone, local AI incident-response lab: a web dashboard injects faults into
the OpenTelemetry Astronomy Shop, watches real telemetry, detects and diagnoses
the incident, proposes a bounded remediation, waits for approval, verifies
recovery, and writes the report.

**Status:** planning. No application code yet.

- [Implementation plan](docs/PLAN.md)

## Quick orientation

```text
dashboard (Next.js :3001) → API (FastAPI :8001) → { Prometheus, Jaeger,
OpenSearch, Docker, Postgres+pgvector } → Astronomy Shop (:8080)
```

The dashboard and deterministic Python own the infrastructure. The LLM only
requests read-only tools and proposes actions from a fixed catalog — it never
gets Docker or shell access.

## Getting started

Coming with Phase 0/1 of the plan:

```powershell
scripts/bootstrap-demo.ps1   # clone the demo at a pinned ref
scripts/up.ps1               # demo + observability + Postgres
```
