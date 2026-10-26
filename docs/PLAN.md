# DocOck — Local Incident Response Lab

**Implementation plan** · v1 · 2026-10-24

Live phase status and verification evidence: [`PROGRESS.md`](PROGRESS.md).

A standalone, local AI-on-call lab: a web dashboard injects faults into a live
microservice demo, watches real telemetry, detects an incident, investigates it
with deterministic analysis plus an LLM, proposes a bounded remediation, waits
for human approval, executes it, verifies recovery, and writes the report.

Nothing here depends on a previous zip, a fault CLI, or a hardcoded model.

---

## 0. How to read this

- **§2–§4** are the decisions that changed from the original design, and why.
- **§5–§13** are the target system (repo, ports, data model, catalogs, contracts).
- **§14–§15** are the milestones and the phase-by-phase task list — the actual
  work order.
- **§17–§19** are tests, risks, and the first week.

Effort figures are focused dev-days for one person. They are estimates, not
commitments.

---

## 1. What we are building

```
inject fault → observe → detect → investigate → diagnose
→ retrieve similar incidents → propose fix → approve/reject
→ execute → verify → report
```

**In scope (v1)**

| Area | v1 target |
|---|---|
| Environment | OpenTelemetry Astronomy Shop on Docker Compose, local only |
| Faults | 13 application faults (flagd) + 6 infrastructure faults, all reversible |
| Telemetry | Prometheus metrics, Jaeger traces, OpenSearch logs, Docker container state |
| Detection | Deterministic threshold + baseline detector, no LLM |
| Analysis | Deterministic suspects, error-origin ranking, log clustering, topology |
| Agent | One LLM diagnosis loop over a fixed tool catalog, evidence-cited |
| Memory | pgvector retrieval over resolved incidents, hypothesis-boost only |
| Safety | Allowlisted targets, action catalog, server-issued approval tokens, audit trail |
| Output | Verified recovery + JSON/Markdown incident report |

**Explicit non-goals for v1**

- Kubernetes, Kafka, Celery, a dedicated vector DB, multi-tenant auth
- Arbitrary shell or Docker access for the LLM
- Auto-remediation without approval (config flag exists, default **off**)
- True packet-loss / jitter injection (see §3, row 3)
- PDF export, alerting integrations, HA, cloud deployment

---

## 2. Verified environment facts

Checked on this machine before planning. These drive several decisions.

| Fact | Value | Consequence |
|---|---|---|
| Repo | `D:\Coding\DocOck`, branch `main`, 1 commit | Greenfield; demo checkout is untracked |
| Demo | `opentelemetry-demo/` already cloned, not a nested git repo | Needs a reproducibility decision (§3, row 1) |
| Docker | Engine 29.9.2, Compose v5.6.1 | Compose override strategy is fine |
| Images already pulled | 20 images, **7.2 GB**, demo core services | No re-pull cost for core |
| Free disk (D:) | **~21 GB** | Tight budget; see §16 |
| GPU | AMD Radeon 890M (integrated), **no CUDA** | Embeddings on CPU (ONNX), no torch required |
| Python | 3.14 default; 3.11 also installed | Use **3.11** venv — ML wheels lag 3.14 |
| Node | v24.16.0, pnpm 11.22.0 | pnpm workspace |
| `uv` | 0.8.13 | Python dependency management |
| Demo traffic | load-generator running by default | Traffic exists from minute one |
| Demo observability | `compose.observability.yaml` adds Jaeger, Prometheus, OpenSearch, Grafana | We use all but Grafana |
| Application faults | 21 flags shipped in `src/flagd/demo.flagd.json`; 13 usable in this compose profile | No service patching needed; faults are **variant selections**, not free-form percentages |
| Jaeger | v2, `base_path: /jaeger/ui`, in-memory 25k traces | API is **not** at `/api/*`; retention is short |
| Logs index | `otel-logs` (dated) in OpenSearch | Log adapter targets that index |
| LLM access | ZenMux OpenAI-compatible API; `meta/muse-spark-1.3-contributor` **exists** | $0.10/M in, $0.20/M out — cheap enough for the lab |

---

## 3. Corrections to the source design

The design is sound. These seven points would break or stall if implemented
literally on this machine.

