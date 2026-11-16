"""Remediation action catalog v1 (PLAN §9).

Data only. The agent's diagnosis *recommends* one of these; the validator checks
that the action exists and allows the target for this incident; P14's planner
and executor decide what actually runs. Nothing here mutates anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.faults.catalog import LOAD_TARGETS, SERVICE_TARGETS

Risk = Literal["none", "low", "medium", "high"]
Approval = Literal["auto", "required", "none"]
TargetKind = Literal["fault", "flag", "service", "load", "lab", "incident"]
RISK_ORDER: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_id: str
    target_kind: TargetKind
    risk: Risk
    approval: Approval
    description: str
    verification: str
    # Fault mechanisms this action reverts through the fault controller (with
    # the fault's recorded pre-image). Empty: the action acts on its own.
    reverts: tuple[str, ...] = ()
    # Rollback when the action fails or does not help. "none" means restoring
    # the pre-action state would re-create the fault, so it is never done.
    rollback: str = "none"
    rollback_note: str = "restoring the pre-action state would re-inject the fault"
    # Fixes drift (app/remediation/reconcile.py) as well as recorded faults, so
    # it works on problems DocOck did not inject.
    generic: bool = True


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
            reverts=("*",),
        ),
        ActionSpec(
            "clear_feature_flag",
            "flag",
            "low",
            "auto",
            "Set the target's fault flag back to off.",
            "same as reset_fault, and the flag reads back off",
            reverts=("flagd",),
        ),
        ActionSpec(
            "restart_service",
            "service",
            "medium",
            "required",
            "Restart the target container.",
            "container healthy, error rate < max(2%, 1.5x baseline) for 90 s",
            reverts=("container_stop", "container_kill", "container_pause"),
            rollback="ensure_running",
            rollback_note="start the container again if it was running before and is not now",
        ),
        ActionSpec(
            "restore_network",
            "service",
            "medium",
            "required",
            "Reconnect the target container to the demo network.",
            "reachability restored, downstream error rate drops",
            reverts=("network_partition",),
        ),
        ActionSpec(
            "remove_resource_limit",
            "service",
            "medium",
            "required",
            "Restore the target container's CPU/memory limits.",
            "CPU/memory back under baseline, no restart loop",
            reverts=("resource_limit",),
        ),
        ActionSpec(
            "stop_load_generator",
            "load",
            "low",
            "auto",
            "Return the load generator to its normal user count.",
            "request rate falls, error rate normalises",
            reverts=("load_spike", "fault:traffic_flood"),
        ),
        ActionSpec(
            "reconcile_service",
            "service",
            "medium",
            "required",
            "Restore every drifted property of the target (running, network, limits, "
            "its flags) to the desired state.",
            "no drift left on the target, detector breaches clear",
            reverts=("*",),
            rollback="ensure_running",
            rollback_note="start the container again if it was running before and is not now",
        ),
        ActionSpec(
            "reconcile_all",
            "lab",
            "high",
            "required",
            "Restore every drifted container, flag and the load generator in the lab to the "
            "desired state. Last resort when the root cause is unclear.",
            "no drift anywhere in the lab, detector breaches clear",
            reverts=("*",),
        ),
        ActionSpec(
            "escalate_human",
            "incident",
            "none",
            "none",
            "Hand the incident to a human; terminates the loop.",
            "none",
            rollback_note="nothing executes",
            generic=False,
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

    `fault_targets` / `flag_targets` are the services with something to reset:
    an open recorded fault (any mechanism / flagd) *or* drift of that kind
    (app/remediation/drift.py). Resetting nothing is not a remediation.
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
    if spec.target_kind == "service" and target not in SERVICE_TARGETS:
        return f"{action_id} only targets allowlisted services; {target!r} is not one"
    if spec.target_kind == "lab" and target not in ("lab", "incident"):
        return f"{action_id} targets the whole lab ('lab'); got {target!r}"
    if spec.target_kind == "load" and target not in LOAD_TARGETS:
        return f"{action_id} only targets {', '.join(LOAD_TARGETS)}; got {target!r}"
    if (
        spec.target_kind == "incident"
        and target != "incident"
        and target not in set(known_services)
    ):
        return f"escalate_human targets 'incident' or a known service; got {target!r}"
    return None


def reverts_fault(spec: ActionSpec, *, fault_type: str, mechanism: str) -> bool:
    """Whether `spec` is the remediation for an injected fault of this kind."""
    return "*" in spec.reverts or mechanism in spec.reverts or f"fault:{fault_type}" in spec.reverts


def risk_at_most(risk: str, ceiling: str) -> bool:
    return RISK_ORDER.get(risk, 99) <= RISK_ORDER.get(ceiling, 0)


__all__ = ["ACTIONS", "RISK_ORDER", "ActionSpec", "reverts_fault", "risk_at_most", "target_allowed"]
