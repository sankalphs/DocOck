"""The remediation ladder: every fix worth trying for one incident, best first.

Pure function, no I/O. Inputs are what the autopilot already read (diagnosis,
incident, drift, open faults, what was tried); the output is an ordered list of
catalog actions. The LLM planner may *pick* from this list, never add to it, so
every action the autopilot can run is one deterministic code proposed and the
planner/approval/executor checks will accept.

Rungs, in order:

1. the diagnosis' recommendation, when it passed the confidence gate;
2. drift that explains the incident's services (most causal kind first);
3. recorded open faults on those services (`reset_fault`);
4. the diagnosis' recommendation below the gate;
5. any other drift in the lab, then any other recorded fault;
6. restart the root cause / primary service (it may be a real software fault);
7. `reconcile_all`, when anything at all has drifted.

A (action, target) pair already attempted for this incident is never retried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.faults.catalog import SERVICE_TARGETS
from app.remediation.catalog import ACTIONS
from app.remediation.drift import DriftItem


@dataclass(frozen=True, slots=True)
class Candidate:
    action_id: str
    target: str
    source: str  # diagnosis | drift | fault | fallback
    reason: str
    risk: str = "low"
    drift_kind: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.action_id, self.target)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "target": self.target,
            "source": self.source,
            "reason": self.reason,
            "risk": self.risk,
            "drift_kind": self.drift_kind,
        }


@dataclass(slots=True)
class LadderInput:
    focus: list[str]
    root: str | None = None
    recommended: dict[str, Any] | None = None
    passes_gate: bool = False
    confidence: float | None = None
    drift: list[DriftItem] = field(default_factory=list)
    # (target, fault type) of open recorded faults.
    faults: list[tuple[str, str]] = field(default_factory=list)
    attempted: set[tuple[str, str]] = field(default_factory=set)


def _risk(action_id: str) -> str:
    spec = ACTIONS.get(action_id)
    return spec.risk if spec else "high"


def build(data: LadderInput) -> list[Candidate]:
    focus = {name for name in data.focus if name}
    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set(data.attempted)

    def add(action_id: str, target: str, source: str, reason: str, kind: str | None = None) -> None:
        if action_id not in ACTIONS or action_id == "escalate_human":
            return
        key = (action_id, target)
        if key in seen:
            return
        seen.add(key)
        out.append(Candidate(action_id, target, source, reason, _risk(action_id), kind))

    recommended = data.recommended or {}
    rec_action = str(recommended.get("action_id") or "")
    rec_target = str(recommended.get("target") or "")
    if data.passes_gate and rec_action and rec_target:
        add(
            rec_action,
            rec_target,
            "diagnosis",
            f"the diagnosis recommends it (confidence {data.confidence or 0:.2f})",
        )

    def explains_focus(item: DriftItem) -> bool:
        return item.service in focus or bool(focus & set(item.explains))

    related = [item for item in data.drift if explains_focus(item)]
    others = [item for item in data.drift if not explains_focus(item)]
    for item in related:
        if item.fix is not None:
            add(item.fix.action_id, item.fix.target, "drift", item.detail, item.kind)
    for target, fault_type in data.faults:
        if target in focus:
            add("reset_fault", target, "fault", f"open recorded fault {fault_type} on {target}")
    if not data.passes_gate and rec_action and rec_target:
        add(
            rec_action,
            rec_target,
            "diagnosis",
            f"the diagnosis recommends it below the gate (confidence {data.confidence or 0:.2f})",
        )
    for item in others:
        if item.fix is not None:
            add(item.fix.action_id, item.fix.target, "drift", item.detail, item.kind)
    for target, fault_type in data.faults:
        add("reset_fault", target, "fault", f"open recorded fault {fault_type} on {target}")
    for service in (data.root, *data.focus):
        if service and service in SERVICE_TARGETS:
            add(
                "restart_service",
                service,
                "fallback",
                f"no remaining drift explains it; restart {service} in case the fault is in "
                "the process itself",
            )
            break
    if data.drift:
        add("reconcile_all", "lab", "fallback", "restore every drifted component in the lab")
    return out


__all__ = ["Candidate", "LadderInput", "build"]