| # | Original design | Problem here | Decision |
|---|---|---|---|
| 1 | "Discard the old zip, fresh repo" | `opentelemetry-demo/` is 1,000+ files of upstream code sitting untracked in the repo root | Keep it **out of git**; add `scripts/bootstrap-demo.ps1` pinning an exact upstream tag/commit, and `.gitignore` the checkout. Submodule is the alternative if you want it visible in the tree — decision needed (§20). |
| 2 | Application faults patched into services | The demo already exposes `paymentFailure`, `cartFailure`, `paymentUnreachable`, `recommendationCacheFailure`, `adFailure`, `emailMemoryLeak`, `intlShippingSlowdown`, `imageSlowLoad`, `loadGeneratorFloodHomepage`, `adHighCpu`, `failedReadinessProbe`, `productCatalogLockContention`, and more | **Drive flagd** — atomic file replace + OFREP read-back to confirm. Zero upstream edits, all faults instantly reversible. Note: flags expose fixed **variants** (`off`, `10%`, `100%`, `5sec`, `1000x`…), not numeric parameters — the UI must offer the declared variants (§8). |
| 3 | `network_latency` / `packet_loss` in the v1 fault catalog | Demo images are mostly distroless Go/Java — no `tc` binary, no `NET_ADMIN`. On Docker Desktop/Windows the netns lives inside the WSL VM, so host-side `tc` is fragile | v1 ships **`network_partition`** (`docker network disconnect/connect`) and **`freeze`** (`pause/unpause`), which cover "unreachable" semantics portably. `netem` delay/loss becomes v1.1 behind a privileged sidecar. |
| 4 | Safety via `incident-zero.managed=true` label on every container | Compose cannot add a label to a service it does not own without an override for each one | Use the **`com.docker.compose.project` label** (set automatically by Compose) + name allowlist + network check. Add `docock.managed=true` in *our* override for containers we create. |
| 5 | FastAPI :8000, Next :3000, Postgres :5432 | Hard conflicts: telemetry-docs **8000**, Grafana **3000**, astronomy-db **5432**. Also Jaeger publishes host **4317**, which the collector already publishes | API **8001**, dashboard **3001**, Postgres+pgvector **5433**, `JAEGER_GRPC_PORT=14317`. Full table in §6. |
| 6 | Local Qwen3-Embedding-0.6B via sentence-transformers | No CUDA; torch has no 3.14 wheels; torch+model costs ~3 GB disk for a low-QPS workload | Python 3.11 + **ONNX int8** (`fastembed`/`optimum`), 1024-d, CPU. Provider interface with a deterministic stub so Phases 0–9 never block on ML deps. |
| 7 | Build embeddings early (step 16) | Blocks all platform work behind a model download | Embedding work moved to **P10**, after the deterministic pipeline is proven. Memory is a boost, not a dependency (§11). |

Also worth stating explicitly: `meta/muse-spark-1.3-contributor` is real and cheap,
but the Contributor tier may allow prompt/output use for Meta product
improvement. Use it for lab data only; the provider interface exists so a
non-contributor or self-hosted model is a config change.

---

## 4. Architecture and trust boundaries

```
┌──────────────────────────────────────────────────────────────┐
│  Dashboard :3001  (Next.js, no Docker access, no API keys)   │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + SSE (localhost only)
┌───────────────────────────▼──────────────────────────────────┐
│  API :8001  (FastAPI)                                        │
│                                                              │
│  Incident Manager · Detector · Evidence Engine · Topology    │
│  Fault Controller · Action Controller · Verifier · Reporter  │
│  Agent Orchestrator · Model Gateway · Memory                 │
└───┬──────────┬──────────┬──────────┬──────────┬──────────────┘
    │          │          │          │          │
 Prometheus  Jaeger   OpenSearch  Docker    PG+pgvector :5433
 :9090      :16686     :9200      socket
    │          │          │          │
    └──────────┴────┬─────┴──────────┘
                    ▼
        OpenTelemetry Astronomy Shop  (:8080)
        + flagd (:8013 / OFREP :8016)
```

**The three rules that matter**

1. **Browser → API → infrastructure.** The dashboard never touches Docker,
   Prometheus, or the LLM key directly.
2. **LLM → tool requests only.** The model may call `get_traces`, never
   `docker restart`. Remediation is an *action ID* from a catalog.
3. **Evidence is created by code.** The model selects evidence IDs; it cannot
   invent observables, and the validator rejects unknown IDs.

---

## 5. Repository layout

```
DocOck/
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── app/
│   │   │   ├── main.py              # app factory, lifespan, routers
│   │   │   ├── config.py            # pydantic-settings, .env
│   │   │   ├── api/                 # health, overview, telemetry, faults,
│   │   │   │                        # incidents, approvals, memory,
│   │   │   │                        # reports, settings, events(SSE)
│   │   │   ├── telemetry/           # prometheus, jaeger, logs, docker, cache
│   │   │   ├── faults/              # catalog, controller, flagd, container,
│   │   │   │                        # network, safety, watchdog
│   │   │   ├── detection/           # detector, baselines, thresholds
│   │   │   ├── analysis/            # metrics, traces, logs, topology, suspects
│   │   │   ├── evidence/            # store, ids, serialization
│   │   │   ├── agent/               # orchestrator, tools, prompts, schemas,
│   │   │   │                        # validator, budget
│   │   │   ├── models/
│   │   │   │   ├── llm/             # base, openai_compatible, zenmux
│   │   │   │   └── embeddings/      # base, onnx_local, remote, stub
│   │   │   ├── memory/              # summarizer, repository, retrieval
│   │   │   ├── remediation/         # catalog, planner, approval, executor
│   │   │   ├── verification/        # verifier, criteria
│   │   │   ├── reports/             # generator (json, markdown)
│   │   │   ├── events/              # in-process bus + SSE broker
│   │   │   └── db/                  # models, session, migrations/
│   │   └── tests/
│   └── dashboard/
│       ├── package.json
│       ├── app/                     # (overview|faults|telemetry|incidents|
│       │                            #  agent|approvals|reports|memory|
│       │                            #  models|settings)/page.tsx
│       ├── components/              # charts, tables, timeline, badges
│       ├── lib/                     # api client, sse, formatters, types
│       └── e2e/
├── infra/
│   ├── compose/
│   │   ├── base.yaml                # project name, network, labels
│   │   └── lab.override.yaml        # demo override: ports, flagd mount,
│   │                                #   jaeger port, resource caps
│   ├── postgres/init.sql            # CREATE EXTENSION vector; schema bootstrap
│   ├── flagd/demo.flagd.json        # runtime working copy (gitignored runtime/)
│   └── otel-demo/                   # pinned upstream checkout (gitignored)
├── scripts/
│   ├── bootstrap-demo.ps1           # clone demo at pinned ref
│   ├── up.ps1 / down.ps1 / reset.ps1
│   ├── seed-memory.py
│   └── run-scenarios.py
├── docs/
│   ├── PLAN.md                      # this file
│   ├── ARCHITECTURE.md
│   ├── RUNBOOK.md                   # local bring-up + troubleshooting
│   └── ADR/                         # decisions 001..n
├── .env.example
├── .gitignore
└── README.md
```

