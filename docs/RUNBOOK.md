# Runbook — local stack

Everything here is Windows PowerShell, but the Compose commands are portable. On macOS,
Linux, WSL or Git Bash the shortest path is the one-command runner:

```bash
./run.sh                    # setup + stack + migrations + API + dashboard
./run.sh --skip-pull        # later runs
./run.sh --stop             # stop apps and containers
./run.sh --purge            # also remove data and project images
./run.sh --check            # quality gate, no Docker required
```

On Windows, `.\run.ps1` forwards the same flags to Git Bash. The rest of this runbook
uses the individual PowerShell scripts, which remain the reference implementation.

## Prerequisites

| Tool | Version used | Check |
|---|---|---|
| Docker Desktop | Engine 29.9.2, Compose 5.6.1 | `docker version` |
| Python | 3.11 (3.14 is *not* supported by later ML deps) | `uv python list` |
| uv | 0.8+ | `uv --version` |
| Node / pnpm | 24 / 11 | `pnpm --version` |
| Free disk | ~12 GB for images + models | `docker system df` |

Docker Desktop needs ~4 GB of memory or more; 8 GB is comfortable. The demo
alone runs ~20 containers.

## First run

```powershell
scripts\up.ps1
```

This will:

1. create `.env` from `.env.example` if missing,
2. clone the demo at the pinned commit if `opentelemetry-demo/` is absent,
3. seed `runtime/flagd/demo.flagd.json` from upstream,
4. refuse to start if a required port is taken by a non-lab process,
5. pull images, start `docock-demo` (demo + observability) and `docock-lab`
   (Postgres/pgvector), and wait for health.

First run pulls several GB. Later runs use `-SkipPull`.

Run the API separately (hot reload):

```powershell
cd apps\api
uv run uvicorn app.main:app --reload --port 8001
```

Apply database migrations (Alembic reads `DATABASE_URL` from `.env`):

```powershell
cd apps\api
uv run alembic upgrade head
uv run alembic current
```

`scripts\db-check.ps1` proves the baseline against a **scratch empty database**
without touching `docock`: it creates `docock_migrate_check`, upgrades,
downgrades, and drops it. Run it after adding a migration.

The API keeps one keep-alive HTTP connection per telemetry dependency. Docker
Desktop's loopback proxy costs ~0.5 s per new TCP connection, so the first
`/api/health` after a restart takes ~2.5 s and later ones are milliseconds.

## Dashboard

```powershell
cd apps\dashboard
pnpm install
pnpm dev        # http://127.0.0.1:3001, hot reload
pnpm build      # production build; also type-checks
pnpm test       # vitest (pure formatting/client units, no stack required)
```

The typed API client lives in `lib/api/schema.d.ts`, generated from the running
API. Regenerate it whenever a router or response model changes, and commit the
result:

```powershell
# API must be running on 8001
pnpm gen:api
```

The browser talks to `http://127.0.0.1:8001` by default. To point it elsewhere,
set `NEXT_PUBLIC_API_URL` in the shell (or in `apps/dashboard/.env.local`) before
`pnpm dev` / `pnpm build`. The API's CORS allowlist only accepts `:3001`, so a
different dashboard port needs a matching `CORS_ORIGINS` entry.

Verification screenshots for each phase are kept in `docs/evidence/`.

## Telemetry contract tests

The adapters have two layers of tests:

```powershell
cd apps\api
uv run pytest                     # recorded fixtures, no stack required
uv run pytest -m live -o addopts=-q   # contract tests against the live stack
scripts\check.ps1 -Live           # everything, including the live contracts
```

If a demo upgrade renames a metric, changes a Jaeger parameter, or reshapes a
log document, the live tests are what fail. Re-record the fixtures afterwards:

```powershell
uv run --project apps\api python scripts\record-telemetry-fixtures.py
```

Confirmed contracts (metric names, labels, Jaeger parameter casing, log fields,
freshness) are documented in `docs/ARCHITECTURE.md`.

## Verifying observability end to end

`scripts\verify-observability.py` flips the `paymentFailure` flag exactly the way
the fault controller will, confirms the change through OFREP, and polls the API
until the symptom is visible in metrics, traces, and logs:

```powershell
uv run --project apps\api python scripts\verify-observability.py inject
uv run --project apps\api python scripts\verify-observability.py revert
```

