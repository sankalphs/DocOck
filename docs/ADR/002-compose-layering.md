# ADR 002 — Compose layering and the host-port boundary

**Status:** accepted · 2026-10-24

## Context

The OpenTelemetry demo is proprietary-ish upstream code that we do not want to
fork, yet we must change four things to make it controllable: deterministic host
ports, a Jaeger OTLP port that does not collide with the collector, a writable
flag file that is not the upstream source, and a Postgres/pgvector service of
our own.

Compose merges files, but relative paths and `.env` lookup are anchored to the
project directory, which makes naive `-f` layering fragile across directories.

## Decision

Run **two Compose projects**:

| Project | Files | Contains |
|---|---|---|
| `docock-demo` | upstream `compose.yaml` + `compose.observability.yaml` + `infra/compose/lab.override.yaml` | demo services, flagd, Prometheus, Jaeger, OpenSearch, Grafana |
| `docock-lab` | `infra/compose/base.yaml` + root `.env` | Postgres/pgvector (API and dashboard run on the host in dev) |

The demo is invoked with `--project-directory opentelemetry-demo` so upstream
relative paths (`./src/...`) resolve correctly, and our override supplies
absolute paths via `DOCOCK_ROOT`.

**The host port is the integration boundary.** The API reaches Prometheus,
Jaeger, OpenSearch, flagd, and Docker through published ports, not a shared
network. That keeps the two projects independent and lets the API run under
`uv run uvicorn` with hot reload.

Every port the system depends on is pinned in the override (`!override` on
`ports:` replaces the upstream list instead of appending to it).

**Flags are never written to the upstream checkout.** flagd reads
`runtime/flagd/demo.flagd.json`, a directory mount. Mounting a directory rather
than a single file avoids Windows creating a directory when the file is absent,
and keeps atomic renames on one filesystem so flagd's watcher sees them.

## Consequences

- Upstream upgrades are a checkout bump, not a merge conflict in `src/`.
- `scripts/up.ps1` / `down.ps1` must always pass the same `-p`, `--project-directory`,
  and `-f` arguments; they are the single place that knows the invocation.
- `down.ps1` stops the demo first, then our services.
- If a port is already bound by a non-lab process, `up.ps1` fails before
  pulling anything.
- The API cannot use Compose DNS names (`prometheus`, `jaeger`); it uses
  `127.0.0.1`. Moving the API into Compose later means adding a network, which
  is a deliberate follow-up rather than a default.

## Rejected alternatives

- **Copy upstream compose into `infra/`**: silently diverges on every demo
  upgrade.
- **Single merged project**: couples upgrade cadence and forces our services
  into the demo's dependency graph.
- **Docker-in-Docker socket proxy from day one**: unnecessary exposure until the
  API itself is containerised.
- **Editing `opentelemetry-demo/.env`**: the checkout is disposable and
  gitignored; configuration belongs to the repo root.
