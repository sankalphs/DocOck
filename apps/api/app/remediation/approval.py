"""Approval flow (PLAN §9): plan → PENDING → approve | reject | expire.

```
planner → approval row (PENDING, no token, expires_at = now + APPROVAL_TTL_S)
        → UI shows the card (no token anywhere in a response)
        → POST /api/approvals/{id}/approve → server mints a single-use token,
          stores only its hash, hands the token in memory to the executor
        → executor verifies hash + token expiry + preconditions, consumes it
```

Reject → REJECTED → ESCALATED. Expired → 409 on any decision, nothing runs,
and the incident escalates. Every step writes an incident event, an SSE event
and a line in `runtime/audit/actions.jsonl`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Action, AgentRun, Approval, Diagnosis, Incident
from app.db.session import SessionFactory
from app.domain.state import IncidentStatus
from app.errors import ApiError
from app.events.bus import bus
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.remediation import tokens
from app.remediation.audit import ActionAuditEntry, ActionAuditLog
from app.remediation.catalog import ACTIONS
from app.remediation.executor import Executor
from app.remediation.executor import executor as default_executor
from app.remediation.planner import evaluate
from app.remediation.schemas import (
    ActionStatus,
    ActionView,
    ApprovalList,
    ApprovalStatus,
    ApprovalView,
    DryRunResult,
    PlanRequest,
    PlanResult,
)

log = get_logger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def action_view(action: Action, incident_ref: str) -> ActionView:
    spec = ACTIONS.get(action.action_id)
    return ActionView(
        id=action.id,
        incident_ref=incident_ref,
        action_id=action.action_id,
        target=action.target,
        status=ActionStatus(action.status),
        risk=action.risk,
        approval_mode=action.approval_mode,
        description=spec.description if spec else "",
        verification=spec.verification if spec else "",
        requested_by=action.requested_by,
        diagnosis_id=action.diagnosis_id,
        args=dict(action.args or {}),
        pre_snapshot=action.pre_snapshot,
        post_snapshot=action.post_snapshot,
        result=action.result,
        rollback=action.rollback,
        error=action.error,
        created_at=_utc(action.created_at),
        started_at=_utc(action.started_at) if action.started_at else None,
        finished_at=_utc(action.finished_at) if action.finished_at else None,
    )


def approval_view(
    approval: Approval, action: Action, incident: Incident, *, now: datetime | None = None
) -> ApprovalView:
    now = now or datetime.now(UTC)
    expires = _utc(approval.expires_at)
    return ApprovalView(
        id=approval.id,
        status=ApprovalStatus(approval.status),
        incident_ref=incident.public_id,
        incident_status=incident.status,
        action=action_view(action, incident.public_id),
        expires_at=expires,
        ttl_remaining_s=(
            max(0, int((expires - now).total_seconds()))
            if approval.status == ApprovalStatus.PENDING
            else 0
        ),
        decided_at=_utc(approval.decided_at) if approval.decided_at else None,
        decided_by=approval.decided_by,
        note=approval.note,
        token_issued=approval.token_hash != tokens.UNISSUED,
        token_used=approval.token_used_at is not None,
        created_at=_utc(approval.created_at),
    )


class ApprovalService:
    def __init__(
        self, *, executor: Executor | None = None, audit: ActionAuditLog | None = None
    ) -> None:
        self.executor = executor or default_executor
        self.audit = audit or ActionAuditLog()
        self._task: asyncio.Task[None] | None = None

    # --- reads --------------------------------------------------------------
    async def _load(
        self, session: AsyncSession, approval_id: int, *, lock: bool = False
    ) -> tuple[Approval, Action, Incident]:
        approval = await session.get(Approval, approval_id, with_for_update=lock or None)
        if approval is None:
            raise ApiError(404, "approval_not_found", f"approval {approval_id} not found")
        action = await session.get(Action, approval.action_row_id, with_for_update=lock or None)
        assert action is not None
        incident = await session.get(Incident, action.incident_id)
        assert incident is not None
        return approval, action, incident

    async def get(self, approval_id: int) -> ApprovalView:
        async with SessionFactory() as session:
            approval, action, incident = await self._load(session, approval_id)
            return approval_view(approval, action, incident)

    async def list(
        self, *, status: str = "all", incident_ref: str | None = None, limit: int = 50
    ) -> ApprovalList:
        async with SessionFactory() as session:
            query = (
                select(Approval, Action, Incident)
                .join(Action, Action.id == Approval.action_row_id)
                .join(Incident, Incident.id == Action.incident_id)
            )
            if incident_ref is not None:
                incident = await incident_service.get(session, incident_ref)
                query = query.where(Incident.id == incident.id)
            if status == "pending":
                query = query.where(Approval.status == ApprovalStatus.PENDING.value)
            elif status == "decided":
                query = query.where(Approval.status != ApprovalStatus.PENDING.value)
            rows = (await session.execute(query.order_by(Approval.id.desc()).limit(limit))).all()
            pending = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Approval)
                    .where(Approval.status == ApprovalStatus.PENDING.value)
                )
                or 0
            )
            now = datetime.now(UTC)
            items = [approval_view(a, b, c, now=now) for a, b, c in rows]
        return ApprovalList(items=items, total=len(items), pending=pending, generated_at=now)

    # --- plan ---------------------------------------------------------------
    async def plan(self, ref: str, request: PlanRequest) -> PlanResult:
        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            await session.refresh(incident, with_for_update=True)
            diagnosis = await session.scalar(
                select(Diagnosis)
                .join(AgentRun, AgentRun.id == Diagnosis.run_id)
                .where(AgentRun.incident_id == incident.id, Diagnosis.valid.is_(True))
                .order_by(Diagnosis.id.desc())
            )
            if diagnosis is None:
                raise ApiError(
                    409, "no_valid_diagnosis", f"{incident.public_id} has no valid diagnosis"
                )
            recommended = dict(diagnosis.recommended_action or {})
            if request.action_id is not None and request.target is None:
                raise ApiError(422, "target_required", "an explicit action needs a target")
            action_id = request.action_id or str(recommended.get("action_id") or "")
            target = request.target or str(recommended.get("target") or "")
            override = (action_id, target) != (
                recommended.get("action_id"),
                recommended.get("target"),
            )
            replacing = request.replace and incident.status == IncidentStatus.AWAITING_APPROVAL
            # Hard refusals (catalog, target rule, safety boundary) come first,
            # whatever state the incident is in.
            check = (
                None
                if action_id == "escalate_human"
                else await evaluate(
                    session,
                    action_id,
                    target,
                    incident_status=incident.status,
                    expected_status=(
                        IncidentStatus.AWAITING_APPROVAL.value
                        if replacing
                        else IncidentStatus.DIAGNOSED.value
                    ),
                )
            )
            if incident.status == IncidentStatus.AWAITING_APPROVAL and not replacing:
                raise ApiError(
                    409,
                    "approval_pending",
                    f"{incident.public_id} already awaits a decision; send replace=true "
                    "to supersede the pending proposal",
                )
            if incident.status != IncidentStatus.DIAGNOSED and not replacing:
                raise ApiError(
                    409,
                    "incident_not_diagnosed",
                    f"{incident.public_id} is {incident.status}; a plan needs DIAGNOSED",
                )

            if check is None:
                await incident_service.transition(
                    session,
                    incident,
                    IncidentStatus.ESCALATED,
                    kind="remediation",
                    payload={
                        "action_id": "escalate_human",
                        "reason": "the diagnosis hands the incident to a human",
                        "diagnosis_id": diagnosis.id,
                    },
                    actor=request.actor,
                )
                await session.commit()
                await self._audit(
                    request.actor,
                    "plan",
                    incident.public_id,
                    None,
                    None,
                    action_id,
                    target,
                    "escalated",
                    "escalate_human",
                )
                return PlanResult(escalated=True, detail="escalate_human: handed to a human")

            if not check.ok:
                raise ApiError(
                    409,
                    "precondition_failed",
                    "; ".join(item.detail for item in check.preconditions if not item.ok),
                    check.model_dump(mode="json"),
                )
            now = datetime.now(UTC)
            action = Action(
                incident_id=incident.id,
                action_id=action_id,
                target=target,
                status=ActionStatus.PLANNED.value,
                risk=check.risk,
                approval_mode=check.approval_mode,
                requested_by=request.actor,
                diagnosis_id=diagnosis.id,
                args={
                    "recommended": recommended,
                    "override": override,
                    "note": request.note,
                    "diagnosis": {
                        "id": diagnosis.id,
                        "root_cause": diagnosis.root_cause_service,
                        "fault_class": diagnosis.fault_class,
                        "confidence": diagnosis.confidence,
                        "summary": diagnosis.summary,
                    },
                    "evidence_codes": list(diagnosis.evidence_codes or []),
                    "planned_check": check.model_dump(mode="json"),
                },
            )
            session.add(action)
            await session.flush()
            approval = Approval(
                action_row_id=action.id,
                status=ApprovalStatus.PENDING.value,
                token_hash=tokens.UNISSUED,
                expires_at=now + timedelta(seconds=request.ttl_s or settings.approval_ttl_s),
            )
            session.add(approval)
            await session.flush()
            payload = {
                "approval_id": approval.id,
                "action_row_id": action.id,
                "action_id": action_id,
                "target": target,
                "risk": check.risk,
                "override": override,
            }
            if replacing:
                await incident_service.withdraw_pending(
                    session,
                    incident,
                    reason=f"replaced by approval {approval.id}",
                    keep=approval.id,
                )
                await incident_service.append_event(
                    session, incident, "approval_replaced", {**payload, "actor": request.actor}
                )
            else:
                await incident_service.transition(
                    session,
                    incident,
                    IncidentStatus.AWAITING_APPROVAL,
                    kind="approval",
                    payload=payload,
                    actor=request.actor,
                )
            await session.commit()
            view = approval_view(approval, action, incident)
        await self._audit(
            request.actor,
            "plan",
            view.incident_ref,
            view.id,
            view.action.id,
            action_id,
            target,
            "PENDING",
            "; ".join(check.steps) + (" (override)" if override else ""),
        )
        bus.publish(
            "approval.requested",
            {
                "approval_id": view.id,
                "incident_id": view.incident_ref,
                "action_id": action_id,
                "target": target,
                "risk": check.risk,
                "expires_at": view.expires_at.isoformat(),
            },
        )
        log.info("approval_requested", approval=view.id, action=action_id, target=target)
        if settings.agent_auto_remediate and check.approval_mode == "auto":
            approved = await self.approve(
                view.id, actor="auto-policy", note="AGENT_AUTO_REMEDIATE: low-risk action"
            )
            return PlanResult(approval=approved, auto_approved=True, detail="auto-approved")
        return PlanResult(approval=view, detail=f"{action_id} on {target} awaits approval")

    async def auto_plan(self, ref: str) -> None:
        """After a gated diagnosis: propose the recommended action. Never raises."""
        if not settings.remediation_auto_plan:
            return
        try:
            result = await self.plan(ref, PlanRequest(actor="agent"))
            log.info("auto_plan", incident=ref, detail=result.detail)
        except ApiError as exc:
            log.info("auto_plan_skipped", incident=ref, reason=exc.message)
            with contextlib.suppress(Exception):
                async with SessionFactory() as session:
                    incident = await incident_service.get(session, ref)
                    await incident_service.append_event(
                        session,
                        incident,
                        "remediation_not_planned",
                        {"reason": exc.message, "code": exc.code},
                    )
                    await session.commit()

    # --- decisions ----------------------------------------------------------
    async def approve(
        self, approval_id: int, *, actor: str, note: str | None = None
    ) -> ApprovalView:
        async with SessionFactory() as session:
            approval, action, incident = await self._load(session, approval_id, lock=True)
            await self._require_pending(session, approval, action, incident)
            check = await evaluate(
                session,
                action.action_id,
                action.target,
                incident_status=incident.status,
                expected_status=IncidentStatus.AWAITING_APPROVAL.value,
            )
            if not check.ok:
                raise ApiError(
                    409,
                    "precondition_failed",
                    "; ".join(item.detail for item in check.preconditions if not item.ok),
                    check.model_dump(mode="json"),
                )
            now = datetime.now(UTC)
            token, digest = tokens.mint()
            approval.token_hash = digest
            approval.token_expires_at = now + timedelta(seconds=settings.approval_token_ttl_s)
            approval.status = ApprovalStatus.APPROVED.value
            approval.decided_at = now
            approval.decided_by = actor
            approval.note = note
            action.status = ActionStatus.APPROVED.value
            await incident_service.transition(
                session,
                incident,
                IncidentStatus.REMEDIATING,
                kind="approval",
                payload={
                    "approval_id": approval.id,
                    "decision": "approved",
                    "action_id": action.action_id,
                    "target": action.target,
                },
                actor=actor,
            )
            await session.commit()
            view = approval_view(approval, action, incident)
        await self._audit(
            actor,
            "approve",
            view.incident_ref,
            view.id,
            view.action.id,
            view.action.action_id,
            view.action.target,
            "APPROVED",
            note or "token issued",
        )
        bus.publish(
            "approval.approved",
            {"approval_id": view.id, "incident_id": view.incident_ref, "decided_by": actor},
        )
        self.executor.launch(view.id, token)
        del token
        return view

    async def reject(
        self, approval_id: int, *, actor: str, note: str | None = None
    ) -> ApprovalView:
        async with SessionFactory() as session:
            approval, action, incident = await self._load(session, approval_id, lock=True)
            await self._require_pending(session, approval, action, incident)
            now = datetime.now(UTC)
            approval.status = ApprovalStatus.REJECTED.value
            approval.decided_at = now
            approval.decided_by = actor
            approval.note = note
            action.status = ActionStatus.REJECTED.value
            await incident_service.transition(
                session,
                incident,
                IncidentStatus.REJECTED,
                kind="approval",
                payload={"approval_id": approval.id, "decision": "rejected", "note": note},
                actor=actor,
            )
            await incident_service.transition(
                session,
                incident,
                IncidentStatus.ESCALATED,
                kind="escalated",
                payload={"reason": f"remediation rejected by {actor}", "approval_id": approval.id},
                actor=actor,
            )
            await session.commit()
            view = approval_view(approval, action, incident)
        await self._audit(
            actor,
            "reject",
            view.incident_ref,
            view.id,
            view.action.id,
            view.action.action_id,
            view.action.target,
            "REJECTED",
            note or "",
        )
        bus.publish(
            "approval.rejected",
            {"approval_id": view.id, "incident_id": view.incident_ref, "decided_by": actor},
        )
        return view

    async def dry_run(self, approval_id: int, *, actor: str = "operator") -> DryRunResult:
        """What approving would do right now. Read-only."""
        async with SessionFactory() as session:
            approval, action, incident = await self._load(session, approval_id)
            check = await evaluate(
                session,
                action.action_id,
                action.target,
                incident_status=incident.status,
                expected_status=IncidentStatus.AWAITING_APPROVAL.value,
            )
            pending = approval.status == ApprovalStatus.PENDING and _utc(
                approval.expires_at
            ) > datetime.now(UTC)
            ref, action_row_id = incident.public_id, action.id
        await self._audit(
            actor,
            "dry_run",
            ref,
            approval_id,
            action_row_id,
            check.action_id,
            check.target,
            "ok" if check.ok and pending else "would_refuse",
            "; ".join(check.steps),
        )
        return DryRunResult(
            approval_id=approval_id,
            would_execute=check.ok and pending,
            check=check,
            generated_at=datetime.now(UTC),
        )

    async def _require_pending(
        self, session: AsyncSession, approval: Approval, action: Action, incident: Incident
    ) -> None:
        if approval.status != ApprovalStatus.PENDING:
            raise ApiError(
                409,
                "approval_not_pending",
                f"approval {approval.id} is {approval.status}",
                {"status": approval.status},
            )
        if _utc(approval.expires_at) <= datetime.now(UTC):
            await self._expire(session, approval, action, incident)
            await session.commit()
            raise ApiError(
                409,
                "approval_expired",
                f"approval {approval.id} expired at {_utc(approval.expires_at).isoformat()}",
            )
        if incident.status != IncidentStatus.AWAITING_APPROVAL:
            raise ApiError(
                409,
                "incident_not_awaiting",
                f"{incident.public_id} is {incident.status}, not AWAITING_APPROVAL",
            )

    async def _expire(
        self, session: AsyncSession, approval: Approval, action: Action, incident: Incident
    ) -> None:
        approval.status = ApprovalStatus.EXPIRED.value
        approval.decided_at = datetime.now(UTC)
        approval.decided_by = "expiry"
        action.status = ActionStatus.EXPIRED.value
        if incident.status == IncidentStatus.AWAITING_APPROVAL:
            await incident_service.transition(
                session,
                incident,
                IncidentStatus.ESCALATED,
                kind="approval",
                payload={
                    "approval_id": approval.id,
                    "decision": "expired",
                    "reason": "nobody decided before the approval expired",
                },
                actor="expiry",
            )
        await self._audit(
            "expiry",
            "expire",
            incident.public_id,
            approval.id,
            action.id,
            action.action_id,
            action.target,
            "EXPIRED",
            "no decision in time",
        )
        bus.publish(
            "approval.expired", {"approval_id": approval.id, "incident_id": incident.public_id}
        )

    async def expire_due(self) -> int:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            ids = list(
                (
                    await session.execute(
                        select(Approval.id).where(
                            Approval.status == ApprovalStatus.PENDING.value,
                            Approval.expires_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
        for approval_id in ids:
            async with SessionFactory() as session:
                approval, action, incident = await self._load(session, approval_id, lock=True)
                if approval.status != ApprovalStatus.PENDING:
                    continue
                await self._expire(session, approval, action, incident)
                await session.commit()
        return len(ids)

    # --- runtime ------------------------------------------------------------
    async def start(self) -> None:
        try:
            stale = await self.executor.recover_stale()
            if stale:
                log.warning("remediation_interrupted", actions=stale)
        except Exception as exc:
            log.warning("remediation_recover_failed", error=str(exc))
        self._task = asyncio.create_task(self._sweep_loop(), name="approval-expiry")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.executor.shutdown()

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await self.expire_due()
            except Exception as exc:
                log.warning("approval_sweep_failed", error=str(exc))
            await asyncio.sleep(settings.remediation_sweep_interval_s)

    async def _audit(
        self,
        actor: str,
        event: str,
        incident: str,
        approval_id: int | None,
        action_row_id: int | None,
        action_id: str,
        target: str,
        outcome: str,
        detail: str,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self.audit.append(
            ActionAuditEntry(
                actor=actor,
                event=event,
                incident=incident,
                approval_id=approval_id,
                action_row_id=action_row_id,
                action_id=action_id,
                target=target,
                outcome=outcome,
                detail=detail,
                after=after,
            )
        )


approvals = ApprovalService()

__all__ = ["ApprovalService", "action_view", "approval_view", "approvals"]