Expect 30–45 s before the payment service observes a flag change (its flagd SDK
polls), then one dashboard poll (10–15 s) before all three panes show it. The
script reverts the flag at the end of a run; always leave it `off`.

## Deterministic analysis scenarios (M2)

`scripts\run-scenarios.py` injects the eight scored faults one at a time, waits
for the detector to open one incident, runs `POST /api/incidents/{ref}/analyze`,
records the top-1 suspect, reverts, and prints a scoreboard (gate: >= 7/8):

```powershell
uv run --project apps\api python scripts\run-scenarios.py             # all eight, ~75 min
uv run --project apps\api python scripts\run-scenarios.py --only S4,S7
```

Before each injection it waits for 240 s of continuous quiet (no breach, no
open incident), up to 900 s, and after an API restart for the detector's 300 s
warm-up. Run it in the background; results go to `runtime\scenarios.json`. Do not
run `next build` or other CPU-heavy work at the same time: latency scenarios are
judged against per-service baselines and a busy host moves them.

To analyse one incident by hand: open it in the dashboard and press
**Analyze**, or `curl -X POST http://127.0.0.1:8001/api/incidents/INC-00001/analyze`.
Re-running is safe; existing evidence keeps its `E####` code.

## Models (P10)

The lab runs offline by default: `LLM_PROVIDER=stub`. To use a real model, put
`LLM_PROVIDER=zenmux` (or `openai_compatible` with `LLM_BASE_URL`) and
`LLM_API_KEY` in `.env`, restart the API, then:

```powershell
curl.exe -X POST -H "content-type: application/json" -d '{\"target\":\"all\"}' http://127.0.0.1:8001/api/settings/models/test
```

To try a real model without changing `.env`, keep `LLM_API_KEY` there, restart
the API, then switch at runtime and back (the override is stored in the
database and survives restarts, so always reset it):

```powershell
curl.exe -X PATCH -H "content-type: application/json" -d '{\"llm\":{\"provider\":\"zenmux\"}}' http://127.0.0.1:8001/api/settings/models
curl.exe -X PATCH -H "content-type: application/json" -d '{\"reset\":true}' http://127.0.0.1:8001/api/settings/models
```

The default ZenMux model is a reasoning model: it spends ~60 hidden tokens
even on a one-word answer. The connection test uses `LLM_MAX_TOKENS`; a test
that reports "all N output tokens were used" needs a larger value. Reset the
override before `check.ps1`.

The first `onnx_local` embedding downloads ~614 MB into `runtime/models/` and
loads in ~2–6 s; later calls take tens of milliseconds. Set
`EMBEDDING_ALLOW_DOWNLOAD=false` to forbid network access after the first
download, or `EMBEDDING_PROVIDER=stub` to skip the model entirely.

## Agent (P11)

Start an investigation from the dashboard (**Agent** page, or **Agent** on an
incident) or with:

```powershell
curl.exe -X POST "http://127.0.0.1:8001/api/incidents/INC-00001/investigate?wait=true"
curl.exe http://127.0.0.1:8001/api/agent/runs/1          # run + ordered steps
curl.exe -X POST http://127.0.0.1:8001/api/agent/runs/1/cancel
```

One run per incident at a time (409 otherwise); resolved incidents are refused.
To check a hand-written (or deliberately bad) diagnosis against an incident
without storing anything:

```powershell
curl.exe -X POST -H "content-type: application/json" -d '{\"root_cause\":{...}}' http://127.0.0.1:8001/api/incidents/INC-00001/diagnosis/validate
```

`scripts\run-scenarios.py --agent --resolve` adds the investigation to every
scenario, checks each cited code against `GET /api/incidents/{ref}/evidence`,
and lets the detector resolve the incident after the revert (results:
`--out runtime\scenarios-p11.json`). Set `PYTHONIOENCODING=utf-8` when the output
goes to a file on Windows.

A run that must outlive the agent harness (the scenario runner, the API during
it) is started detached:

```powershell
Start-Process uv -ArgumentList "run","uvicorn","app.main:app","--port","8001" `
  -WorkingDirectory apps\api -RedirectStandardOutput runtime\api.log `
  -RedirectStandardError runtime\api.err.log -WindowStyle Hidden
```

## Memory (P12)

A resolved incident with a valid diagnosis is summarized and embedded
automatically (`memory.stored` on the event bus). At startup the API checks
that the `memories.embedding` column, the stored model and the configured
embedder agree; on a mismatch retrieval is off and `/api/memory/status` says why.

