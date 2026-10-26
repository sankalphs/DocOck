"""Incident report: one self-contained JSON document (P15).

Everything a reader needs without the stack: the timeline, the suspects, the
diagnosis with the full rows of every cited evidence code, each proposed
action with its human decision, what executed, the verification before/after
and criteria, the faults that overlapped the incident and the audit trail.
The Markdown export (app/reports/markdown.py) renders this document only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import summarise
from app.agent.orchestrator import diagnosis_view
from app.db.models import Action, AgentRun, Approval, Diagnosis, Evidence, Incident, Verification
from app.incidents.service import service as incident_service
from app.remediation.approval import action_view
from app.remediation.audit import ActionAuditLog

SCHEMA = "docock.report/v1"
# Verification samples are kept in full in the DB; the report keeps the tail.
MAX_SAMPLES = 60


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def event_detail(kind: str, payload: dict[str, Any]) -> str:
    """One line per timeline event."""
    if "from" in payload and "to" in payload:
        text = f"{payload['from']} → {payload['to']}"
        if payload.get("actor"):
            text += f" by {payload['actor']}"
        for key in ("verdict", "action_id", "reason", "note"):
            if payload.get(key):
                text += f"; {key}={payload[key]}"
        return text
    if kind == "signal":
        return f"{payload.get('service')} {payload.get('signal')}: {payload.get('detail', '')}"
    if kind == "opened":
        # The trigger carries every breached signal; the first one opened it.
        signals = payload.get("signals") or [{}]
        first = signals[0]
        head = (payload.get("severity"), first.get("service"), first.get("signal"))
        text = " ".join(str(part) for part in head if part)
        if first.get("detail"):
            text += f": {first['detail']}"
        if len(signals) > 1:
            text += f" (+{len(signals) - 1} more signal(s))"
        return text
    keys = (
        "detail",
        "reason",
        "action_id",
        "target",
        "approval_id",
        "status",
        "run_id",
        "focus",
    )
    parts = [f"{key}={payload[key]}" for key in keys if payload.get(key) not in (None, "", [])]
    return "; ".join(parts) or kind


def _evidence_row(row: Evidence, *, full: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": row.code,
        "type": row.type,
        "source": row.source,
        "service": row.service,
        "at": _iso(row.at),
        "summary": summarise(row),
    }
    if full:
        item["data"] = dict(row.data or {})
        item["hash"] = row.hash
    return item


async def build(session: AsyncSession, incident: Incident) -> dict[str, Any]:
    detail = await incident_service.detail(session, incident)
    evidence = (
        (
            await session.execute(
                select(Evidence).where(Evidence.incident_id == incident.id).order_by(Evidence.code)
            )
        )
        .scalars()
        .all()
    )
    run_rows = (
        (
            await session.execute(
                select(AgentRun).where(AgentRun.incident_id == incident.id).order_by(AgentRun.id)
            )
        )
        .scalars()
        .all()
    )
    diagnosis = await session.scalar(
        select(Diagnosis)
        .join(AgentRun, AgentRun.id == Diagnosis.run_id)
        .where(AgentRun.incident_id == incident.id)
        .order_by(Diagnosis.valid.desc(), Diagnosis.id.desc())
    )
    actions = (
        (
            await session.execute(
                select(Action).where(Action.incident_id == incident.id).order_by(Action.id)
            )
        )
        .scalars()
        .all()
    )
    approvals = {
        row.action_row_id: row
        for row in (
            await session.execute(
                select(Approval).where(Approval.action_row_id.in_([a.id for a in actions] or [0]))
            )
        ).scalars()
    }
    verifications: dict[int, Verification] = {}
    for row in (
        await session.execute(
            select(Verification)
            .where(Verification.action_row_id.in_([a.id for a in actions] or [0]))
            .order_by(Verification.id)
        )
    ).scalars():
        verifications[row.action_row_id] = row

    cited_codes = list(diagnosis.evidence_codes or []) if diagnosis else []
    by_code = {row.code: row for row in evidence}
    remediation: list[dict[str, Any]] = []
    for action in actions:
        view = action_view(action, incident.public_id).model_dump(mode="json")
        approval = approvals.get(action.id)
        verification = verifications.get(action.id)
        remediation.append(
            {
                "action": view,
                "approval": (
                    {
                        "id": approval.id,
                        "status": approval.status,
                        "created_at": _iso(approval.created_at),
                        "expires_at": _iso(approval.expires_at),
                        "decided_at": _iso(approval.decided_at),
                        "decided_by": approval.decided_by,
                        "note": approval.note,
                        # The token itself is never stored or reported.
                        "token_issued": approval.token_expires_at is not None,
                        "token_used": approval.token_used_at is not None,
                    }
                    if approval
                    else None
                ),
                "verification": (
                    {
                        "id": verification.id,
                        "verdict": verification.verdict,
                        "observed_for_s": verification.observed_for_s,
                        "created_at": _iso(verification.created_at),
                        "criteria": dict(verification.criteria or {}),
                        "before": dict(verification.before or {}),
                        "after": {
                            **{
                                k: v
                                for k, v in (verification.after or {}).items()
                                if k != "samples"
                            },
                            "samples": list((verification.after or {}).get("samples") or [])[
                                -MAX_SAMPLES:
                            ],
                        },
                    }
                    if verification
                    else None
                ),
            }
        )
    executed = [
        r for r in remediation if r["action"]["status"] in ("EXECUTED", "ROLLED_BACK", "FAILED")
    ]
    last = executed[-1] if executed else None
    opened = detail.opened_at
    closed = detail.closed_at
    audit = await ActionAuditLog().tail(limit=200, incident=incident.public_id)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "incident": {
            "ref": incident.public_id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "primary_service": incident.primary_service,
            "signature": incident.signature,
            "opened_at": _iso(opened),
            "closed_at": _iso(closed),
            "duration_s": int((closed - opened).total_seconds()) if closed else None,
            "trigger": dict(incident.trigger or {}),
        },
        "outcome": {
            "status": incident.status,
            "action": (
                f"{last['action']['action_id']} on {last['action']['target']}" if last else None
            ),
            "verdict": (last["verification"] or {}).get("verdict") if last else None,
            "observed_for_s": (last["verification"] or {}).get("observed_for_s") if last else None,
            "closed_by": next(
                (
                    e.payload.get("actor")
                    for e in reversed(detail.events)
                    if e.payload.get("to") == incident.status
                ),
                None,
            ),
        },
        "timeline": [
            {
                "seq": event.seq,
                "at": _iso(event.at),
                "kind": event.kind,
                "detail": event_detail(event.kind, event.payload),
            }
            for event in detail.events
        ],
        "suspects": [
            {
                "rank": item.rank,
                "service": item.service,
                "score": round(item.score, 4),
                "signals": item.signals,
            }
            for item in detail.suspects
        ],
        "diagnosis": diagnosis_view(diagnosis).model_dump(mode="json") if diagnosis else None,
        "agent_runs": [
            {
                "id": run.id,
                "status": run.status,
                "provider": run.provider,
                "model": run.model,
                "tokens_in": run.tokens_in,
                "tokens_out": run.tokens_out,
                "cost_usd": float(run.cost_usd),
                "latency_ms": run.latency_ms,
                "started_at": _iso(run.started_at),
            }
            for run in run_rows
        ],
        "evidence": {
            "cited": [_evidence_row(by_code[c], full=True) for c in cited_codes if c in by_code],
            "missing_citations": [c for c in cited_codes if c not in by_code],
            "index": [_evidence_row(row, full=False) for row in evidence],
        },
        "faults": [fault.model_dump(mode="json") for fault in detail.faults],
        "remediation": remediation,
        "audit": [entry.model_dump(mode="json") for entry in audit],
    }


__all__ = ["SCHEMA", "build", "event_detail"]