---

## 6. Runtime topology and ports

| Component | Port | Notes |
|---|---|---|
| Astronomy Shop frontend | 8080 | demo frontend-proxy |
| **DocOck dashboard** | **3001** | Next.js |
| **DocOck API** | **8001** | FastAPI |
| **DocOck Postgres + pgvector** | **5433** | `pgvector/pgvector:pg18` |
| flagd | 8013 / OFREP 8016 | fault injection surface |
| flagd UI | 4000 | read-only, useful for debugging |
| Prometheus | 9090 | OTLP push + scrape |
| Jaeger UI/API | 16686 | **under `/jaeger/ui`** |
| OpenSearch | 9200 | index `otel-logs*` |
| Collector | 4317/4318 | demo-internal + host |
| Jaeger gRPC | **14317** | must be remapped — 4317 collides |
| Grafana | not started | we build our own UI; saves ~500 MB |
| astronomy-db | 5432 | demo's own Postgres |

Resource caps for our containers: API 512 MB, dashboard 512 MB, Postgres 512 MB.
Leave the demo's own limits untouched.

---

## 7. Data model

Postgres 18 + pgvector. SQLAlchemy 2 async + Alembic.

| Table | Purpose | Key columns |
|---|---|---|
| `incidents` | first-class incident object | `public_id` (`INC-00042`), `status`, `severity`, `opened_at`, `closed_at`, `trigger` jsonb, `title`, `primary_service` |
| `incident_events` | append-only timeline, SSE replay | `incident_id`, `seq`, `kind`, `payload` jsonb, `at` |
| `evidence` | immutable observables | `incident_id`, `code` (`E0042`), `type`, `source`, `service`, `at`, `data` jsonb, `hash`; unique `(incident_id, code)` |
| `suspects` | deterministic ranking snapshots | `incident_id`, `service`, `score`, `signals` jsonb, `rank` |
| `agent_runs` | one investigation attempt | `incident_id`, `status`, `provider`, `model`, `tokens_in/out`, `cost_usd`, `error`, timings |
| `agent_steps` | tool calls + reasoning trace for the UI | `run_id`, `seq`, `kind`, `tool`, `input`, `output`, `latency_ms`, `at` |
| `diagnoses` | validated structured output | `run_id`, `root_cause_service`, `fault_class`, `confidence`, `summary`, `evidence_codes` text[], `recommended_action` jsonb, `valid` |
| `faults` | injected faults | `type`, `target`, `params`, `status`, `injected_at`, `expires_at`, `reset_at`, `injection_ref` jsonb |
| `actions` | remediation attempts | `incident_id`, `action_id`, `target`, `args`, `status`, `pre_snapshot`, `post_snapshot`, `result`, `rollback` |
| `approvals` | server-issued approval tokens | `action_row_id`, `status`, `token_hash`, `expires_at`, `decided_at`, `decided_by`, `note` |
| `verifications` | before/after verdicts | `action_row_id`, `verdict`, `before`, `after`, `criteria`, `observed_for_s` |
| `memories` | resolved-incident knowledge | `incident_id`, `summary`, `embedding vector(1024)`, `root_cause`, `fault_class`, `services` text[], `metadata` jsonb + HNSW (cosine) |
| `reports` | rendered artifacts | `incident_id`, `format`, `content`, `created_at` |
| `settings` | runtime model config (no secrets) | `key`, `value` jsonb, `updated_at` |
| `baselines` | rolling service baselines | `service`, `metric`, `value`, `window_s`, `computed_at` |

Incident states (single source of truth in `app/domain/state.py`):

```
OPEN → INVESTIGATING → DIAGNOSED → AWAITING_APPROVAL
     → REMEDIATING → VERIFYING → RESOLVED | ESCALATED
                     ↘ REJECTED → ESCALATED
```

`DIAGNOSED → RESOLVED` is also allowed (added in P11): when the symptoms clear
without a remediation (the fault was reverted or expired), the detector resolves
the incident, so it can reach memory before P14 exists.

Every transition writes an `incident_event` and publishes to SSE.

