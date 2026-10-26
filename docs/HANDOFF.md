# Handoff protocol

The build runs in sequential threads. Each thread owns 2–4 phases, commits per
phase, and launches the next thread. No thread may skip ahead: later phases
assume the database, adapters, and detectors from earlier ones exist.

## Thread batches

| Thread | Phases | Milestone | Why this cut |
|---|---|---|---|
| 1 | P0–P1 | — | Repo + a running stack (done in this thread) |
| 2 | P2–P5 | partial M1 | API/DB, dashboard shell, real telemetry, first screens |
| 3 | P6–P9 | M1 + M2 | Faults end to end, incident engine, deterministic diagnosis |
| 4 | P10–P13 | M3 | Models, agent loop, memory, agent UI |
| 5 | P14–P16 | M4 | Approval, execution, verification, reports, evaluation |

Adjust only if a phase is blocked; do not reorder dependencies (see PLAN §15).

## Rules for every thread

1. **Read first.** `docs/PLAN.md` §5–§13, then `docs/PROGRESS.md`, then the
   ADRs. The plan is the spec; ADRs record deviations already agreed.
2. **One commit per phase** (sub-steps may add more commits, but a phase must
   end with a commit named `feat(pN): …` or `chore(pN): …`).
3. **Verify before claiming done.** Run `scripts\check.ps1` and the phase's DoD
   from PLAN §15. Live checks need `scripts\up.ps1` and must be run for real —
   no "should work" claims.
4. **Update `docs/PROGRESS.md`** in the same commit: status, commit SHA, and the
   evidence you actually observed.
5. **Update `docs/PLAN.md`** only if reality invalidates it. Put the reason in
   the same commit message.
6. **Never commit** `.env`, `runtime/`, `opentelemetry-demo/`, or model caches.
7. **Do not weaken safety** to make a test pass: allowlists, evidence-ID
   validation, and approval tokens are load-bearing.
8. **Blocked?** Commit what works, mark the phase `BLOCKED` in PROGRESS with the
   exact failing command and error, and report it instead of guessing.

## Definition of done (per phase)

- Code merged to `main` in this checkout (no worktrees).
- `scripts\check.ps1` passes; new tests cover the happy path and one failure path.
- The phase's DoD line in PLAN §15 is demonstrated, with the command or URL used.
- `docs/PROGRESS.md` updated.
- Next thread launched with a prompt that names the phases, the prior commit,
  and any deviations.

## Starting the next thread

Use `t3_thread_launch` with:

- `workspaceStrategy`: `{"type":"root"}` — the same checkout, no worktree.
- `message`: the next batch, the rules above, and the current `PROGRESS.md`
  summary. Ask it to finish by launching the following thread.

Do not run two batches concurrently: they touch the same database schema and
the same Compose stack.
