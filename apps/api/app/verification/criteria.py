"""Per-action verification criteria (PLAN §9), as pure functions.

One `Observation` is what the verifier saw on one detector cycle after an
action executed. `evaluate` turns it into named criteria; the verifier passes
an action only when every criterion holds on N consecutive cycles. Nothing
here does I/O, so the rules are unit-tested without a stack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# PLAN §9 for restart_service: error rate < max(2 %, 1.5x baseline).
ERROR_FLOOR = 0.02
ERROR_BASELINE_FACTOR = 1.5
FAULT_DONE = frozenset({"REVERTED", "EXPIRED"})


@dataclass(slots=True)
class Criterion:
    id: str
    label: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Observation:
    """One detector cycle, reduced to what the criteria read."""

    at: str
    # Detector breaches of this cycle: {"service", "signal", "detail"}.
    breaches: list[dict[str, Any]] = field(default_factory=list)
    # Target container (planner snapshot shape) or None for non-service targets.
    container: dict[str, Any] | None = None
    # The container's (StartedAt, RestartCount) at this cycle and when
    # verification started; a change means it restarted again.
    started: tuple[str | None, int] | None = None
    started_initial: tuple[str | None, int] | None = None
    # Feature flags the action cleared: flag -> current variant.
    flags: dict[str, str | None] = field(default_factory=dict)
    # Faults the action reverted: {"id", "fault_id", "status"}.
    faults: list[dict[str, Any]] = field(default_factory=list)
    # Target inbound error rate and its detector baseline.
    error_rate: float | None = None
    baseline_error: float | None = None


def focus_services(
    *,
    target: str,
    primary: str | None,
    trigger: list[str] | None,
    root: str | None,
) -> list[str]:
    """Services whose detector breaches decide the verdict.

    The action target, the incident's primary service, the services in its
    trigger and the diagnosed root cause. Noise elsewhere (a slow checkout on
    an unrelated path) does not fail a correct remediation.
    """
    names = {target, primary, root, *(trigger or [])}
    return sorted(name for name in names if name and name not in ("load-generator", "incident"))


def _symptoms(obs: Observation, focus: list[str]) -> Criterion:
    hits = [b for b in obs.breaches if b.get("service") in focus]
    if hits:
        detail = "; ".join(f"{b['service']} {b['signal']}: {b.get('detail', '')}" for b in hits)
        return Criterion(
            "symptoms_clear", "No detector breach on the focus services", False, detail
        )
    return Criterion(
        "symptoms_clear",
        "No detector breach on the focus services",
        True,
        f"no breach on {', '.join(focus) or 'any service'}",
    )


def _faults(obs: Observation) -> Criterion:
    label = "Reverted faults stay reverted"
    if not obs.faults:
        return Criterion("faults_reverted", label, True, "no fault was reverted by this action")
    open_ = [f for f in obs.faults if f.get("status") not in FAULT_DONE]
    if open_:
        detail = ", ".join(f"#{f['id']} {f.get('fault_id')} is {f.get('status')}" for f in open_)
        return Criterion("faults_reverted", label, False, detail)
    detail = ", ".join(f"#{f['id']} {f.get('fault_id')} {f.get('status')}" for f in obs.faults)
    return Criterion("faults_reverted", label, True, detail)


def _flags(obs: Observation) -> Criterion:
    label = "Feature flag reads back off"
    stuck = {flag: value for flag, value in obs.flags.items() if value != "off"}
    if not obs.flags:
        return Criterion("flag_off", label, False, "no flag value could be read")
    if stuck:
        return Criterion("flag_off", label, False, f"not off: {stuck}")
    return Criterion("flag_off", label, True, ", ".join(f"{f}=off" for f in obs.flags))


def _healthy(obs: Observation) -> Criterion:
    label = "Container running and healthy"
    state = obs.container
    if not state:
        return Criterion("container_healthy", label, False, "container not found")
    if not state.get("running") or state.get("paused"):
        return Criterion("container_healthy", label, False, f"container is {state.get('status')}")
    if state.get("health") == "unhealthy":
        return Criterion("container_healthy", label, False, "healthcheck reports unhealthy")
    return Criterion(
        "container_healthy", label, True, f"running, health={state.get('health', 'none')}"
    )


def _stable(obs: Observation) -> Criterion:
    label = "No restart loop"
    if obs.started is None or obs.started_initial is None:
        return Criterion("no_restart_loop", label, False, "container start time unknown")
    if obs.started != obs.started_initial:
        return Criterion(
            "no_restart_loop",
            label,
            False,
            f"restarted again (started {obs.started[0]}, restarts {obs.started[1]})",
        )
    return Criterion(
        "no_restart_loop", label, True, f"started {obs.started[0]}, restarts {obs.started[1]}"
    )


def error_ceiling(baseline: float | None) -> float:
    return max(ERROR_FLOOR, ERROR_BASELINE_FACTOR * (baseline or 0.0))


def _error_rate(obs: Observation) -> Criterion:
    ceiling = error_ceiling(obs.baseline_error)
    label = "Error rate under max(2%, 1.5x baseline)"
    if obs.error_rate is None:
        # No inbound spans in the window: nothing failed. A dead service is
        # caught by container_healthy, not here.
        return Criterion(
            "error_rate", label, True, f"no requests in the window (ceiling {ceiling:.1%})"
        )
    ok = obs.error_rate < ceiling
    return Criterion("error_rate", label, ok, f"{obs.error_rate:.1%} vs ceiling {ceiling:.1%}")


def _network(obs: Observation) -> Criterion:
    label = "Container attached to its network"
    networks = (obs.container or {}).get("networks") or []
    if networks:
        return Criterion("network_attached", label, True, ", ".join(networks))
    return Criterion("network_attached", label, False, "no network attached")


def _signal_clear(obs: Observation, signal: str, label: str, crit: str) -> Criterion:
    hits = [b for b in obs.breaches if b.get("signal") == signal]
    if hits:
        return Criterion(
            crit, label, False, "; ".join(f"{b['service']}: {b.get('detail', '')}" for b in hits)
        )
    return Criterion(crit, label, True, f"no {signal} breach")


def evaluate(action_id: str, obs: Observation, focus: list[str]) -> list[Criterion]:
    """The criteria for one action on one cycle (PLAN §9)."""
    criteria = [_symptoms(obs, focus)]
    if action_id in (
        "reset_fault",
        "clear_feature_flag",
        "restore_network",
        "remove_resource_limit",
        "stop_load_generator",
    ):
        criteria.append(_faults(obs))
    if action_id == "clear_feature_flag":
        criteria.append(_flags(obs))
    if action_id == "restart_service":
        criteria += [_healthy(obs), _stable(obs), _error_rate(obs)]
    elif action_id == "restore_network":
        criteria.append(_network(obs))
    elif action_id == "remove_resource_limit":
        criteria += [
            _stable(obs),
            _signal_clear(obs, "container_resource", "Resource limit restored", "limits_restored"),
        ]
    elif action_id == "stop_load_generator":
        criteria.append(
            _signal_clear(obs, "throughput", "Request rate back to normal", "request_rate_normal")
        )
    return criteria


def all_ok(criteria: list[Criterion]) -> bool:
    return bool(criteria) and all(item.ok for item in criteria)


__all__ = [
    "Criterion",
    "Observation",
    "all_ok",
    "error_ceiling",
    "evaluate",
    "focus_services",
]
