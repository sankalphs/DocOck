# Demo script — fault to verified recovery

A 15–20 minute walkthrough of the whole loop on the local stack: inject a fault,
watch it get detected and explained, approve the fix, see it verified, read the
report. Then show the two unhappy paths: a rejected action and a wrong action
that fails verification. Timings are the ones measured in `docs/PROGRESS.md`.

## Before the audience arrives (10 min)

```powershell
scripts\up.ps1 -SkipPull                                  # demo + observability + Postgres
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\api-detached.ps1   # API on :8001
cd apps\dashboard; pnpm dev                               # dashboard on :3001
scripts\health.ps1                                        # every row OK
```

- Wait at least 5 minutes after starting the API: the detector learns its
  baselines during a 300 s warm-up and opens no incident before that.
- Open <http://127.0.0.1:3001> and press **Open the dashboard** (or go straight to
  <http://127.0.0.1:3001/dashboard>). The Overview should show all services green, no
  active fault, no open incident. If an old fault is active, press
  **Reset all** in the Fault Lab.
- The default model provider is the offline `stub`; nothing leaves the machine.
  A real provider is configured under **Models** (the key stays in `.env`).

## 1. A healthy system (1 min) — Overview, Telemetry

- **Overview**: error rate, p95 latency and throughput per service, container
  health, live event stream.
- **Telemetry**: the same signals from Prometheus, Jaeger and OpenSearch, linked
  trace ↔ logs.

## 2. Break it (1 min) — Fault Lab

- **Fault Lab → Payment failure → 100% → Inject** (keep the default TTL). Show
  **Dry run** first: the exact flag change, the allowlisted target and the
  automatic revert.
- Say: every fault has a TTL and a watchdog; it reverts even if nobody does.

## 3. Detection (≈ 1–2 min) — Incidents

- Payment errors reach the Overview in 30–45 s (flagd propagation).
- The detector opens one incident within ~2 minutes (S1 measured 126 s), with
  the breached signals and the correlated services (payment, checkout, frontend).

## 4. Diagnosis (≈ 30 s) — Incidents → Analyze, Agent

- **Analyze**: deterministic suspects with scores and evidence codes (`E0001` …).
  payment ranks first.
- **Agent → Investigate**: live timeline of tool calls, suspect board, cited
  diagnosis. Click a citation to open the evidence. Say: the agent may cite only
  evidence codes that exist for this incident; anything else is rejected.
- **Memory** shows similar past incidents and what fixed them.

## 5. Approval (1 min) — Approvals

- The diagnosis became a **pending approval**: action `reset_fault` on
  `payment`, risk, preconditions, steps, rollback, the cited evidence.
- **Dry-run**, then **Approve**. Say: approval issues a single-use, short-lived
  token bound to this action; only its hash is stored, and the executor refuses
  a replayed token.

## 6. Verification (≈ 1–2 min) — Incidents, Reports

- The incident moves `REMEDIATING → VERIFYING`. The verifier needs 4 clean
  detector cycles (S1: PASSED after 70 s; S5: 44 s), checks the fault row is
  reverted, the flag is off, the container is healthy and the error rate is back
  under its ceiling. Then `RESOLVED`.
- **Reports → the incident**: summary, timeline, cited evidence, the action with
  its criteria table, **before → after** metrics (payment error rate 100% → 0%),
  audit trail. **Download Markdown** gives the same report as a file.

## 7. Rejection (1 min)

- Inject **Email service down** (`service_down` on `email`), let the incident
  and proposal appear, then **Reject** with a note. The incident goes
  `AWAITING_APPROVAL → REJECTED → ESCALATED`; nothing executes. Reset the fault.

## 8. A wrong action fails verification (≈ 8 min, start it early)

- Inject **Payment failure** again and wait for the pending approval. Replace
  the proposal with `restart_service` on `payment` (the operator override; the
  dashboard has no form for it), then **Approve** the new card in **Approvals**:

  ```powershell
  curl.exe -X POST -H "content-type: application/json" -d '{\"action_id\":\"restart_service\",\"target\":\"payment\",\"replace\":true,\"actor\":\"demo\"}' http://127.0.0.1:8001/api/incidents/INC-00100/approvals
  ```

- Restarting payment does not clear a feature-flag fault, so the error rate stays
  high. After the 420 s window the verdict is `FAILED`, the incident is
  `ESCALATED`, and the report shows the failing criteria (`symptoms_clear`,
  `faults_reverted`, `flag_off`). The fault TTL then cleans up.
- The scripted version is scenario W1:
  `uv run --project apps\api python -u scripts\run-scenarios.py --only W1 --remediate`.

## 9. Cost, latency and guardrails (1 min) — Settings

- **Settings**: agent runs, cost against the daily cap, investigation latency
  p50/p95, incident close time, verification verdicts, rate-limit rules and how
  many requests they rejected, and the guardrails that are not editable from the
  browser.

## After the demo

- **Fault Lab → Reset all**; confirm **Incidents** has no open incident.
- The full evaluation (all scenarios, scoreboard, secret audit) is one command:
  `scripts\eval.ps1` (~2 h; see `docs/RUNBOOK.md`).
