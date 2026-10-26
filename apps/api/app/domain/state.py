"""Incident lifecycle.

The state machine is the single source of truth: routers, the detector, the
agent, and the UI all read it from here. Every legal transition is declared
once, and every transition is expected to write an `incident_event` row and
publish `incident.*` to SSE (P8).
"""

from __future__ import annotations

from enum import StrEnum


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


ACTIVE_STATUSES: frozenset[IncidentStatus] = frozenset(
    {
        IncidentStatus.OPEN,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.DIAGNOSED,
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,
        IncidentStatus.VERIFYING,
    }
)

TERMINAL_STATUSES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.ESCALATED}
)

TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    # OPEN -> RESOLVED is the *recovery* edge: the detector saw the symptom
    # clear before anyone investigated. Any later status is owned by the agent
    # and remediation phases, which close through VERIFYING.
    IncidentStatus.OPEN: frozenset(
        {IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED, IncidentStatus.ESCALATED}
    ),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.DIAGNOSED, IncidentStatus.ESCALATED}),
    # DIAGNOSED -> RESOLVED is the same recovery edge after a diagnosis: the
    # symptom cleared (the fault was reverted or expired) before any remediation
    # was approved. Memory (P12) is written from RESOLVED incidents, which is
    # what makes this edge necessary before P14/P15 exist.
    IncidentStatus.DIAGNOSED: frozenset(
        {IncidentStatus.AWAITING_APPROVAL, IncidentStatus.RESOLVED, IncidentStatus.ESCALATED}
    ),
    IncidentStatus.AWAITING_APPROVAL: frozenset(
        {IncidentStatus.REMEDIATING, IncidentStatus.REJECTED, IncidentStatus.ESCALATED}
    ),
    IncidentStatus.REMEDIATING: frozenset({IncidentStatus.VERIFYING, IncidentStatus.ESCALATED}),
    IncidentStatus.VERIFYING: frozenset({IncidentStatus.RESOLVED, IncidentStatus.ESCALATED}),
    IncidentStatus.REJECTED: frozenset({IncidentStatus.ESCALATED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.ESCALATED: frozenset(),
}

SEVERITIES: frozenset[str] = frozenset({"sev1", "sev2", "sev3", "sev4"})


class InvalidTransition(Exception):
    def __init__(self, current: IncidentStatus, target: IncidentStatus) -> None:
        super().__init__(f"illegal incident transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    return target in TRANSITIONS[current]


def ensure_transition(current: IncidentStatus, target: IncidentStatus) -> IncidentStatus:
    """Return `target`, or raise if the move is not in the machine."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target


def is_active(status: IncidentStatus) -> bool:
    return status in ACTIVE_STATUSES


def close_target(current: IncidentStatus) -> IncidentStatus:
    """Manual close: a verified incident resolves, anything else escalates."""
    if current == IncidentStatus.VERIFYING:
        return IncidentStatus.RESOLVED
    if current in TERMINAL_STATUSES:
        return current
    return IncidentStatus.ESCALATED


def recovery_target(current: IncidentStatus) -> IncidentStatus | None:
    """Where an incident goes when the symptom clears on its own.

    OPEN (never investigated) and DIAGNOSED (investigated, nothing executed yet)
    qualify. While the agent runs (INVESTIGATING) or once remediation has started,
    the agent and the verification phase own the outcome, not the detector.
    """
    if current in (IncidentStatus.OPEN, IncidentStatus.DIAGNOSED):
        return IncidentStatus.RESOLVED
    return None
