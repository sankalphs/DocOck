from __future__ import annotations

from itertools import pairwise

import pytest

from app.domain.state import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    IncidentStatus,
    InvalidTransition,
    close_target,
    ensure_transition,
    is_active,
    recovery_target,
)


def test_happy_path_reaches_resolved() -> None:
    path = [
        IncidentStatus.OPEN,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.DIAGNOSED,
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,
        IncidentStatus.VERIFYING,
        IncidentStatus.RESOLVED,
    ]
    for current, target in pairwise(path):
        assert ensure_transition(current, target) == target


def test_recovery_edge_only_leaves_open() -> None:
    # P8: the detector may close an incident that recovered before anyone
    # investigated it, and only then.
    assert (
        ensure_transition(IncidentStatus.OPEN, IncidentStatus.RESOLVED) == IncidentStatus.RESOLVED
    )
    with pytest.raises(InvalidTransition):
        ensure_transition(IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED)
    assert recovery_target(IncidentStatus.OPEN) == IncidentStatus.RESOLVED
    assert recovery_target(IncidentStatus.INVESTIGATING) is None
    # A diagnosed incident whose symptom cleared before remediation resolves.
    assert recovery_target(IncidentStatus.DIAGNOSED) == IncidentStatus.RESOLVED
    assert recovery_target(IncidentStatus.REMEDIATING) is None
    ensure_transition(IncidentStatus.DIAGNOSED, IncidentStatus.RESOLVED)


def test_rejection_escalates() -> None:
    ensure_transition(IncidentStatus.AWAITING_APPROVAL, IncidentStatus.REJECTED)
    ensure_transition(IncidentStatus.REJECTED, IncidentStatus.ESCALATED)


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition):
        ensure_transition(IncidentStatus.OPEN, IncidentStatus.DIAGNOSED)
    with pytest.raises(InvalidTransition):
        ensure_transition(IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING)


def test_terminal_states_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert not is_active(status)
        assert close_target(status) == status


def test_every_active_status_can_escalate() -> None:
    for status in ACTIVE_STATUSES:
        ensure_transition(status, IncidentStatus.ESCALATED)


def test_manual_close_target() -> None:
    assert close_target(IncidentStatus.VERIFYING) == IncidentStatus.RESOLVED
    assert close_target(IncidentStatus.INVESTIGATING) == IncidentStatus.ESCALATED
    assert close_target(IncidentStatus.OPEN) == IncidentStatus.ESCALATED