```powershell
curl.exe http://127.0.0.1:8001/api/memory/status
curl.exe "http://127.0.0.1:8001/api/memory/search?incident=INC-00070&k=5"
curl.exe "http://127.0.0.1:8001/api/memory/search?q=payment+charges+fail"
curl.exe -X POST "http://127.0.0.1:8001/api/memory/incidents/INC-00070?force=true"
```

```powershell
uv run --project apps\api python scripts\seed-memory.py            # 10 synthetic seeds
uv run --project apps\api python scripts\seed-memory.py --remove
uv run --project apps\api python scripts\reembed.py --dry-run      # what would change
uv run --project apps\api python scripts\reembed.py                # after changing the embedder
uv run --project apps\api python scripts\reembed.py --resummarize  # after changing the summary template
```

Changing the embedding **dimension** also needs a migration for the column;
`reembed.py` refuses to write vectors of the wrong size. Stop the API first when
switching models, then restart it so the guard re-reads the settings.

`scripts\run-scenarios.py --history runtime\scenarios-p11.json` checks
retrieval: each new incident's search must rank the same scenario's earlier
incident in the top 3.

## Remediation and approvals (P14)

A diagnosis that passes the confidence gate proposes its action as a PENDING
approval. Decide on the **Approvals** page or with:

```powershell
curl.exe "http://127.0.0.1:8001/api/approvals?status=pending"
curl.exe -X POST "http://127.0.0.1:8001/api/approvals/4/dry-run?actor=me"
curl.exe -X POST -H "content-type: application/json" -d '{\"actor\":\"me\",\"note\":\"ok\"}' "http://127.0.0.1:8001/api/approvals/4/approve?wait=true"
curl.exe -X POST -H "content-type: application/json" -d '{\"actor\":\"me\"}' http://127.0.0.1:8001/api/approvals/4/reject
curl.exe "http://127.0.0.1:8001/api/approvals/audit?incident=INC-00088"
```

Propose a different catalog action instead of the recommendation (the pending
one is withdrawn):

```powershell
curl.exe -X POST -H "content-type: application/json" -d '{\"action_id\":\"restart_service\",\"target\":\"email\",\"replace\":true}' http://127.0.0.1:8001/api/incidents/INC-00090/approvals
```

`AGENT_AUTO_REMEDIATE=true` in `.env` lets low-risk (`approval=auto`) actions
approve themselves; it is `false` by default. The audit file is
`runtime\audit\actions.jsonl`.

The live P14 check (expiry, rejection, unsafe proposals, approve → execute,
token replay, restart) takes about 25 minutes and needs no open incident:

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run --project apps\api python -u scripts\remediation-check.py --out docs\evidence\p14-remediation.json
```

Restart the API detached (survives the agent harness; logs in
`runtime\api.log` / `runtime\api.err.log`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\api-detached.ps1
```

## Verification and reports (P15)

After an approved action executes, the verifier watches the detector until
every criterion holds for 4 consecutive cycles (PASSED → RESOLVED) or 420 s
pass (FAILED → rollback where safe → ESCALATED). Follow it on the **Reports**
page or with:

```powershell
curl.exe http://127.0.0.1:8001/api/incidents/INC-00095/verifications
curl.exe http://127.0.0.1:8001/api/incidents/INC-00095/report
curl.exe -o INC-00095.md "http://127.0.0.1:8001/api/incidents/INC-00095/report.md?download=true"
```