---

## 8. Fault catalog v1

One JSON-schema-validated catalog, one Pydantic model, one dynamic form in the UI.

Application flags are **not** free-form numbers. Each exposes a fixed variant set,
and the "severity" control in the UI is a variant selector — the API rejects any
value outside the declared set. Verified against `demo.flagd.json`:

**Application faults (flagd)**

| Fault ID | Flag | Target | Selectable variants |
|---|---|---|---|
| `payment_failure` | `paymentFailure` | payment | `10%`, `25%`, `50%`, `75%`, `90%`, `100%` |
| `payment_unreachable` | `paymentUnreachable` | payment | `on` |
| `cart_failure` | `cartFailure` | cart | `10%`, `25%`, `50%`, `75%`, `90%`, `100%` |
| `recommendation_cache_failure` | `recommendationCacheFailure` | recommendation | `on` |
| `ad_failure` | `adFailure` | ad | `on` |
| `ad_high_cpu` | `adHighCpu` | ad | `on` |
| `ad_manual_gc` | `adManualGc` | ad | `on` |
| `catalog_lock_contention` | `productCatalogLockContention` | product-catalog | `on` |
| `email_memory_leak` | `emailMemoryLeak` | email | `1x`, `10x`, `100x`, `1000x`, `10000x` |
| `shipping_slowdown` | `intlShippingSlowdown` | shipping | `5sec`, `10sec` |
| `image_slow_load` | `imageSlowLoad` | image-provider | `5sec`, `10sec` |
| `readiness_failure` | `failedReadinessProbe` | cart | `on` |
| `traffic_flood` | `loadGeneratorFloodHomepage` | frontend | `on` |

**Deliberately excluded**

| Flag | Why |
|---|---|
| `productCatalogFailure` | Its targeting rule hardcodes one product and returns `off` on both branches — currently a no-op upstream. Re-check on demo upgrade. |
| `kafkaConsumerDead`, `kafkaQueueProblems` | Kafka is not part of the compose profile in use. |
| `cartMetricCardinalityOverflow` | Self-observability demo, not a service fault. |
| `emitRawPii` | Data-governance demo, not a failure mode. |
| `aiRunawayAgent`, `aiSlowResponse` | Target the demo's own AI agent, not our lab services. |

**Infrastructure faults (Docker)** — no ambiguity about blast radius

| Fault ID | Mechanism | Targets | Params |
|---|---|---|---|
| `service_down` | stop / start | allowlisted services | — |
| `service_freeze` | pause / unpause | allowlisted services | — |
| `network_partition` | disconnect / connect network | allowlisted services | `direction` |
| `cpu_pressure` | `docker update --cpus` | allowlisted services | `cpus` 0.05–2.0 |
| `memory_pressure` | `docker update --memory` | allowlisted services | `memory_mb` 64–2048 |
| `load_spike` | bespoke locust stage | load-generator | `users`, `duration_s` |

**Lifecycle contract**

- `POST /api/faults` → validate → safety check → inject → store → SSE `fault.injected`
- Every fault gets `expires_at` (default 15 min, max 60) and a **watchdog** that
  auto-reverts; a crashed API cannot leave the lab broken.
- `DELETE /api/faults/{id}` → idempotent revert, verified by read-back
  (flagd OFREP value, container state, network attachment).
- `POST /api/faults/reset-all` → used by the panic button and by test teardown.
- Injection and revert are audited with actor, timestamp, before/after state.

**flagd write protocol** (the only writes to demo config)

1. Read current JSON, set `flags[key].defaultVariant`. Validate the variant is
   declared for that flag.
2. Write a temp file in the same directory as UTF-8 **without BOM** (a BOM makes
   flagd fail with `invalid character 'ï'`), then `os.replace` → atomic.
3. Ensure the mtime is current. flagd ignores a file whose timestamp moved
   backwards, which happens when a seed copy preserves the source timestamp.
4. Poll OFREP at `:8016` (`POST /ofrep/v1/evaluate/flags/{key}`) until the
   evaluation reflects the change; on timeout, restart flagd and re-check.
5. Serialize all writes behind one async lock; store the pre-image for revert.
6. Runtime file is `runtime/flagd/demo.flagd.json`, mounted as a directory by
   `lab.override.yaml`; the repo copy stays pristine.

Verified on this host: hot reload works over the Docker Desktop bind mount, so
fault injection does not require restarting flagd (which would cause a
self-inflicted flag outage).

---

## 9. Remediation action catalog v1

| Action ID | Target | Risk | Approval | Verification criteria |
|---|---|---|---|---|
| `reset_fault` | fault | low | auto-eligible | error rate and p95 return to baseline within 90 s |
| `clear_feature_flag` | flag | low | auto-eligible | same, flag reads back `off` |
| `restart_service` | service | medium | **required** | container healthy, error rate < max(2%, 1.5× baseline) for 90 s |
| `restore_network` | service | medium | **required** | reachability restored, downstream error rate drops |
| `remove_resource_limit` | service | medium | **required** | CPU/mem back under baseline, no restart loop |
| `stop_load_generator` | load-generator | low | auto-eligible | request rate falls, error rate normalizes |
| `escalate_human` | incident | — | — | terminates the loop |

