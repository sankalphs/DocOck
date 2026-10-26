"""Remediation action catalog v1 (PLAN §9).

Data only. The agent's diagnosis *recommends* one of these; the validator checks
that the action exists and allows the target for this incident; P14's planner
and executor decide what actually runs. Nothing here mutates anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.faults.catalog import INFRA_TARGETS, LOAD_TARGETS

Risk = Literal["none", "low", "medium"]
Approval = Literal["auto", "required", "none"]
TargetKind = Literal["fault", "flag", "service", "load", "incident"]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_id: str
    target_kind: TargetKind
    risk: Risk
    approval: Approval
    description: str
    verification: str


ACTIONS: dict[str, ActionSpec] = {
    spec.action_id: spec
    for spec in (
        ActionSpec(
            "reset_fault",
            "fault",
            "low",
            "auto",
            "Revert the injected fault on the target service.",
            "error rate and p95 return to baseline within 90 s",
        ),
        ActionSpec(
            "clear_feature_flag",
            "flag",
            "low",
            "auto",
            "Set the target's fault flag back to off.",
            "same as reset_fault, and the flag reads back off",
        ),
        ActionSpec(
            "restart_service",
            "service",
            "medium",
            "required",
            "Restart the target container.",
            "container healthy, error rate < max(2%, 1.5x baseline) for 90 s",
        ),
        ActionSpec(
            "restore_network",
            "service",
            "medium",
            "required",
            "Reconnect the target container to the demo network.",
            "reachability restored, downstream error rate drops",
        ),
        ActionSpec(
            "remove_resource_limit",
            "service",
            "medium",
            "required",
            "Restore the target container's CPU/memory limits.",
            "CPU/memory back under baseline, no restart loop",
        ),
        ActionSpec(
            "stop_load_generator",
            "load",
            "low",
            "auto",
            "Return the load generator to its normal user count.",
            "request rate falls, error rate normalises",
        ),
        ActionSpec(
            "escalate_human",
            "incident",
            "none",
            "none",
            "Hand the incident to a human; terminates the loop.",
            "none",
        ),
    )
}


def target_allowed(
    action_id: str,
    target: str,
    *,
    fault_targets: Iterable[str] = (),
    flag_targets: Iterable[str] = (),
    known_services: Iterable[str] = (),
) -> str | None:
    """None when allowed, otherwise the reason it is not.

    `fault_targets` / `flag_targets` are the services with a fault (any
    mechanism / flagd) overlapping the incident: resetting a fault that does not
    exist is not a remediation.
    """
    spec = ACTIONS.get(action_id)
    if spec is None:
        return f"action {action_id!r} is not in the catalog ({', '.join(sorted(ACTIONS))})"
    if spec.target_kind == "fault" and target not in set(fault_targets):
        return (
            f"{action_id} needs a service with an injected fault in this incident; got {target!r}"
        )
    if spec.target_kind == "flag" and target not in set(flag_targets):
        return f"{action_id} needs a service with a flag fault in this incident; got {target!r}"
    if spec.target_kind == "service" and target not in INFRA_TARGETS:
        return f"{action_id} only targets allowlisted services; {target!r} is not one"
    if spec.target_kind == "load" and target not in LOAD_TARGETS:
        return f"{action_id} only targets {', '.join(LOAD_TARGETS)}; got {target!r}"
    if (
        spec.target_kind == "incident"
        and target != "incident"
        and target not in set(known_services)
    ):
        return f"escalate_human targets 'incident' or a known service; got {target!r}"
    return None


__all__ = ["ACTIONS", "ActionSpec", "target_allowed"]
