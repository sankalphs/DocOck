"""Compact incident context for the model (PLAN §11, first box of the loop).

The context is one JSON document: trigger, suspects (each with the evidence codes
behind it), an index of every evidence row with a one-line summary, topology, new
log templates, and flag/container state. Every code listed here counts as
*shown* to the model; the validator rejects a citation of any other code unless
a tool returned it later.

The deterministic suspect ranking is the starting hypothesis, not the answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.validator import canonical
from app.analysis.suspects import NOT_SUSPECTS
from app.db.models import Evidence, Fault, Incident, Suspect
from app.faults.catalog import ALLOWED_TARGETS, DEFINITIONS, INFRA_TARGETS, Mechanism
from app.remediation.catalog import ACTIONS

MAX_INDEX_ROWS = 140
CODES_PER_SUSPECT = 8
_TYPE_PRIORITY = {
    "error_origin": 0,
    "onset": 1,
    "metric_anomaly": 2,
    "flag_state": 3,
    "container_state": 4,
    "log_template": 5,
    "trace_sample": 6,
    "topology": 7,
    "suspect_ranking": 8,
}


@dataclass
class AgentContext:
    payload: dict[str, Any]
    shown_codes: set[str]
    incident_codes: set[str]
    evidence_data: dict[str, Any]
    topology_services: set[str]
    known_services: set[str]
    fault_targets: set[str] = field(default_factory=set)
    flag_targets: set[str] = field(default_factory=set)

    def as_text(self) -> str:
        return json.dumps(self.payload, separators=(",", ":"), default=str)


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarise(row: Evidence) -> str:
    data = row.data or {}
    kind = row.type
    if kind in ("metric_anomaly", "container_state", "flag_state") and data.get("detail"):
        return _short(data["detail"])
    if kind == "onset":
        return (
            f"first anomalous span {data.get('first_anomalous_span_at')} "
            f"(+{data.get('delta_s_from_earliest')}s from earliest)"
        )
    if kind == "error_origin":
        return (
            f"origins={data.get('origins')} local={data.get('local_errors')} "
            f"propagated={data.get('propagated_errors')} "
            f"silent_peer={data.get('silent_peer_errors')} peers={data.get('peers')}"
        )
    if kind in ("trace_sample", "healthy_control"):
        return _short(
            f"trace {data.get('trace_id')} {data.get('root_name')} "
            f"origin={data.get('origin')} services={data.get('services')}"
        )
    if kind == "log_template":
        return _short(f"x{data.get('count')} {data.get('template')}")
    if kind == "log_cluster":
        return _short(f"log totals {data.get('totals')} new_templates={data.get('new_templates')}")
    if kind == "topology":
        return f"{len(data.get('edges') or [])} edges over {data.get('traces')} traces"
    if kind == "suspect_ranking":
        ranking = data.get("ranking") or []
        return ", ".join(f"{item.get('service')} {item.get('score')}" for item in ranking[:5])
    return _short(json.dumps(data, default=str), 120)


async def build_context(
    session: AsyncSession,
    incident: Incident,
    *,
    window: tuple[str, str],
    hypotheses: list[dict[str, Any]] | None = None,
) -> AgentContext:
    evidence = list(
        (
            await session.execute(
                select(Evidence).where(Evidence.incident_id == incident.id).order_by(Evidence.code)
            )
        )
        .scalars()
        .all()
    )
    suspects = list(
        (
            await session.execute(
                select(Suspect).where(Suspect.incident_id == incident.id).order_by(Suspect.rank)
            )
        )
        .scalars()
        .all()
    )
    trigger = incident.trigger or {}

    by_service: dict[str, list[Evidence]] = {}
    for row in evidence:
        if row.service:
            by_service.setdefault(canonical(row.service), []).append(row)

    topology_rows = [row for row in evidence if row.type == "topology"]
    topology_nodes: set[str] = set()
    for row in topology_rows:
        topology_nodes |= {canonical(str(node)) for node in (row.data or {}).get("nodes") or []}
    incident_services = {
        canonical(str(item)) for item in (trigger.get("services") or []) if item
    } | ({canonical(incident.primary_service)} if incident.primary_service else set())
    topology_services = (
        topology_nodes
        | incident_services
        | {canonical(item.service) for item in suspects}
        | {canonical(row.service) for row in evidence if row.service}
    ) - {""}
    known_services = topology_services | set(ALLOWED_TARGETS)

    faults = [row for row in evidence if row.type == "flag_state"]
    fault_targets = {canonical(str(row.service)) for row in faults if row.service}
    flag_targets = {
        canonical(str(row.service))
        for row in faults
        if row.service and (row.data or {}).get("mechanism") == "flagd"
    }
    # A fault injected after the last analysis is still a fault of this window.
    opened = incident.opened_at
    live_faults = (
        (
            await session.execute(
                select(Fault).where(Fault.status.in_(["PROPAGATING", "ACTIVE", "REVERTING"]))
            )
        )
        .scalars()
        .all()
    )
    for fault in live_faults:
        fault_targets.add(fault.target)
        definition = DEFINITIONS.get(fault.type)
        if definition is not None and definition.mechanism == Mechanism.FLAGD:
            flag_targets.add(fault.target)

    ordered = sorted(
        evidence,
        key=lambda row: (
            row.type == "container_state" and "healthy" in str((row.data or {}).get("detail")),
            _TYPE_PRIORITY.get(row.type, 9),
            row.code,
        ),
    )[:MAX_INDEX_ROWS]
    index = [
        {"code": row.code, "type": row.type, "service": row.service, "summary": summarise(row)}
        for row in sorted(ordered, key=lambda row: row.code)
    ]
    shown = {row.code for row in ordered}

    suspect_rows = []
    for suspect in suspects[:5]:
        rows = sorted(
            by_service.get(canonical(suspect.service), []),
            key=lambda row: (_TYPE_PRIORITY.get(row.type, 9), row.code),
        )[:CODES_PER_SUSPECT]
        codes = [row.code for row in rows]
        shown |= set(codes)
        suspect_rows.append(
            {
                "rank": suspect.rank,
                "service": suspect.service,
                "score": suspect.score,
                "signals": {
                    name: {
                        "value": signal.get("value"),
                        "contribution": signal.get("contribution"),
                        "detail": signal.get("detail"),
                    }
                    for name, signal in (suspect.signals or {}).items()
                    if (signal.get("contribution") or 0) > 0
                },
                "evidence_codes": codes,
            }
        )

    edges = []
    if topology_rows:
        raw_edges = (topology_rows[-1].data or {}).get("edges") or []
        edges = sorted(raw_edges, key=lambda edge: -(edge.get("errors") or 0))[:15]

    payload: dict[str, Any] = {
        "incident": {
            "id": incident.public_id,
            "title": incident.title,
            "status": incident.status,
            "primary_service": incident.primary_service,
            "primary_signal": trigger.get("primary_signal"),
            "opened_at": opened.isoformat() if opened else None,
            "window": {"from": window[0], "to": window[1]},
            "signals": [
                {
                    key: item.get(key)
                    for key in ("service", "signal", "value", "baseline", "threshold", "detail")
                }
                for item in (trigger.get("signals") or [])
                if isinstance(item, dict)
            ][:10],
        },
        "suspects": suspect_rows,
        "topology": {
            "evidence_code": topology_rows[-1].code if topology_rows else None,
            "edges": edges,
        },
        "evidence_index": index,
        "services": sorted(topology_services - NOT_SUSPECTS),
        "fault_targets": sorted(fault_targets),
        "action_catalog": [
            {
                "action_id": spec.action_id,
                "target": _target_hint(spec.target_kind),
                "risk": spec.risk,
            }
            for spec in ACTIONS.values()
        ],
    }
    if hypotheses:
        payload["similar_past_incidents"] = {
            "note": (
                "HYPOTHESES ONLY. Past incidents are not evidence for this incident: "
                "check them with tools, never cite them."
            ),
            "items": hypotheses,
        }
    return AgentContext(
        payload=payload,
        shown_codes=shown,
        incident_codes={row.code for row in evidence},
        evidence_data={row.code: row.data for row in evidence},
        topology_services=topology_services,
        known_services=known_services,
        fault_targets=fault_targets,
        flag_targets=flag_targets,
    )


def _target_hint(kind: str) -> str:
    return {
        "fault": "a service listed in fault_targets",
        "flag": "a service in fault_targets whose fault is a flag",
        "service": "one of: " + ", ".join(INFRA_TARGETS),
        "load": "load-generator",
        "incident": "incident",
    }[kind]


__all__ = ["AgentContext", "build_context", "summarise"]