`approval=auto` actions still write an audit row. `AGENT_AUTO_REMEDIATE=false`
in `.env` keeps even low-risk actions manual until you flip it.

**Approval token flow**

```
planner → approval row (PENDING, token_hash, expires_at = now+10m)
        → UI shows the card (no token in DOM)
        → POST /api/approvals/{id}/approve  (server issues single-use token)
        → executor verifies token hash + expiry + action preconditions
        → action row → verification
```

Reject → `REJECTED` + escalate. Expired → 409, no action.

---

## 10. Detection and evidence

**Detector** (deterministic, no LLM)

```
error_rate(s, w) = Σ rate(span_calls{service=s, status=ERROR}[w])
                 / Σ rate(span_calls{service=s}[w])

ALERT if  error_rate > max(0.02, 3 × baseline_error_rate)
     and  last 3 consecutive 15 s samples breach
     and  no open incident for (service, signature) within 5 min cooldown

latency: p95 > max(2 × baseline_p95, 500 ms), same debounce
```

- Baselines: rolling 15-min median, recomputed every 60 s, persisted in
  `baselines`. Cold start falls back to the last healthy 15 min, or the first
  5 min after bring-up are "warm-up" and suppressed.
- Thresholds live in `settings`, editable from the Models/Settings screen.
- One incident per (service, signature); new signals attach as events, they do
  not open a second incident.
- **Phase 4 must confirm exact metric and label names** by querying
  `GET /api/v1/label/__name__/values` on Prometheus before hardcoding anything.
  Plan assumes `traces_span_metrics_calls_total{service_name,status_code}` and
  `traces_span_metrics_duration_milliseconds_bucket`.

**Evidence engine** — the largest deterministic component

| Analysis | Produces |
|---|---|
| Metric anomaly scan | per-service z-score vs baseline for error rate, p95, throughput, CPU, memory, restarts |
| Error origin | per-service "fails locally vs propagated" from span status + parent/child relation |
| Onset ordering | first failing span per service, ranked by time (earliest wins) |
| Trace sample | top 3 error traces + one healthy control, with failing span path |
| Log clustering | normalize (strip IDs/numbers) → template counts → *new* templates vs baseline |
| Container state | health, restarts, OOM kills, CPU/mem limits, network attachment |
| Flag state | enabled flags and targets during the window |
| Topology | service edges from span parent-child, with error rate per edge |
| Suspect scoring | weighted sum → `[{service, score, signals}]`, weights in config |

Starter weights: error origin 0.35, earliest onset 0.25, local error ratio 0.20,
latency anomaly 0.10, container unhealthy 0.10. Scores are normalized to sum to
1 and always carry the contributing signals — the UI must show *why*, not just a
number.

Every observable becomes an evidence row with a stable `E####` code, scoped to
the incident. Codes are handed to the agent; the agent may cite only those.

---

## 11. Agent contract

**Loop**

```
compact incident context (trigger, suspects, topology, new log templates,
                            flag + container state)
  → retrieve top-3 similar resolved incidents (hypothesis boost only)
  → LLM (tool loop, budget 20 calls, 90 s wall clock)
  → structured diagnosis (JSON schema, Pydantic-validated)
  → evidence validator
  → confidence gate (0.70)
  → DIAGNOSED | ESCALATED
```

**Tools exposed to the model** (all read-only, all argument-validated)

`get_incident_summary`, `get_metrics(service, metric, window)`,
`get_traces(service, errors_only, min_duration_ms, limit)`,
`get_trace(trace_id)`, `get_logs(service, level, query, limit)`,
`get_log_templates(service)`, `get_container_health(service)`,
`get_topology`, `get_active_flags`, `retrieve_similar_incidents(query, k)`,
`get_service_baseline(service)`.

No tool mutates anything. Remediation is chosen from the action catalog after
diagnosis, in code, not by the model.

**Diagnosis schema**

```json
{
  "root_cause": {"service": "payment", "fault_class": "application_error"},
  "confidence": 0.91,
  "summary": "…",
  "evidence_codes": ["E0021", "E0025", "E0041"],
  "affected_services": ["checkout", "frontend"],
  "recommended_action": {"action_id": "reset_fault", "target": "payment"},
  "alternative_hypotheses": [{"service": "checkout", "confidence": 0.06}]
}
```

`fault_class ∈ {application_error, resource_exhaustion, dependency_unavailable,
network, traffic, configuration, unknown}`.

**Validation rules (hard rejects, one repair attempt, then escalate)**

1. Every cited `E####` exists **in this incident** and was shown to the model.
2. `root_cause.service` exists in the topology.
3. `confidence ∈ [0,1]` and `≥ AGENT_CONFIDENCE_THRESHOLD` to proceed.
4. `recommended_action` is in the catalog **and** allows that target.
5. Summary ≤ 1,200 chars; no markdown tables; no invented metric values
   (numbers in the summary must trace to cited evidence or be dropped).
6. `affected_services` ⊆ known services.

Failures are stored on the run, surfaced in the UI, and never silently dropped.

---

## 12. Memory

Store **only after resolution**, from a normalized template — never raw logs.

