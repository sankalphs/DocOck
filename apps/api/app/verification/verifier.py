"""Verifier: did the executed action actually fix the incident? (P15)

The executor hands over every EXECUTED action (incident VERIFYING). The
verifier records the "before" picture (metrics at the moment the action
started, the planner's pre-snapshot), then follows the detector cycle by cycle
and evaluates the action's criteria (app/verification/criteria.py) on each
fresh report. Every criterion must hold on `verification_clean_cycles`
consecutive cycles within `verification_timeout_s`:

  PASSED  -> incident RESOLVED (memory is written from it)
  FAILED  -> safe rollback evaluated, incident ESCALATED

The detector is the same judge that opened the incident, so "fixed" means the
signal that raised the alarm is gone, not that a command returned 0.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db.models import Action, Diagnosis, Fault, Incident, Verification
from app.db.session import SessionFactory
from app.detection.runtime import runtime as detection
from app.domain.state import IncidentStatus
from app.events.bus import bus
from app.faults.catalog import ALLOWED_TARGETS, DEFINITIONS
from app.faults.controller import get_controller
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.remediation.audit import ActionAuditEntry, ActionAuditLog
from app.remediation.executor import executor
from app.remediation.planner import snapshot
from app.remediation.schemas import ActionStatus
from app.telemetry.docker import DockerError, docker_inspect
from app.telemetry.prometheus import PrometheusAdapter
from app.verification.criteria import Observation, all_ok, evaluate, focus_services

log = get_logger(__name__)

VerifiedHook = Callable[[str], Awaitable[None]]


class Verdict:
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


METRICS = ("error_rate", "p95_latency", "throughput")


class Verifier:
    def __init__(
        self,
        *,
        prometheus: PrometheusAdapter | None = None,
        audit: ActionAuditLog | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusAdapter(ttl_s=0)
        self.audit = audit or ActionAuditLog(settings.remediation_audit_file)
        self.on_verified: VerifiedHook | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}

    # --- lifecycle ------------------------------------------------------------
    async def hook(self, action_row_id: int) -> None:
        """Executor callback. Returns at once; verification runs for minutes."""
        self.launch(action_row_id)

    def launch(self, action_row_id: int) -> asyncio.Task[None]:
        task = self._tasks.get(action_row_id)
        if task is None or task.done():
            task = asyncio.create_task(
                self._guarded(action_row_id), name=f"verify-action-{action_row_id}"
            )
            self._tasks[action_row_id] = task
        return task

    async def wait(self, action_row_id: int, timeout_s: float) -> None:
        task = self._tasks.get(action_row_id)
        if task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout_s)

    def running(self) -> list[int]:
        return [row for row, task in self._tasks.items() if not task.done()]

    async def start(self) -> int:
        """Resume verifications cut short by an API restart.

        An incident left VERIFYING with an EXECUTED action and no verification
        row would otherwise stay open forever: the detector does not resolve
        VERIFYING incidents (the verifier owns that outcome).
        """
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    select(Action.id)
                    .join(Incident, Incident.id == Action.incident_id)
                    .outerjoin(Verification, Verification.action_row_id == Action.id)
                    .where(
                        Incident.status == IncidentStatus.VERIFYING.value,
                        Action.status == ActionStatus.EXECUTED.value,
                        Verification.id.is_(None),
                    )
                )
            ).scalars()
            pending = list(rows)
        for row in pending:
            self.launch(row)
        if pending:
            log.info("verification_resumed", actions=pending)
        return len(pending)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def _guarded(self, action_row_id: int) -> None:
        try:
            await self.verify(action_row_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave the incident silently VERIFYING
            log.error("verification_crashed", action_row=action_row_id, error=str(exc))
            with contextlib.suppress(Exception):
                await self._conclude(
                    action_row_id,
                    verdict=Verdict.INCONCLUSIVE,
                    before={},
                    after={"error": str(exc)[:300]},
                    criteria={"final": [], "error": str(exc)[:300]},
                    observed_for_s=0,
                )

    # --- the check -------------------------------------------------------------
    async def verify(self, action_row_id: int) -> None:
        async with SessionFactory() as session:
            action = await session.get(Action, action_row_id)
            if action is None or action.status != ActionStatus.EXECUTED.value:
                return
            incident = await session.get(Incident, action.incident_id)
            assert incident is not None
            diagnosis = (
                await session.get(Diagnosis, action.diagnosis_id) if action.diagnosis_id else None
            )
            ref = incident.public_id
            action_id, target = action.action_id, action.target
            started_at = action.started_at or action.created_at
            pre = dict(action.pre_snapshot or {})
            fault_ids = [
                int(step["fault_row_id"])
                for step in (action.result or {}).get("steps", [])
                if step.get("fault_row_id") is not None
            ]
            focus = focus_services(
                target=target,
                primary=incident.primary_service,
                trigger=[str(s) for s in ((incident.trigger or {}).get("services") or [])],
                root=diagnosis.root_cause_service if diagnosis else None,
            )
            await incident_service.append_event(
                session,
                incident,
                "verification_started",
                {
                    "action_row_id": action_row_id,
                    "action_id": action_id,
                    "target": target,
                    "focus": focus,
                    "clean_cycles": settings.verification_clean_cycles,
                    "timeout_s": settings.verification_timeout_s,
                },
            )
            await session.commit()
        bus.publish(
            "verification.started",
            {"incident_id": ref, "action_row_id": action_row_id, "focus": focus},
        )
        before = {
            "at": started_at.isoformat(),
            "metrics": await self._metrics(focus, at=started_at),
            "state": pre,
            "breaches": self._breaches(),
        }
        flags = self._flags_for(await self._fault_types(fault_ids))
        initial_start = await self._started(target)

        required = settings.verification_clean_cycles
        deadline = asyncio.get_running_loop().time() + settings.verification_timeout_s
        t0 = datetime.now(UTC)
        samples: list[dict[str, Any]] = []
        clean = 0
        last_seen: datetime | None = None
        criteria: list[Any] = []
        verdict = Verdict.FAILED
        reports = 0
        while asyncio.get_running_loop().time() < deadline:
            report = await self._next_report(last_seen, deadline)
            if report is None:
                samples.append({"at": datetime.now(UTC).isoformat(), "clean": 0, "stale": True})
                clean = 0
                continue
            last_seen = report.at
            reports += 1
            obs = await self._observe(report, target, fault_ids, flags, initial_start)
            criteria = evaluate(action_id, obs, focus)
            clean = clean + 1 if all_ok(criteria) else 0
            failing = [c.id for c in criteria if not c.ok]
            samples.append({"at": obs.at, "clean": clean, "failing": failing})
            bus.publish(
                "verification.progress",
                {
                    "incident_id": ref,
                    "action_row_id": action_row_id,
                    "clean": clean,
                    "required": required,
                    "failing": failing,
                },
            )
            if clean >= required:
                verdict = Verdict.PASSED
                break
        if verdict != Verdict.PASSED and reports == 0:
            verdict = Verdict.INCONCLUSIVE
        observed = int((datetime.now(UTC) - t0).total_seconds())
        post_state = await snapshot(target, await self._fault_dicts(fault_ids))
        after = {
            "at": datetime.now(UTC).isoformat(),
            "metrics": await self._metrics(focus, at=None),
            "state": post_state,
            "breaches": self._breaches(),
            "samples": samples,
        }
        await self._conclude(
            action_row_id,
            verdict=verdict,
            before=before,
            after=after,
            criteria={
                "final": [c.as_dict() for c in criteria],
                "focus": focus,
                "required_clean_cycles": required,
                "clean_cycles": clean,
                "timeout_s": settings.verification_timeout_s,
                "detector_reports": reports,
            },
            observed_for_s=observed,
        )

    async def _conclude(
        self,
        action_row_id: int,
        *,
        verdict: str,
        before: dict[str, Any],
        after: dict[str, Any],
        criteria: dict[str, Any],
        observed_for_s: int,
    ) -> None:
        rollback: dict[str, Any] | None = None
        async with SessionFactory() as session:
            action = await session.get(Action, action_row_id)
            assert action is not None
            pre = dict(action.pre_snapshot or {})
            action_id, target = action.action_id, action.target
        if verdict != Verdict.PASSED:
            # Same policy as a failed execution: only undo what cannot re-create
            # the fault (restart_service makes sure the container runs again).
            rollback = await executor.rollback(
                action_id, target, pre, reason=f"verification {verdict.lower()}"
            )
        failing = [c for c in criteria.get("final", []) if not c.get("ok")]
        async with SessionFactory() as session:
            action = await session.get(Action, action_row_id, with_for_update=True)
            assert action is not None
            incident = await session.get(Incident, action.incident_id)
            assert incident is not None
            row = Verification(
                action_row_id=action_row_id,
                verdict=verdict,
                before=before,
                after=after,
                criteria={**criteria, **({"rollback": rollback} if rollback else {})},
                observed_for_s=observed_for_s,
            )
            session.add(row)
            if rollback is not None:
                action.rollback = {**(action.rollback or {}), "after_verification": rollback}
                if rollback.get("performed"):
                    action.status = ActionStatus.ROLLED_BACK.value
            await session.flush()
            moved_to: str | None = None
            if incident.status == IncidentStatus.VERIFYING:
                target_status = (
                    IncidentStatus.RESOLVED
                    if verdict == Verdict.PASSED
                    else IncidentStatus.ESCALATED
                )
                await incident_service.transition(
                    session,
                    incident,
                    target_status,
                    kind="verification",
                    payload={
                        "verification_id": row.id,
                        "action_row_id": action_row_id,
                        "action_id": action_id,
                        "target": target,
                        "verdict": verdict,
                        "observed_for_s": observed_for_s,
                        "failing": [c["id"] for c in failing],
                        **({"rollback": rollback.get("detail")} if rollback else {}),
                    },
                    actor="verifier",
                )
                moved_to = target_status.value
            await session.commit()
            ref = incident.public_id
            verification_id = row.id
        detail = (
            f"all criteria held for {criteria.get('clean_cycles')} detector cycles"
            if verdict == Verdict.PASSED
            else "; ".join(f"{c['id']}: {c['detail']}" for c in failing) or verdict.lower()
        )
        await self.audit.append(
            ActionAuditEntry(
                actor="verifier",
                event="verified" if verdict == Verdict.PASSED else "verification_failed",
                incident=ref,
                approval_id=None,
                action_row_id=action_row_id,
                action_id=action_id,
                target=target,
                outcome=verdict,
                detail=detail[:500],
                before=None,
                after={"verification_id": verification_id, "rollback": rollback},
            )
        )
        bus.publish(
            "verification.completed",
            {
                "incident_id": ref,
                "action_row_id": action_row_id,
                "verification_id": verification_id,
                "verdict": verdict,
                "observed_for_s": observed_for_s,
                "incident_status": moved_to,
            },
        )
        log.info(
            "verification_finished",
            incident=ref,
            action_row=action_row_id,
            verdict=verdict,
            observed_s=observed_for_s,
        )
        if self.on_verified is not None:
            with contextlib.suppress(Exception):
                await self.on_verified(ref)

    # --- observation ------------------------------------------------------------
    async def _next_report(self, last_seen: datetime | None, deadline: float) -> Any:
        """The next detector report newer than `last_seen` (None if stale)."""
        loop = asyncio.get_running_loop()
        wait_until = min(deadline, loop.time() + 3 * settings.detector_interval_s)
        while loop.time() < wait_until:
            report = detection.detector.last_report
            if report is not None and (last_seen is None or report.at > last_seen):
                return report
            await asyncio.sleep(1.0)
        return None

    def _breaches(self) -> list[dict[str, Any]]:
        report = detection.detector.last_report
        if report is None:
            return []
        return [
            {"service": b.service, "signal": b.signal, "detail": b.detail, "severity": b.severity}
            for b in report.breaches
        ]

    async def _observe(
        self,
        report: Any,
        target: str,
        fault_ids: list[int],
        flags: list[str],
        initial_start: tuple[str | None, int] | None,
    ) -> Observation:
        shot = await snapshot(target, [])
        flag_values: dict[str, str | None] = {}
        if flags:
            variants = await asyncio.to_thread(get_controller().mechanisms.flagd.current_variants)
            flag_values = {flag: variants.get(flag) for flag in flags}
        error_rate = None
        try:
            result = await self.prometheus.metric(
                "error_rate", service=target, window=settings.detector_window
            )
            if result.series and result.series[0].points:
                error_rate = result.series[0].points[-1].v
        except Exception:
            error_rate = None
        return Observation(
            at=report.at.isoformat(),
            breaches=[
                {"service": b.service, "signal": b.signal, "detail": b.detail}
                for b in report.breaches
            ],
            container=shot.get("container"),
            started=await self._started(target),
            started_initial=initial_start,
            flags=flag_values,
            faults=await self._fault_dicts(fault_ids),
            error_rate=error_rate,
            baseline_error=(report.baselines.get(target) or {}).get("error_rate"),
        )

    async def _metrics(
        self, focus: list[str], *, at: datetime | None
    ) -> dict[str, dict[str, float | None]]:
        """error rate, p95 and throughput per focus service, now or at `at`."""
        out: dict[str, dict[str, float | None]] = {service: {} for service in focus}
        for metric in METRICS:
            try:
                if at is None:
                    result = await self.prometheus.metric(metric, window=settings.detector_window)
                else:
                    result = await self.prometheus.metric(
                        metric,
                        window=settings.detector_window,
                        start=at - timedelta(seconds=60),
                        end=at,
                        step_s=15,
                    )
            except Exception as exc:
                log.warning("verification_metric_failed", metric=metric, error=str(exc))
                continue
            for series in result.series:
                service = series.labels.get("service_name")
                if service in out and series.points:
                    out[service][metric] = round(series.points[-1].v, 6)
        return out

    async def _started(self, target: str) -> tuple[str | None, int] | None:
        if target not in ALLOWED_TARGETS:
            return None
        try:
            items = await docker_inspect(target, timeout_s=10.0)
        except DockerError:
            return None
        if not items:
            return None
        state = items[0].get("State") or {}
        return (state.get("StartedAt"), int(items[0].get("RestartCount") or 0))

    async def _fault_dicts(self, fault_ids: list[int]) -> list[dict[str, Any]]:
        if not fault_ids:
            return []
        async with SessionFactory() as session:
            rows = (
                (await session.execute(select(Fault).where(Fault.id.in_(fault_ids))))
                .scalars()
                .all()
            )
            return [
                {"id": row.id, "fault_id": row.type, "status": row.status, "target": row.target}
                for row in rows
            ]

    async def _fault_types(self, fault_ids: list[int]) -> list[str]:
        return [str(item["fault_id"]) for item in await self._fault_dicts(fault_ids)]

    @staticmethod
    def _flags_for(fault_types: list[str]) -> list[str]:
        flags = []
        for fault_type in fault_types:
            definition = DEFINITIONS.get(fault_type)
            if definition is not None and definition.flag:
                flags.append(definition.flag)
        return flags


verifier = Verifier()

__all__ = ["Verdict", "Verifier", "verifier"]