The full loop on the live stack (inject → detect → agent → approve → execute →
verify → report), including the deliberately wrong action W1 that must fail
verification and escalate:

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run --project apps\api python -u scripts\run-scenarios.py --only S1,S5,S7,W1 --remediate --out docs\evidence\p15-scenarios.json
```

After changing the verdict wording, rebuild the memory summaries:
`uv run --project apps\api python scripts\reembed.py --resummarize`.

## Hardening and evaluation (P16)

**One command for the whole evaluation** (stack and API up, no open incident):

```powershell
scripts\eval.ps1                    # S1-S8 + W1, full loop with remediation, ~2 h
scripts\eval.ps1 -Only S1,S5,W1     # a subset
scripts\eval.ps1 -AnalysisOnly      # detection + analysis + agent, no remediation
```

It prints the scoreboard (one row per scenario, then detection/recovery/agent
latency, cost, spend against the daily cap, verdicts, rate-limit rejections and
open incidents afterwards) and writes `runtime\eval.json`,
`runtime\scoreboard.md` and `runtime\secret-audit.json`. Exit code 0 only when
the scenario gate and the secret audit both pass. Run it in the background and
do not build or run other CPU-heavy work meanwhile (see M2 above).

**Secret masking audit** on its own (needs the API and the dashboard; an
unreachable page counts as a failure):

```powershell
uv run --project apps\api python scripts\secret-audit.py --out runtime\secret-audit.json
```

It never prints a secret: a finding shows only its length and first two
characters.

**Cost, latency, outcomes**: the **Settings** page, or
`curl.exe "http://127.0.0.1:8001/api/stats?window_h=24"`.

**Rate limits** apply to writes only, per client address, per 60 s (table in
`docs/ARCHITECTURE.md`). A limited call gets 429 `rate_limited` with
`Retry-After`. Set `RATE_LIMIT_ENABLED=false` in `.env` and restart the API to
turn them off. A restart also clears the counters. The live check scripts
(`scripts\_http.py`) wait for `Retry-After` and send the request again, so
`verify-faults.py` still passes with the limits on (it injects 19 faults, more
than the 10 per minute that `fault_inject` allows).

`docs/DEMO.md` is a 15–20 minute walkthrough of the whole loop for an audience.

## Reproduce M1–M4 from scratch

On a clean machine with the prerequisites above. Each step names its gate.

1. **Bring up the lab** (first run pulls several GB):

   ```powershell
   git clone <this repo>; cd DocOck
   scripts\bootstrap-demo.ps1          # pinned upstream demo checkout
   scripts\up.ps1                      # creates .env, starts demo + observability + Postgres
   cd apps\api; uv sync; uv run alembic upgrade head; cd ..\..
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\api-detached.ps1
   cd apps\dashboard; pnpm install; pnpm dev   # separate terminal, :3001
   scripts\health.ps1                  # every row OK
   scripts\check.ps1                   # quality gate; add -Live for the telemetry contracts
   ```

   Wait 5 minutes after the API starts: the detector learns baselines during a
   300 s warm-up. The model provider is the offline `stub`; no key is needed.

2. **M1 — fault → observe → recover** (P0–P7):

   ```powershell
   uv run --project apps\api python scripts\verify-faults.py      # every v1 fault injects and reverts
   scripts\fault-kill-test.ps1                                    # TTL survives an API kill
   ```

   Then in the browser: **Fault Lab → payment_failure 100% → Dry run → Inject**,
   watch the Overview break, **Revert**, watch it recover.

3. **M2 — deterministic suspects** (P8–P9), gate ≥ 7/8 top-1:

   ```powershell
   uv run --project apps\api python scripts\verify-incidents.py
   uv run --project apps\api python -u scripts\run-scenarios.py --out runtime\scenarios.json
   ```

4. **M3 — agent diagnosis** (P10–P13), gate: every diagnosis correct with only
   valid evidence codes, memory finds the earlier incident in the top 3:

   ```powershell
   uv run --project apps\api python -u scripts\run-scenarios.py --agent --resolve --out runtime\scenarios-p11.json
   uv run --project apps\api python -u scripts\run-scenarios.py --agent --resolve --history runtime\scenarios-p11.json --out runtime\scenarios-p12.json
   ```

5. **M4 — approval → verify → report** (P14–P16), gate: verified recovery on 3
   scenarios, the rejection path, a wrong action that fails verification and
   escalates, a report with timeline + evidence + before/after:

   ```powershell
   $env:PYTHONIOENCODING = "utf-8"
   uv run --project apps\api python -u scripts\remediation-check.py --out runtime\p14-remediation.json   # rejection, expiry, unsafe proposals, token replay
   scripts\eval.ps1 -Only S1,S5,S7,W1                                                                     # verified recovery x3 + wrong action, scoreboard, secret audit
   ```

   Open **Reports** for the incidents in the scoreboard.

   If the check's email flow fails with "confidence below 0.7", the payment
   flow's residue still ranks checkout close to email. Rerun that flow alone:
   `scripts\remediation-check.py --only-restart --out runtime\p14-restart.json`.

6. **Leave it clean**: **Fault Lab → Reset all** (or
   `curl.exe -X POST http://127.0.0.1:8001/api/faults/reset-all`), and check that
   **Incidents** has no open incident.

## Endpoints