```
<service>. <fault class>. <symptom phrase>.
Onset: <service> errors preceded <downstream> by <x>s.
Container: <healthy|crashed|oom|paused>. Restart: <helped|no effect>.
Action: <action_id> on <target>. Verification: <verdict>.
```

- Embedding: Qwen3-Embedding-0.6B (1024-d) over the summary only.
- Retrieval: cosine top-k (k=5, threshold 0.55) + filter by fault class when
  confident; rerank by recency.
- **Guardrail:** retrieved memories are injected as *hypotheses to check*, and
  the validator treats them as non-evidence. The prompt says so, and the
  diagnosis schema has no field for memory citations.
- Re-embedding job (`scripts/reembed.py`) for model/dimension changes; the
  dimension is stored in `settings` and checked at startup.

---

## 13. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + dependency status (prom/jaeger/os/docker/db/llm) |
| GET | `/api/overview` | KPI bundle for the home screen |
| GET | `/api/faults/catalog` | schema-driven fault form |
| GET/POST | `/api/faults` | list / inject |
| DELETE | `/api/faults/{id}` | revert |
| POST | `/api/faults/reset-all` | panic reset |
| GET | `/api/telemetry/metrics` | whitelisted query passthrough |
| GET | `/api/telemetry/traces` | search |
| GET | `/api/telemetry/traces/{id}` | waterfall |
| GET | `/api/telemetry/logs` | search |
| GET | `/api/telemetry/services` | inventory + health |
| GET | `/api/incidents` | list/filter |
| GET | `/api/incidents/{id}` | full aggregate |
| POST | `/api/incidents/{id}/investigate` | start/retry agent |
| POST | `/api/incidents/{id}/close` | manual close |
| GET | `/api/incidents/{id}/evidence` | evidence list |
| GET | `/api/incidents/{id}/report` | json / `?format=md` |
| GET | `/api/memory/search` | debug retrieval |
| GET/POST | `/api/incidents/{id}/approvals` | request/list |
| POST | `/api/approvals/{id}/approve\|reject\|dry-run` | decide |
| GET/PATCH | `/api/settings/models` | provider/model/dim/thresholds (keys masked) |
| POST | `/api/settings/models/test` | provider smoke test |
| GET | `/api/events` | SSE: `fault.*`, `incident.*`, `agent.*`, `approval.*`, `action.*`, `verification.*` |

SSE is the default (one-way, proxy-friendly, replayable via `Last-Event-ID`);
WebSocket is only added if the approval card needs client→server streaming.

API binds `127.0.0.1` by default. No auth in v1; documented as a deliberate
local-only trade-off.

---

## 14. Milestones

Each milestone is a demo you can run end to end. Nothing advances on "the code
compiles".

### M1 — Fault → observe → recover (P0–P7)

> From the dashboard I inject a fault into Astronomy Shop, watch it appear in
> metrics/traces/logs, reset it, and see the system recover.

Proves: environment, dashboard, API, fault controller, all four telemetry
adapters, real-time events. **No LLM involved.**

Gate: 3 different faults (payment, cart, container stop) each produce a visible
symptom within 60 s and full recovery within 120 s of reset.

### M2 — Deterministic suspects (P8–P9)

> Inject a fault and have plain Python name the right service with a visible
> reason.

Gate: on 8 scripted scenarios, top-1 suspect is the injected target in ≥ 7,
and every score has machine-readable signals.

### M3 — Agent diagnosis (P10–P13)

> The agent investigates live, cites real evidence, retrieves a similar past
> incident, and produces a validated diagnosis with confidence.

Gate: 10 runs, 0 invalid evidence codes, 0 unvalidated actions, p95 run < 90 s,
median cost < $0.01.

### M4 — Approval → execute → verify → report (P14–P16)

> The full loop, human in the middle, with a verified outcome and a report.

Gate: verified recovery on 3 scenarios, rejection path works, a deliberately
wrong action fails verification and escalates, report contains timeline +
evidence + before/after.

---

## 15. Phase-by-phase work order

Estimates in dev-days; dependencies are strict.

