"""Autopilot: run the whole incident loop without a human.

    detector opens incident ─► (settle delay) ─► agent investigation
      ─► DIAGNOSED (or gate-failed fallback) ─► ladder + LLM planner choose a fix
      ─► plan (catalog + safety + preconditions) ─► auto-approve within risk policy
      ─► executor (single-use token) ─► verifier
           PASSED ─► RESOLVED + report
           FAILED ─► next rung of the ladder (up to AUTOPILOT_MAX_ATTEMPTS)
                     ─► ESCALATED + report when the ladder is exhausted

What stays deterministic: the candidate ladder, every precondition, the
approval token, the executor, the verifier. What the LLM adds: the diagnosis
(through the agent) and the choice/explanation of the next rung. The autopilot
only *drives* the existing services; it adds no new way to mutate the lab.

It is also crash-safe: a sweep re-derives what to do from incident state, so an
API restart mid-loop resumes instead of leaving an incident half-remediated.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.autopilot.ladder import Candidate, LadderInput, build
from app.autopilot.planner import RemediationPlanner
from app.autopilot.planner import planner as default_planner
from app.config import settings
from app.db.models import Action, AgentRun, Diagnosis, Incident, Verification
from app.db.session import SessionFactory
from app.domain.state import IncidentStatus
from app.errors import ApiError
from app.events.bus import bus
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.remediation.catalog import risk_at_most
from app.remediation.drift import DriftScanner
from app.remediation.drift import scanner as default_scanner
from app.remediation.planner import open_faults
from app.remediation.schemas import ActionStatus, PlanRequest
from app.verification.criteria import focus_services

log = get_logger(__name__)

# Action outcomes that count as a finished attempt.
FINISHED = frozenset(
    {
        ActionStatus.EXECUTED.value,
        ActionStatus.FAILED.value,
        ActionStatus.ROLLED_BACK.value,
        ActionStatus.REJECTED.value,
        ActionStatus.EXPIRED.value,
    }
)
DRIVEN = frozenset(
    {
        IncidentStatus.DIAGNOSED.value,
        IncidentStatus.REMEDIATING.value,
        IncidentStatus.VERIFYING.value,
    }
)


class Autopilot:
    def __init__(
        self,
        *,
        planner: RemediationPlanner | None = None,
        drift: DriftScanner | None = None,
    ) -> None:
        self.planner = planner or default_planner
        self.scanner = drift or default_scanner
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loops: list[asyncio.Task[None]] = []
        self._queue: asyncio.Queue[Any] | None = None
        self.last_sweep: datetime | None = None
        self.decisions: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return settings.autopilot_enabled

    # --- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        if not self.enabled:
            log.info("autopilot_disabled")
            return
        self._queue = bus.subscribe()
        self._loops = [
            asyncio.create_task(self._events(), name="autopilot-events"),
            asyncio.create_task(self._sweep_loop(), name="autopilot-sweep"),
            asyncio.create_task(self._learn_loop(), name="autopilot-learn"),
        ]
        log.info(
            "autopilot_started",
            max_attempts=settings.autopilot_max_attempts,
            max_risk=settings.autopilot_max_risk,
            llm_planner=self.planner.uses_llm,
        )

    async def stop(self) -> None:
        for task in [*self._loops, *self._tasks]:
            task.cancel()
        for task in [*self._loops, *self._tasks]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._loops, self._tasks = [], set()
        if self._queue is not None:
            bus.unsubscribe(self._queue)
            self._queue = None

    def _spawn(self, coro: Any, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _lock(self, ref: str) -> asyncio.Lock:
        return self._locks.setdefault(ref, asyncio.Lock())

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": any(not task.done() for task in self._loops),
            "max_attempts": settings.autopilot_max_attempts,
            "max_risk": settings.autopilot_max_risk,
            "investigate_delay_s": settings.autopilot_investigate_delay_s,
            "llm_planner": self.planner.uses_llm,
            "last_sweep": self.last_sweep.isoformat() if self.last_sweep else None,
            "recent_decisions": self.decisions[-20:],
        }

    # --- event + sweep drivers --------------------------------------------------
    async def _events(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            if event.type == "incident.opened":
                ref = str(event.data.get("public_id") or "")
                if ref:
                    self._spawn(self._investigate_later(ref), f"autopilot-investigate-{ref}")

    async def _investigate_later(self, ref: str) -> None:
        await asyncio.sleep(settings.autopilot_investigate_delay_s)
        await self.investigate(ref)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.autopilot_sweep_interval_s)
            try:
                await self.sweep()
            except Exception as exc:
                log.warning("autopilot_sweep_failed", error=str(exc))

    async def _learn_loop(self) -> None:
        """Keep the desired state fresh while the lab is healthy."""
        while True:
            try:
                async with SessionFactory() as session:
                    busy = await incident_service.latest_open(session)
                if busy is None:
                    await self.scanner.scan(learn=True)
            except Exception as exc:
                log.warning("desired_state_learn_failed", error=str(exc))
            await asyncio.sleep(settings.autopilot_learn_interval_s)

    async def sweep(self) -> None:
        """Re-derive the next step for every active incident from its state."""
        self.last_sweep = datetime.now(UTC)
        async with SessionFactory() as session:
            incidents = list(
                (
                    await session.execute(
                        select(Incident).where(
                            Incident.status.in_([IncidentStatus.OPEN.value, *sorted(DRIVEN)])
                        )
                    )
                )
                .scalars()
                .all()
            )
            todo: list[tuple[str, str]] = []
            now = datetime.now(UTC)
            for incident in incidents:
                opened = incident.opened_at
                opened = opened if opened.tzinfo else opened.replace(tzinfo=UTC)
                if incident.status == IncidentStatus.OPEN.value:
                    if (now - opened).total_seconds() < settings.autopilot_investigate_delay_s:
                        continue
                    running = await session.scalar(
                        select(AgentRun.id).where(
                            AgentRun.incident_id == incident.id, AgentRun.status == "RUNNING"
                        )
                    )
                    if running is None:
                        todo.append(("investigate", incident.public_id))
                    continue
                if await self._needs_step(session, incident):
                    todo.append(("advance", incident.public_id))
        for kind, ref in todo:
            if self._lock(ref).locked():
                continue
            if kind == "investigate":
                self._spawn(self.investigate(ref), f"autopilot-investigate-{ref}")
            elif kind == "advance":
                self._spawn(self.advance(ref), f"autopilot-advance-{ref}")

    async def _needs_step(self, session: Any, incident: Incident) -> bool:
        latest = await session.scalar(
            select(Action).where(Action.incident_id == incident.id).order_by(Action.id.desc())
        )
        if incident.status == IncidentStatus.DIAGNOSED.value:
            return True
        if latest is None:
            return incident.status != IncidentStatus.DIAGNOSED.value
        if incident.status == IncidentStatus.REMEDIATING.value:
            return latest.status in (ActionStatus.FAILED.value, ActionStatus.ROLLED_BACK.value)
        if incident.status == IncidentStatus.VERIFYING.value:
            verdict = await session.scalar(
                select(Verification.verdict).where(Verification.action_row_id == latest.id)
            )
            return verdict is not None and verdict != "PASSED"
        return False

    # --- steps --------------------------------------------------------------------
    async def investigate(self, ref: str) -> None:
        from app.agent.orchestrator import orchestrator

        async with self._lock(ref):
            async with SessionFactory() as session:
                incident = await incident_service.get(session, ref)
                if incident.status != IncidentStatus.OPEN.value:
                    return
            try:
                await orchestrator.start(ref, actor="autopilot")
                self._decide(ref, "investigate", "agent investigation started")
                return
            except ApiError as exc:
                if exc.code not in ("llm_budget_exhausted", "llm_unconfigured"):
                    log.info("autopilot_investigate_skipped", incident=ref, reason=exc.message)
                    return
                reason = exc.message
            # No model available: continue on deterministic evidence alone (drift,
            # recorded faults, the suspect ranking) instead of stalling.
            async with SessionFactory() as session:
                incident = await incident_service.get(session, ref)
                await session.refresh(incident, with_for_update=True)
                if incident.status != IncidentStatus.OPEN.value:
                    return
                for target in (IncidentStatus.INVESTIGATING, IncidentStatus.DIAGNOSED):
                    await incident_service.transition(
                        session,
                        incident,
                        target,
                        kind="autopilot",
                        payload={"mode": "deterministic", "reason": reason},
                        actor="autopilot",
                    )
                await session.commit()
            self._decide(ref, "investigate", f"deterministic only: {reason}")
        await self.advance(ref)

    async def on_diagnosed(self, ref: str) -> None:
        """Orchestrator hook (replaces approvals.auto_plan while enabled)."""
        self._spawn(self.advance(ref), f"autopilot-advance-{ref}")

    async def on_failed(self, ref: str) -> None:
        """Executor/verifier hook: an attempt failed; try the next rung."""
        self._spawn(self.advance(ref), f"autopilot-advance-{ref}")

    async def advance(self, ref: str) -> None:
        """Propose (and, within policy, approve) the next remediation, or escalate."""
        async with self._lock(ref):
            try:
                await self._advance(ref)
            except Exception as exc:  # never leave an incident stranded mid-loop
                log.exception("autopilot_advance_crashed", incident=ref)
                with contextlib.suppress(Exception):
                    await self.escalate(ref, f"autopilot error: {type(exc).__name__}: {exc}")

    async def _advance(self, ref: str) -> None:
        from app.remediation.approval import AutopilotPlan, approvals

        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            if incident.status not in DRIVEN:
                return
            if not await self._needs_step(session, incident):
                return
            diagnosis = await session.scalar(
                select(Diagnosis)
                .join(AgentRun, AgentRun.id == Diagnosis.run_id)
                .where(AgentRun.incident_id == incident.id, Diagnosis.valid.is_(True))
                .order_by(Diagnosis.id.desc())
            )
            actions = list(
                (
                    await session.execute(
                        select(Action).where(Action.incident_id == incident.id).order_by(Action.id)
                    )
                )
                .scalars()
                .all()
            )
            verdicts = {
                row.action_row_id: row.verdict
                for row in (
                    await session.execute(
                        select(Verification).where(
                            Verification.action_row_id.in_([a.id for a in actions] or [0])
                        )
                    )
                ).scalars()
            }
            faults = [(fault.target, fault.type) for fault in await open_faults(session)]
            trigger = [str(s) for s in ((incident.trigger or {}).get("services") or [])]
            primary = incident.primary_service
            status = incident.status

        attempts = [
            {
                "action_id": action.action_id,
                "target": action.target,
                "status": action.status,
                "verdict": verdicts.get(action.id),
                "error": (action.error or "")[:200] or None,
            }
            for action in actions
            if action.status in FINISHED or action.id in verdicts
        ]
        if len(attempts) >= settings.autopilot_max_attempts:
            await self.escalate(
                ref, f"autopilot tried {len(attempts)} remediation(s); none verified", attempts
            )
            return

        root = diagnosis.root_cause_service if diagnosis else None
        focus = focus_services(
            target=root or primary or "", primary=primary, trigger=trigger, root=root
        )
        if diagnosis is not None:
            focus = sorted({*focus, *(diagnosis.affected_services or [])})
        drift = await self.scanner.scan()
        threshold = settings.agent_confidence_threshold
        ladder = build(
            LadderInput(
                focus=focus,
                root=root,
                recommended=dict(diagnosis.recommended_action or {}) if diagnosis else None,
                passes_gate=bool(diagnosis and diagnosis.confidence >= threshold),
                confidence=diagnosis.confidence if diagnosis else None,
                drift=drift.items,
                faults=faults,
                attempted={(a.action_id, a.target) for a in actions},
            )
        )
        ladder = [c for c in ladder if risk_at_most(c.risk, settings.autopilot_max_risk)]
        choice = await self.planner.choose(
            ladder,
            incident={"id": ref, "status": status, "primary": primary, "trigger": trigger},
            diagnosis=(
                {
                    "root_cause": diagnosis.root_cause_service,
                    "fault_class": diagnosis.fault_class,
                    "confidence": diagnosis.confidence,
                    "summary": diagnosis.summary,
                    "recommended_action": diagnosis.recommended_action,
                }
                if diagnosis
                else None
            ),
            drift=drift.summary(),
            attempts=attempts,
        )
        if choice.index < 0 or not ladder:
            await self.escalate(
                ref,
                choice.rationale if ladder else "no remediation candidate is left",
                attempts,
            )
            return
        order = [ladder[choice.index]] + [c for i, c in enumerate(ladder) if i != choice.index]
        skipped: list[dict[str, Any]] = []
        for rank, candidate in enumerate(order):
            rationale = (
                choice.rationale if rank == 0 else f"{candidate.reason} ({candidate.source})"
            )
            try:
                result = await approvals.plan(
                    ref,
                    PlanRequest(
                        action_id=candidate.action_id,
                        target=candidate.target,
                        actor="autopilot",
                        note=rationale[:500],
                    ),
                    autopilot=AutopilotPlan(
                        source=candidate.source,
                        rationale=rationale,
                        planner=choice.by if rank == 0 else "deterministic",
                        attempt=len(attempts) + 1,
                        candidates=[c.as_dict() for c in ladder[:8]],
                        planner_error=choice.error if rank == 0 else None,
                        planner_cost_usd=choice.cost_usd if rank == 0 else 0.0,
                    ),
                )
            except ApiError as exc:
                skipped.append({**candidate.as_dict(), "refused": exc.message[:200]})
                continue
            self._decide(
                ref,
                "remediate",
                f"attempt {len(attempts) + 1}: {candidate.action_id} on {candidate.target} "
                f"({choice.by if rank == 0 else 'deterministic'}): {rationale[:200]}",
                skipped=skipped,
                auto_approved=result.auto_approved,
            )
            return
        await self.escalate(
            ref, "every remediation candidate was refused by its preconditions", attempts, skipped
        )

    async def escalate(
        self,
        ref: str,
        reason: str,
        attempts: list[dict[str, Any]] | None = None,
        skipped: list[dict[str, Any]] | None = None,
    ) -> None:
        from app.reports.service import reports

        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            await session.refresh(incident, with_for_update=True)
            if incident.status in (
                IncidentStatus.RESOLVED.value,
                IncidentStatus.ESCALATED.value,
            ):
                return
            if incident.status == IncidentStatus.AWAITING_APPROVAL.value:
                await incident_service.withdraw_pending(session, incident, reason="escalated")
            await incident_service.transition(
                session,
                incident,
                IncidentStatus.ESCALATED,
                kind="escalated",
                payload={
                    "reason": reason,
                    "by": "autopilot",
                    "attempts": attempts or [],
                    "skipped": skipped or [],
                },
                actor="autopilot",
            )
            await session.commit()
        self._decide(ref, "escalate", reason)
        await reports.generate(ref)

    def _decide(self, ref: str, kind: str, detail: str, **extra: Any) -> None:
        entry = {"at": datetime.now(UTC).isoformat(), "incident": ref, "kind": kind}
        entry.update({"detail": detail, **extra})
        self.decisions.append(entry)
        del self.decisions[:-100]
        bus.publish("autopilot.decision", entry)
        log.info("autopilot_decision", incident=ref, kind=kind, detail=detail[:200])


autopilot = Autopilot()

__all__ = ["Autopilot", "Candidate", "autopilot"]