| What | URL |
|---|---|
| Astronomy Shop | http://127.0.0.1:8080 |
| Prometheus | http://127.0.0.1:9090 |
| Jaeger UI | http://127.0.0.1:16686/jaeger/ui |
| OpenSearch | http://127.0.0.1:9200 |
| Grafana *(kept: `frontend-proxy` depends on it)* | http://127.0.0.1:3000 |
| flagd UI | http://127.0.0.1:4000 |
| Load generator | http://127.0.0.1:8089 |
| Postgres (pgvector) | `127.0.0.1:5433`, db/user `docock` |
| DocOck API | http://127.0.0.1:8001/api/health |
| DocOck dashboard *(P3+)* | http://127.0.0.1:3001 |
Jaeger's query API lives under the configured base path — do not assume
`/api/traces`. The health script probes `/jaeger/ui/api/services`; if upstream
changes the base path, update `JAEGER_BASE_PATH` and `scripts/_common.ps1`.

## Everyday commands

```powershell
scripts\health.ps1                  # one-shot status table
scripts\health.ps1 -WaitSeconds 180 # block until healthy
scripts\reset.ps1                   # restore flags, reload flagd
scripts\reset.ps1 -Hard             # also restart every demo container
scripts\down.ps1                    # stop everything, keep data
scripts\down.ps1 -Volumes           # stop and delete Postgres data
scripts\check.ps1                   # API lint + types + tests, dashboard lint + tests + build
scripts\check.ps1 -ApiOnly          # skip the dashboard steps
scripts\db-check.ps1                # migrations against an empty scratch DB
```

## What runs where

| Compose project | Purpose | Invocation |
|---|---|---|
| `docock-demo` | upstream demo, flagd, Prometheus, Jaeger, OpenSearch, Grafana | upstream compose + `infra/compose/lab.override.yaml` |
| `docock-lab` | our Postgres/pgvector | `infra/compose/base.yaml` |

Both invocations are encapsulated in `scripts/up.ps1` / `down.ps1`. If you run
Compose by hand, copy the argument list from those scripts — `-p`,
`--project-directory`, and the three `-f` files all matter.

## Troubleshooting

**`up.ps1` reports a port conflict.**
Something outside the lab holds 8080/9090/16686/9200/5433/… . Find it:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 8080,9090,16686,9200,5433
```

or stop leftovers with `scripts\down.ps1`.

**Stack is up but `/jaeger/ui` 404s.**
Jaeger v2 serves the UI under `base_path`. Check `src/jaeger/config.yml` in the
demo checkout and align `JAEGER_BASE_PATH`.

**Flag changes do not take effect.**
flagd watches `runtime/flagd/demo.flagd.json`. Two traps, both hit during P1:

- A write must be **UTF-8 without BOM**. A BOM makes flagd log
  `invalid character 'ï' looking for beginning of value` and serve zero flags.
- The file's mtime must move **forward**. `Copy-Item` preserves the source
  timestamp, so a reseed can appear "older" and be ignored. Use
  `Copy-FlagSeed` (in `scripts/_common.ps1`) or touch the file.

Confirm with:

```powershell
Invoke-WebRequest -Method Post -ContentType application/json -Body '{}' `
  http://127.0.0.1:8016/ofrep/v1/evaluate/flags/paymentFailure
```

If it is still stale: `docker restart flagd`, then re-check. `scripts\reset.ps1`
does both.

**OpenSearch never turns green.**
It is the slowest dependency (~60–120 s) and limited to 1 GB. Check
`docker logs opensearch --tail 50`. If the VM is memory-starved, raise Docker
Desktop memory rather than removing limits.

**Containers keep restarting.**
`docker compose -p docock-demo ps` shows the state. Most demo services depend on
the collector; if `otel-collector` is crash-looping, all of them look broken.
Start there.

**Metrics look stale (up to a minute behind).**
Span metrics are pushed by the collector's `span_metrics` connector, not
scraped. Upstream flushes every 60 s; `infra/otel-collector/extras.yaml` lowers
that to 10 s and is mounted by `lab.override.yaml`. If you changed the collector
and freshness regressed, check that the extras file is the mounted one:

```powershell
docker inspect otel-collector --format '{{range .Mounts}}{{.Destination}} <- {{.Source}}{{"\n"}}{{end}}'
```


**`checkout` / `product-catalog` report unhealthy but serve fine.**
Their bundled `grpc_health_probe` has no connect/RPC deadline and can exceed the
5 s healthcheck timeout on a loaded host. `infra/compose/lab.override.yaml`
passes `-connect-timeout=2s -rpc-timeout=2s`. Remove that override only if a
future image fixes the probe.