| Phase | Work | DoD | ~d |
|---|---|---|---|
| **P0** Repo & tooling | Monorepo skeleton, `.env.example`, `pyproject` (ruff/mypy/pytest), pnpm workspace, `Makefile`/ps1 scripts, ADR 001 (stack), bootstrap script pinning the demo, `.gitignore` for demo checkout + runtime dir | Fresh clone → `scripts/up.ps1` reaches a healthy demo; lint+types+test run in one command | 1 |
| **P1** Infra bring-up | `compose/base.yaml` + `lab.override.yaml`: project name, ports (§6), Jaeger gRPC remap, flagd runtime mount, resource caps, pgvector service, init.sql | One command brings up demo + observability + DB; healthcheck script verifies 9 endpoints; Grafana excluded | 1 |
| **P2** API skeleton + DB | App factory, settings, structured logging, error envelope, routers stub, SQLAlchemy async + Alembic baseline, all tables from §7, `/api/health` with dependency probes | `/docs` browsable; migrations apply on empty DB; health shows per-dependency status | 1.5 |
| **P3** Dashboard shell | Next App Router, Tailwind + shadcn, 10-section nav, typed client generated from OpenAPI, SSE hook, status header, empty states | Nav works; live health + demo reachability; no placeholder lorem | 1.5 |
| **P4** Telemetry adapters | Prometheus (query/range, allowlist, step handling), Jaeger (search/trace, `/jaeger/ui` base path), OpenSearch (`otel-logs*`, query DSL), Docker (ps/inspect/stats/events), shared cache + timeout + retry + typed DTOs, fixture tests | Adapter contract tests against the live stack; each has recorded fixtures; Prometheus metric names confirmed and documented | 3 |
| **P5** Overview + Telemetry UI | KPI cards, error/latency/throughput charts, service health table, trace waterfall, log table with trace-id link, service inventory | Manual flag flip appears in all three panes within 60 s of the service observing it (dashboard poll ≤ 15 s; the demo's flagd SDK propagation adds 30–45 s — see ARCHITECTURE). Trace→log and log→trace navigation works | 3 |
| **P6** Fault controller | Catalog + Pydantic validation + UI schema, safety guard, flagd writer, container/network/limits adapters, TTL watchdog, audit, idempotent revert, fault API + events | All v1 faults inject + revert; non-lab target rejected with 403; TTL auto-revert proven; API kill mid-fault does not strand the lab | 3 |
| **P7** Fault Lab UI | Dynamic forms from catalog, active-fault cards with countdown, reset/reset-all, dry-run, history | Inject payment failure from UI → charts break → reset → recovery; zero CLI use | 1.5 |
| **P8** Incident engine | State machine, transitions, dedupe/cooldown, baselines, detector loop, incident list/detail API, SSE replay | Injected fault opens exactly one incident in ≤ 90 s; recovery closes it; duplicate alerts attach as events; replay from `Last-Event-ID` works | 3 |
| **P9** Deterministic analysis | Metric anomaly + onset + error-origin + log templating + container/flag signals + topology + suspect scoring + evidence store | 8-scenario script scores ≥ 7/8 top-1; scoring unit tests pin the weights; evidence codes stable and unique | 4 |
| **P10** Model gateway + embeddings | `LLMProvider` ABC + OpenAI-compatible/ZenMux adapter (tool calling, JSON mode, retries, timeouts, token/cost accounting), `EmbeddingProvider` ABC + ONNX local + stub + remote, `/api/settings/models`, connection test | Provider swap is config-only; embeddings return 1024-d vectors on CPU; test endpoint reports latency and model ID; stub provider lets tests run offline | 2.5 |
| **P11** Agent orchestrator | Tool registry + JSON schemas, budget enforcement, context assembler, prompts, diagnosis schema, validator, confidence gate, run/step persistence, SSE step events | 8 scenarios end to end; 0 invalid evidence; tool calls ≤ 20; validator rejects seeded bad outputs | 4 |
| **P12** Memory + retrieval | Summarizer, embed-on-resolve, HNSW index, hybrid retrieval, hypothesis-only injection, reembed script, seed data | After 3 resolved scenarios the matching incident is in top-3; memory cannot appear as evidence; dimension guard at startup | 2 |
| **P13** Agent UI | Live timeline, suspect board, tool inspector, evidence viewer, diagnosis card with citations + confidence, rerun/cancel | A full investigation is watchable in real time; every claim links to an evidence row | 2 |
| **P14** Remediation + approval | Action catalog, planner, server-issued single-use tokens, dry-run, executor with preconditions, rollback, audit | Approve/reject/expire all correct; unsafe action rejected; every execution has an audit trail | 3 |
| **P15** Verification + reports | Snapshot/observe/compare, per-action criteria, verdicts, escalation, JSON+MD report, incident report page + export | Verified recovery on 3 scenarios; failed action escalates and rolls back where safe; report is self-contained | 2.5 |
| **P16** Hardening + eval | Scenario runner + matrix report, cost/latency tracking, rate limits, secret masking audit, docs (ARCHITECTURE/RUNBOOK), demo script | One command runs the scenario suite and prints a scoreboard; runbook reproduces M1–M4 from scratch | 2 |

**Critical path:** P1 → P4 → P6 → P8 → P9 → P11 → P14 → P15.
P3/P5/P7/P13 can be built in parallel with their backend phase.
Total ≈ **40 dev-days**.

---

## 16. Resource budget

| Item | Est. | Note |
|---|---|---|
| Demo images (already pulled) | 7.2 GB | fixed |
| Observability images to pull | ~2.9 GB | jaeger, prometheus, opensearch, grafana (kept: upstream dependency) |
| pgvector image | ~0.5 GB | |
| Qwen3-Embedding-0.6B int8 | ~0.7 GB | ONNX, CPU |
| node_modules | ~1.0 GB | pnpm store shared |
| Python venv (no torch) | ~0.5 GB | onnxruntime only |
| **Total** | **~12.4 GB** vs 21 GB free | ~8 GB headroom |

Rules: no torch; Grafana kept only because `frontend-proxy` depends on it;
prune only dangling images (never `-a` before a demo upgrade); keep Jaeger
retention at 25k traces; rotate `runtime/` logs weekly.

---

## 17. Test and evaluation plan

**Unit (fast, no infra)** — fault catalog validation, safety guard, detector
windows and cooldown, baseline math, suspect scoring, evidence code generation,
diagnosis validator (valid/invalid/seeded-hallucination), approval token
expiry/single-use, verification criteria, report rendering.

**Contract (live stack, opt-in marker)** — each adapter against its real
dependency, with fixtures recorded for offline runs. One test asserts the
Prometheus metric names still exist, so a demo upgrade fails loudly.

**Scenario (the real evaluation)** — `scripts/run-scenarios.py`, each scenario:
reset → warm-up → inject → wait for incident → run agent → approve → verify →
reset. Records pass/fail, top-1 accuracy, evidence validity, latency, cost.

| # | Fault | Expected top-1 | Expected action |
|---|---|---|---|
| S1 | payment_failure | payment | reset_fault |
| S2 | cart_failure | cart | reset_fault |
| S3 | catalog_lock_contention | product-catalog | reset_fault |
| S4 | recommendation_cache_failure | recommendation | reset_fault |
| S5 | service_down (email) | email | restart_service |
| S6 | cpu_pressure (ad) | ad | remove_resource_limit |
| S7 | network_partition (payment) | payment | restore_network |
| S8 | traffic_flood | frontend-proxy | stop_load_generator |
| S9 | email_memory_leak | email | reset_fault |
| S10 | ad_failure | ad | reset_fault |

Do not claim the lab "works" on fewer than 10 clean runs.

---

## 18. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Demo + stack memory pressure on Docker Desktop | OOM, flapping services | Compose resource caps, keep Grafana lean, 8 GB+ Docker memory, 5 load-generator users |
| Windows/Docker networking quirks (`tc`, netns) | delay/loss faults fail | v1 avoids `netem`; partition via `docker network disconnect`; netem deferred |
| Port collisions (4317, 8000, 3000, 5432) | `up` fails | Explicit port map in §6 + a preflight script that checks the list |
| Jaeger in-memory retention (25k traces) | evidence disappears | Query within 10 min; raise `MEMORY_MAX_TRACES`; snapshot evidence into Postgres |
| Prometheus 60 s scrape vs 15 s detection | stale signal | Rely on OTLP push; verify actual freshness in P4; document the true step |
| flagd file-write races | half-applied faults | Single async lock, atomic replace, OFREP read-back, pre-image for revert |
| LLM hallucinated evidence | credibility loss | Evidence-ID allowlist + validator + repair-once + escalate |
| Memory overfitted answers | wrong root cause | Hypothesis-only injection, no memory field in schema, recency+similarity thresholds |
| Model/provider deprecation | rework | Provider ABC + config, one adapter per provider, contract test |
| Cost drift | surprise bill | Max 20 tool calls, context caps, per-run token accounting, hard daily budget guard |
| Contributor data terms | privacy | Lab data only; non-contributor model is an env change |
| Secrets leaking to browser | key exposure | Keys only in env; settings API returns masked `sk-…abcd`; audit responses in P16 |

---

## 19. First week (P0–P1 checklist)

1. `docs/ADR/001-stack.md` — record the Next/FastAPI/Postgres decisions and the
   seven corrections in §3.
2. `.gitignore`: `opentelemetry-demo/`, `runtime/`, `.env`, `*.egg-info`,
   `.venv`, `node_modules`.
3. `scripts/bootstrap-demo.ps1` — clone `open-telemetry/opentelemetry-demo` at a
   pinned ref into `infra/otel-demo/`.
4. `apps/api/pyproject.toml` with Python 3.11 + uv; `uv sync` works.
5. FastAPI `main.py` + `/api/health` returning a static payload.
6. `infra/compose/base.yaml`: project `docock-lab`, network `docock-lab`,
   pgvector on 5433.
7. `lab.override.yaml`: port fixes, `JAEGER_GRPC_PORT=14317`, flagd runtime
   mount, resource caps.
8. `scripts/up.ps1` / `down.ps1` / `reset.ps1` + preflight port check.
9. Bring the stack up, confirm Prometheus/Jaeger/OpenSearch have data after
   5 minutes, and screenshot the service list.
10. Record actual metric names and labels from Prometheus into
    `docs/ARCHITECTURE.md` — do not guess them in P4.

**First milestone gate:** dashboard says "HEALTHY" with real data from all four
sources, and the panic reset button works.

---

## 20. Open decisions

1. **Demo checkout**: gitignored + bootstrap script (recommended), or a git
   submodule?
2. **Start now?** P0–P1 are safe, reversible, and unblock everything.
3. **Model access**: is a ZenMux key available, or should P10 target a local
   OpenAI-compatible server first (Ollama/vLLM) and ZenMux second?
4. **Scope trim**: are all nine dashboard sections required for v1, or is
   Overview / Fault Lab / Telemetry / Incidents / Agent / Approvals / Reports
   enough (deferring Memory debug and Models settings to admin routes)?
5. **Load profile**: keep the demo's default load, or reduce it so the stack
   fits comfortably in Docker's memory?

Defaults if unanswered: gitignored + script, ZenMux primary with local fallback,
all sections kept but Memory/Settings minimal, demo default load.
