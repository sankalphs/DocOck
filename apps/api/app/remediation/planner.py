"""Remediation planner: turn a diagnosis into a checked, approvable action.

Two kinds of refusal, on purpose:

- **Hard** (the action can never be approved): an action outside the catalog
  (422 `unknown_action`), a target the catalog does not allow for that action
  (422 `target_not_allowed`), or a service outside the fault-controller safety
  boundary (403 `unsafe_target`, the same check every fault write passes).
- **Preconditions** (true or false *now*): the fault the action reverts is
  still open, the container exists, the incident is in the expected state.
  They are evaluated when the plan is made, again on approve, and again by
  the executor right before it acts.

The planner never mutates the lab. It writes one `actions` row (PLANNED) and
one `approvals` row (PENDING, no token yet) and moves the incident to
AWAITING_APPROVAL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Fault
from app.errors import ApiError
from app.faults.catalog import ALLOWED_TARGETS, DEFINITIONS, OPEN_STATUSES, Mechanism
from app.faults.controller import get_controller
from app.faults.mechanisms import _state as container_state
from app.faults.safety import resolve_target
from app.remediation.catalog import ACTIONS, ActionSpec, reverts_fault, target_allowed
from app.remediation.schemas import PlanCheck, PreconditionView
from app.telemetry.docker import DockerError, docker_inspect


def _mechanism(fault: Fault) -> str:
    definition = DEFINITIONS.get(fault.type)
    return definition.mechanism.value if definition else "unknown"


def spec_for(action_id: str) -> ActionSpec:
    spec = ACTIONS.get(action_id)
    if spec is None:
        raise ApiError(
            422,
            "unknown_action",
            f"action {action_id!r} is not in the remediation catalog",
            {"catalog": sorted(ACTIONS)},
        )
    return spec


async def open_faults(session: AsyncSession) -> list[Fault]:
    return list(
        (
            await session.execute(
                select(Fault)
                .where(Fault.status.in_([status.value for status in OPEN_STATUSES]))
                .order_by(Fault.id)
            )
        )
        .scalars()
        .all()
    )


def matching_faults(spec: ActionSpec, target: str, faults: list[Fault]) -> list[Fault]:
    return [
        fault
        for fault in faults
        if fault.target == target
        and reverts_fault(spec, fault_type=fault.type, mechanism=_mechanism(fault))
    ]


async def evaluate(
    session: AsyncSession,
    action_id: str,
    target: str,
    *,
    incident_status: str,
    expected_status: str,
) -> PlanCheck:
    """Check an action against the catalog, the safety boundary and live state.

    Raises for hard refusals; returns a `PlanCheck` whose `ok` is the AND of
    every precondition otherwise.
    """
    spec = spec_for(action_id)
    if spec.target_kind == "incident":
        raise ApiError(
            422,
            "not_executable",
            "escalate_human hands the incident to a human; there is nothing to approve",
        )
    faults = await open_faults(session)
    fault_targets = {fault.target for fault in faults}
    flag_targets = {fault.target for fault in faults if _mechanism(fault) == Mechanism.FLAGD}
    reason = target_allowed(
        action_id,
        target,
        fault_targets=fault_targets,
        flag_targets=flag_targets,
        known_services=ALLOWED_TARGETS,
    )
    if reason is not None and spec.target_kind in ("service", "load"):
        raise ApiError(422, "target_not_allowed", reason, {"action_id": action_id})
    if reason is None and spec.target_kind == "service":
        # The fault controller's boundary: allowlist, compose labels, network.
        await resolve_target(target, require_network=action_id != "restore_network")

    matched = matching_faults(spec, target, faults)
    checks = [
        PreconditionView(
            name="incident_state",
            ok=incident_status == expected_status,
            detail=f"incident is {incident_status}, needs {expected_status}",
        ),
        PreconditionView(
            name="catalog_target",
            ok=reason is None,
            detail=reason or f"{action_id} allows {target}",
        ),
    ]
    if spec.reverts and spec.action_id != "restart_service":
        checks.append(
            PreconditionView(
                name="fault_present",
                ok=bool(matched),
                detail=(
                    f"open fault(s) {', '.join(f'#{f.id} {f.type}' for f in matched)} on {target}"
                    if matched
                    else f"no open fault on {target} that {action_id} reverts"
                ),
            )
        )
    container: dict[str, Any] | None = None
    if spec.target_kind == "service" and reason is None:
        container = await _container(target)
        checks.append(
            PreconditionView(
                name="container_exists",
                ok=container is not None,
                detail=(
                    f"{target} is {container['status']}"
                    if container
                    else f"no container for {target}"
                ),
            )
        )
        if action_id == "restore_network" and container is not None:
            detached = settings.demo_network not in container["networks"]
            checks.append(
                PreconditionView(
                    name="network_detached",
                    ok=detached,
                    detail=(
                        f"{target} is not attached to {settings.demo_network}"
                        if detached
                        else f"{target} is already attached to {settings.demo_network}"
                    ),
                )
            )

    steps = [
        f"revert fault #{fault.id} {fault.type} on {fault.target} with its recorded pre-image"
        for fault in matched
    ]
    if action_id == "restart_service":
        verb = "restart"
        if container is not None and not container["running"]:
            verb = "start"
        elif container is not None and container["paused"]:
            verb = "unpause"
        steps.insert(0, f"docker {verb} {target}, then wait until it is running")
    return PlanCheck(
        action_id=action_id,
        target=target,
        ok=all(item.ok for item in checks),
        risk=spec.risk,
        approval_mode=spec.approval,
        description=spec.description,
        verification=spec.verification,
        preconditions=checks,
        faults=[
            {
                "id": fault.id,
                "fault_id": fault.type,
                "target": fault.target,
                "mechanism": _mechanism(fault),
                "status": fault.status,
                "params": dict(fault.params or {}),
            }
            for fault in matched
        ],
        steps=steps,
        rollback={"policy": spec.rollback, "note": spec.rollback_note},
    )


async def _container(service: str) -> dict[str, Any] | None:
    if service not in ALLOWED_TARGETS:
        return None
    try:
        items = await docker_inspect(service, timeout_s=10.0)
    except DockerError:
        return None
    return container_state(items[0]) if items else None


async def snapshot(target: str, faults: list[dict[str, Any]]) -> dict[str, Any]:
    """The observable state an action changes: container, faults, flags."""
    shot: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "target": target}
    shot["container"] = await _container(target)
    flags: list[str] = []
    for fault in faults:
        definition = DEFINITIONS.get(str(fault["fault_id"]))
        if definition is not None and definition.flag:
            flags.append(definition.flag)
    if flags:
        variants = await asyncio.to_thread(get_controller().mechanisms.flagd.current_variants)
        shot["flags"] = {flag: variants.get(flag) for flag in flags}
    return shot


__all__ = ["evaluate", "matching_faults", "open_faults", "snapshot", "spec_for"]