**The storefront returns 500 after restarting a backend.**
The frontend's gRPC channel caches the failed address; restart `frontend` (or
wait for reconnection) after recreating `product-catalog`, `cart`, etc.

**`up.ps1` fails with "container opensearch is unhealthy" after Docker Desktop starts.**
On a cold Docker start OpenSearch needs longer than its compose health budget
(about 60 s). Wait until `docker inspect opensearch --format '{{.State.Health.Status}}'`
says `healthy`, then run `scripts\up.ps1 -SkipPull` again.

**Incidents open with no fault active; checkout or cart p95 is seconds, not milliseconds.**
Seen on 2026-10-26 after a cold Docker restart: cart's flag lookups waited 1.6 s
or more on a starved `flagd`, `quote` went from 2 ms to 300 ms, and the scenario
runner caught these incidents instead of the injected fault. Compare
per-service p95 with Prometheus or the **Telemetry** page (healthy: cart
~6 ms, quote ~2 ms, shipping ~6 ms, checkout ~150 ms). Fix: `scripts\reset.ps1
-Hard`, wait for health, then restart the API (`scripts\api-detached.ps1`). The
detector learned its baselines on the slow stack and must learn them again.
Do this before `scripts\eval.ps1` whenever the stack was restarted cold.

**A flag fault is ACTIVE but its service shows no symptom (S3: no incident within 240 s).**
The service started while flagd was down, so its flag client answers with the
defaults. Seen on 2026-10-26: checkout and flagd restarted in the same second.
Payment still received every charge while `paymentUnreachable` was `on`.
`docker restart checkout` fixed it, and checkout errors started 40 s later.
`scripts\reset.ps1 -Hard` now restarts flagd first, waits for :8013, then
restarts the other containers. A plain `docker compose restart` still has this race.

**Latency incidents on ad, checkout, payment, quote and shipping, then every check times out.**
Seen on 2026-10-26 15:50–16:15 UTC. Docker Desktop's VM DNS resolver timed
out (`log\vm\init.log` full of `failed to query external DNS server` for
container names), so calls between services slowed, then the engine API
answered 500 and `docker ps` hung. The API returns 503 and the scenario runner
records the error. Fix: `docker desktop restart`, then `scripts\reset.ps1 -Hard`
(flagd first), wait for `scripts\health.ps1` to pass, then restart the API.

**A checkout latency or silence incident opens while `scripts\check.ps1` runs.**
Seen twice on 2026-10-26 (INC-00158, INC-00184). `next build` loads every host
core, and the load generator then sent no checkout for 2.5 min while browsing
went on. The detector reports a real gap, and the incident resolves itself about
a minute after the gate ends. Do not run the gate during `scripts\eval.ps1`.

**Throughput incidents open again and again on cart, frontend and product-catalog with no fault active.**
Seen on 2026-10-26 after the host slept. Prometheus has a gap, and the load
generator ramps up again after wake. The detector learned the low ramp-up
traffic as its baseline (frontend 7.9/s against a normal ~25/s). It then reads
normal traffic as a 3x surge. A breaching sample does not update its own
baseline, so the breach does not clear. The scenario runner waits for quiet that
never comes. Fixed after P16: when two detector cycles are more than 120 s apart,
the detector drops its live samples and repeats the 300 s warm-up
(`detector_gap_reset` in the API log, `last_gap_reset_at` in
`/api/detection/status`). During warm-up, throughput and silence hits no longer
block learning, so a ramp cannot freeze the baseline. If the symptom comes back,
restart the API (`scripts\api-detached.ps1`) when the traffic is steady again.

**Postgres restarts with "there appears to be PostgreSQL data in /var/lib/postgresql/data".**
Postgres 18 moved its data layout. The volume must mount at
`/var/lib/postgresql`, not `/var/lib/postgresql/data`. Delete the volume with
`docker compose -p docock-lab ... down -v` if it was created with the old path.

**Everything is wedged.**
```powershell
scripts\down.ps1
docker compose -p docock-lab -f infra\compose\base.yaml down -v   # drops data
scripts\up.ps1
```

**Disk is filling up.**
Never `docker system prune -a` here unless you intend to re-pull ~10 GB of demo
images. `docker image prune` (dangling only) is safe.
