"""Remediation executor: token → preconditions → act → snapshot → rollback.

The executor is the only code that turns an approved action into a mutation.
It accepts exactly one argument besides the approval id: the single-use token
the approve call minted in memory. Before it touches anything it

1. locks the approval and checks the token (hash, own expiry, not yet used),
2. consumes the token in the same transaction,
3. re-evaluates the action's preconditions against live state.

Fault-reverting actions go through the fault controller, so the revert uses
the fault's recorded pre-image (flag variant, network aliases, limits) and
leaves the fault audit trail as well. `restart_service` is the only action
that issues its own Docker command, and only after the safety boundary passed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import Action, Approval, Incident
from app.db.session import SessionFactory
from app.domain.state import IncidentStatus
from app.errors import ApiError
from app.events.bus import bus
from app.faults.controller import get_controller
from app.faults.mechanisms import wait_for_container
from app.faults.safety import resolve_target
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.remediation import tokens
from app.remediation.audit import ActionAuditEntry, ActionAuditLog
from app.remediation.planner import evaluate, snapshot, spec_for
from app.remediation.schemas import ActionStatus, ApprovalStatus
from app.telemetry.docker import run_docker

log = get_logger(__name__)

# P15 hooks the verifier in here: called with the action row id once the
# incident is VERIFYING.
ExecutedHook = Callable[[int], Awaitable[None]]


class TokenRejected(ApiError):
    def __init__(self, reason: str) -> None:
        super().__init__(403, "token_rejected", f"approval token refused: {reason}")


class Executor:
    def __init__(self, audit: ActionAuditLog | None = None) -> None:
        self.audit = audit or ActionAuditLog()
        self.on_executed: ExecutedHook | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}

    # --- task management ----------------------------------------------------
    def launch(self, approval_id: int, token: str) -> asyncio.Task[None]:
        task = asyncio.create_task(
            self._guarded(approval_id, token), name=f"remediate-{approval_id}"
        )
        self._tasks[approval_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(approval_id, None))
        return task

    async def wait(self, approval_id: int, timeout_s: float) -> None:
        task = self._tasks.get(approval_id)
        if task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _guarded(self, approval_id: int, token: str) -> None:
        try:
            await self.execute(approval_id, token)
        except ApiError as exc:
            log.warning("remediation_refused", approval=approval_id, error=exc.message)
        except Exception:
            log.exception("remediation_crashed", approval=approval_id)

    # --- the execution ------------------------------------------------------
    async def execute(self, approval_id: int, token: str) -> None:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            approval = await session.get(Approval, approval_id, with_for_update=True)
            if approval is None:
                raise ApiError(404, "approval_not_found", f"approval {approval_id} not found")
            action = await session.get(Action, approval.action_row_id, with_for_update=True)
            assert action is not None
            incident = await session.get(Incident, action.incident_id)
            assert incident is not None
            reason = tokens.check(
                token,
                stored_hash=approval.token_hash,
                expires_at=approval.token_expires_at,
                used_at=approval.token_used_at,
                now=now,
            )
            if reason is None and approval.status != ApprovalStatus.APPROVED:
                reason = f"approval is {approval.status}"
            if reason is not None:
                await self.audit.append(
                    ActionAuditEntry(
                        actor="executor",
                        event="token_refused",
                        incident=incident.public_id,
                        approval_id=approval.id,
                        action_row_id=action.id,
                        action_id=action.action_id,
                        target=action.target,
                        outcome="refused",
                        detail=reason,
                    )
                )
                bus.publish(
                    "action.refused",
                    {"approval_id": approval.id, "action_row_id": action.id, "reason": reason},
                )
                raise TokenRejected(reason)
            approval.token_used_at = now
            actor = f"remediation:A{action.id}"
            check = await evaluate(
                session,
                action.action_id,
                action.target,
                incident_status=incident.status,
                expected_status=IncidentStatus.REMEDIATING.value,
            )
            if not check.ok:
                failed = [item.detail for item in check.preconditions if not item.ok]
                action.status = ActionStatus.FAILED.value
                action.error = "precondition failed at execution: " + "; ".join(failed)
                action.finished_at = now
                if incident.status == IncidentStatus.REMEDIATING:
                    await incident_service.transition(
                        session,
                        incident,
                        IncidentStatus.ESCALATED,
                        kind="action",
                        payload={
                            "action_row_id": action.id,
                            "action_id": action.action_id,
                            "reason": action.error,
                        },
                        actor="executor",
                    )
                await session.commit()
                await self._audit(incident.public_id, approval.id, action, "precondition", "failed")
                bus.publish(
                    "action.failed",
                    {
                        "incident_id": incident.public_id,
                        "action_row_id": action.id,
                        "error": action.error,
                    },
                )
                return
            action.status = ActionStatus.EXECUTING.value
            action.started_at = now
            action.pre_snapshot = await snapshot(action.target, check.faults)
            args = dict(action.args or {})
            args["executed_check"] = check.model_dump(mode="json")
            action.args = args
            await session.commit()
            incident_ref, action_row_id = incident.public_id, action.id
            action_id, target = action.action_id, action.target
            pre = dict(action.pre_snapshot or {})

        await self._audit_simple(
            incident_ref,
            approval_id,
            action_row_id,
            action_id,
            target,
            "execute_started",
            "started",
            "; ".join(check.steps),
            pre,
        )
        bus.publish(
            "action.started",
            {
                "incident_id": incident_ref,
                "approval_id": approval_id,
                "action_row_id": action_row_id,
                "action_id": action_id,
                "target": target,
            },
        )

        result: dict[str, Any] = {}
        error: str | None = None
        try:
            result = await self._perform(action_id, target, check.faults, actor=actor)
        except Exception as exc:
            error = exc.message if isinstance(exc, ApiError) else f"{type(exc).__name__}: {exc}"
            log.error("remediation_failed", action_row=action_row_id, error=error)

        rollback: dict[str, Any] | None = None
        if error is not None:
            rollback = await self.rollback(action_id, target, pre, reason=error)
        post = await snapshot(target, check.faults)
        await self._finish(
            approval_id, action_row_id, result=result, error=error, rollback=rollback, post=post
        )

    async def _perform(
        self, action_id: str, target: str, faults: list[dict[str, Any]], *, actor: str
    ) -> dict[str, Any]:
        controller = get_controller()
        result: dict[str, Any] = {"steps": []}
        if action_id == "restart_service":
            container = await resolve_target(target)
            if container.state != "running":
                verb = "start"
            elif container.paused:
                verb = "unpause"
            else:
                verb = "restart"
            await run_docker(verb, container.container, timeout_s=60.0)
            state = await wait_for_container(
                container.container,
                lambda s: s["running"] and not s["paused"],
                timeout_s=60.0,
            )
            result["steps"].append(
                {"docker": verb, "container": container.container, "state": state}
            )
        for fault in faults:
            view = await controller.revert(int(fault["id"]), actor=actor, reason="remediation")
            result["steps"].append(
                {
                    "fault_row_id": view.id,
                    "fault_id": view.fault_id,
                    "status": view.status.value,
                    "revert": (view.injection_ref or {}).get("revert"),
                }
            )
            if view.status.value not in ("REVERTED", "EXPIRED"):
                raise ApiError(502, "revert_incomplete", f"fault #{view.id} is {view.status}")
        if action_id == "clear_feature_flag":
            shot = await snapshot(target, faults)
            result["flags"] = shot.get("flags") or {}
            stuck = {flag: value for flag, value in result["flags"].items() if value != "off"}
            if stuck:
                raise ApiError(502, "flag_not_off", f"flag(s) did not read back off: {stuck}")
        return result

    async def rollback(
        self, action_id: str, target: str, pre: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Undo what is safe to undo. Never re-creates a fault."""
        spec = spec_for(action_id)
        outcome: dict[str, Any] = {
            "policy": spec.rollback,
            "reason": reason,
            "performed": False,
            "at": datetime.now(UTC).isoformat(),
        }
        if spec.rollback != "ensure_running":
            outcome["detail"] = f"not performed: {spec.rollback_note}"
            return outcome
        before = pre.get("container") or {}
        try:
            container = await resolve_target(target)
        except ApiError as exc:
            outcome["detail"] = f"not performed: {exc.message}"
            return outcome
        if not before.get("running"):
            outcome["detail"] = "not needed: the container was not running before the action"
        elif container.state == "running" and not container.paused:
            outcome["detail"] = "not needed: the container is running"
        else:
            verb = "unpause" if container.paused else "start"
            try:
                await run_docker(verb, container.container, timeout_s=60.0)
                await wait_for_container(
                    container.container, lambda s: s["running"], timeout_s=60.0
                )
                outcome.update(performed=True, detail=f"docker {verb} {container.container}")
            except Exception as exc:
                outcome["detail"] = f"rollback failed: {exc}"
        return outcome

    async def _finish(
        self,
        approval_id: int,
        action_row_id: int,
        *,
        result: dict[str, Any],
        error: str | None,
        rollback: dict[str, Any] | None,
        post: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            action = await session.get(Action, action_row_id, with_for_update=True)
            assert action is not None
            incident = await session.get(Incident, action.incident_id)
            assert incident is not None
            action.result = result
            action.post_snapshot = post
            action.rollback = rollback
            action.finished_at = now
            if error is None:
                action.status = ActionStatus.EXECUTED.value
                target_status = IncidentStatus.VERIFYING
            else:
                action.error = error
                action.status = (
                    ActionStatus.ROLLED_BACK.value
                    if rollback and rollback.get("performed")
                    else ActionStatus.FAILED.value
                )
                target_status = IncidentStatus.ESCALATED
            moved = False
            if incident.status == IncidentStatus.REMEDIATING:
                await incident_service.transition(
                    session,
                    incident,
                    target_status,
                    kind="action",
                    payload={
                        "action_row_id": action.id,
                        "action_id": action.action_id,
                        "target": action.target,
                        "action_status": action.status,
                        **({"reason": error} if error else {}),
                    },
                    actor="executor",
                )
                moved = True
            await session.commit()
            incident_ref = incident.public_id
            status = action.status
            action_id, target = action.action_id, action.target
        await self._audit_simple(
            incident_ref,
            approval_id,
            action_row_id,
            action_id,
            target,
            "executed" if error is None else "execute_failed",
            status,
            error or "ok",
            post,
            extra={"result": result, "rollback": rollback},
        )
        bus.publish(
            "action.executed" if error is None else "action.failed",
            {
                "incident_id": incident_ref,
                "action_row_id": action_row_id,
                "action_id": action_id,
                "target": target,
                "status": status,
                "error": error,
            },
        )
        if rollback is not None:
            bus.publish("action.rolled_back", {"action_row_id": action_row_id, **rollback})
        log.info("remediation_finished", action_row=action_row_id, status=status, error=error)
        if error is None and moved and self.on_executed is not None:
            await self.on_executed(action_row_id)

    # --- audit --------------------------------------------------------------
    async def _audit(
        self, incident: str, approval_id: int, action: Action, event: str, outcome: str
    ) -> None:
        await self._audit_simple(
            incident,
            approval_id,
            action.id,
            action.action_id,
            action.target,
            event,
            outcome,
            action.error or "",
        )

    async def _audit_simple(
        self,
        incident: str,
        approval_id: int,
        action_row_id: int,
        action_id: str,
        target: str,
        event: str,
        outcome: str,
        detail: str,
        state: dict[str, Any] | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        before = state if event == "execute_started" else None
        after = {**(state or {}), **(extra or {})} if event != "execute_started" else None
        await self.audit.append(
            ActionAuditEntry(
                actor="executor",
                event=event,
                incident=incident,
                approval_id=approval_id,
                action_row_id=action_row_id,
                action_id=action_id,
                target=target,
                outcome=outcome,
                detail=detail,
                before=before,
                after=after,
            )
        )

    async def recover_stale(self) -> int:
        """Executions cut short by an API restart can never finish; fail them.

        That covers EXECUTING rows and APPROVED rows whose token was never
        used: the token lived only in the dead process's memory.
        """
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Action).where(
                            Action.status.in_(
                                [ActionStatus.EXECUTING.value, ActionStatus.APPROVED.value]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            for action in rows:
                action.status = ActionStatus.FAILED.value
                action.error = "interrupted: the API restarted before the action finished"
                action.finished_at = datetime.now(UTC)
                incident = await session.get(Incident, action.incident_id)
                if incident is not None and incident.status == IncidentStatus.REMEDIATING:
                    await incident_service.transition(
                        session,
                        incident,
                        IncidentStatus.ESCALATED,
                        kind="action",
                        payload={"action_row_id": action.id, "reason": action.error},
                        actor="startup",
                    )
            await session.commit()
        return len(rows)


executor = Executor()

__all__ = ["Executor", "TokenRejected", "executor"]
