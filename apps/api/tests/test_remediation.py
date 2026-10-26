"""Remediation (P14): tokens, catalog matching, planner refusals, rollback policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models import Fault
from app.errors import ApiError
from app.faults import safety
from app.remediation import planner, tokens
from app.remediation.catalog import ACTIONS, reverts_fault
from app.remediation.executor import Executor
from app.remediation.schemas import ApprovalView, PlanRequest

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


# --- tokens -----------------------------------------------------------------
def test_token_is_stored_only_as_a_hash() -> None:
    token, digest = tokens.mint()
    assert token not in digest
    assert len(digest) == 64
    assert digest == tokens.token_hash(token)
    other, other_digest = tokens.mint()
    assert other != token and other_digest != digest


def test_token_accepted_once_before_expiry() -> None:
    token, digest = tokens.mint()
    ok = tokens.check(
        token, stored_hash=digest, expires_at=NOW + timedelta(seconds=60), used_at=None, now=NOW
    )
    assert ok is None


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("forged", "does not match"),
        ("replayed", "already used"),
        ("expired", "expired"),
        ("unissued", "no token"),
    ],
)
def test_token_refusals(case: str, reason: str) -> None:
    token, digest = tokens.mint()
    kwargs: dict[str, Any] = {
        "stored_hash": digest,
        "expires_at": NOW + timedelta(seconds=60),
        "used_at": None,
        "now": NOW,
    }
    if case == "forged":
        token = tokens.mint()[0]
    elif case == "replayed":
        kwargs["used_at"] = NOW - timedelta(seconds=1)
    elif case == "expired":
        kwargs["expires_at"] = NOW
    else:
        kwargs["stored_hash"] = tokens.UNISSUED
    result = tokens.check(token, **kwargs)
    assert result is not None and reason in result


def test_no_api_model_can_carry_a_token() -> None:
    assert not {"token", "token_hash"} & set(ApprovalView.model_fields)
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"action_id": "reset_fault", "token": "x"})


# --- catalog ----------------------------------------------------------------
def test_each_action_reverts_only_its_own_fault_kind() -> None:
    assert reverts_fault(
        ACTIONS["reset_fault"], fault_type="service_down", mechanism="container_stop"
    )
    assert reverts_fault(
        ACTIONS["clear_feature_flag"], fault_type="payment_failure", mechanism="flagd"
    )
    assert not reverts_fault(
        ACTIONS["clear_feature_flag"], fault_type="service_down", mechanism="container_stop"
    )
    assert reverts_fault(
        ACTIONS["restore_network"], fault_type="network_partition", mechanism="network_partition"
    )
    assert not reverts_fault(
        ACTIONS["restart_service"], fault_type="payment_failure", mechanism="flagd"
    )
    assert reverts_fault(
        ACTIONS["stop_load_generator"], fault_type="traffic_flood", mechanism="flagd"
    )
    assert not reverts_fault(ACTIONS["escalate_human"], fault_type="x", mechanism="flagd")


def _fault(row_id: int, fault_type: str, target: str) -> Fault:
    return Fault(id=row_id, type=fault_type, target=target, status="ACTIVE", params={})


def test_matching_faults_filters_by_target_and_mechanism() -> None:
    faults = [
        _fault(1, "payment_failure", "payment"),
        _fault(2, "service_down", "email"),
        _fault(3, "network_partition", "payment"),
    ]
    reset = planner.matching_faults(ACTIONS["reset_fault"], "payment", faults)
    assert [f.id for f in reset] == [1, 3]
    network = planner.matching_faults(ACTIONS["restore_network"], "payment", faults)
    assert [f.id for f in network] == [3]
    assert planner.matching_faults(ACTIONS["restart_service"], "payment", faults) == []


# --- planner refusals -------------------------------------------------------
@pytest.fixture
def no_faults(monkeypatch: pytest.MonkeyPatch) -> list[Fault]:
    faults: list[Fault] = []

    async def fake_open(_session: Any) -> list[Fault]:
        return faults

    monkeypatch.setattr(planner, "open_faults", fake_open)
    return faults


async def _evaluate(action_id: str, target: str) -> Any:
    return await planner.evaluate(
        None,  # type: ignore[arg-type]
        action_id,
        target,
        incident_status="DIAGNOSED",
        expected_status="DIAGNOSED",
    )


async def test_unknown_action_is_a_hard_reject(no_faults: list[Fault]) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _evaluate("drop_database", "payment")
    assert excinfo.value.status_code == 422 and excinfo.value.code == "unknown_action"


async def test_escalate_human_is_not_approvable(no_faults: list[Fault]) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _evaluate("escalate_human", "incident")
    assert excinfo.value.code == "not_executable"


async def test_restart_outside_the_allowlist_is_rejected_without_docker(
    no_faults: list[Fault], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("docker must not be called for a non-lab target")

    monkeypatch.setattr(safety, "docker_json", boom)
    with pytest.raises(ApiError) as excinfo:
        await _evaluate("restart_service", "postgres")
    assert excinfo.value.status_code == 422 and excinfo.value.code == "target_not_allowed"


async def test_reset_without_an_open_fault_fails_its_preconditions(
    no_faults: list[Fault],
) -> None:
    check = await _evaluate("reset_fault", "payment")
    assert not check.ok
    failed = {item.name for item in check.preconditions if not item.ok}
    assert failed == {"catalog_target", "fault_present"}


async def test_reset_with_an_open_fault_plans_a_controller_revert(
    no_faults: list[Fault],
) -> None:
    no_faults.append(_fault(7, "payment_failure", "payment"))
    check = await _evaluate("reset_fault", "payment")
    assert check.ok
    assert [f["id"] for f in check.faults] == [7]
    assert "revert fault #7 payment_failure" in check.steps[0]
    assert check.rollback["policy"] == "none"


async def test_wrong_incident_state_fails_the_precondition(no_faults: list[Fault]) -> None:
    no_faults.append(_fault(7, "payment_failure", "payment"))
    check = await planner.evaluate(
        None,  # type: ignore[arg-type]
        "reset_fault",
        "payment",
        incident_status="RESOLVED",
        expected_status="AWAITING_APPROVAL",
    )
    assert not check.ok
    assert [i.name for i in check.preconditions if not i.ok] == ["incident_state"]


# --- rollback policy --------------------------------------------------------
async def test_flag_rollback_is_never_performed() -> None:
    outcome = await Executor().rollback("reset_fault", "payment", {}, reason="boom")
    assert outcome["performed"] is False
    assert "re-inject" in outcome["detail"]


async def test_restart_rollback_skips_a_container_that_was_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(*_args: Any, **_kwargs: Any) -> Any:
        return safety.ContainerTarget(
            service="email",
            container="email",
            project="docock-demo",
            image="x",
            state="exited",
            paused=False,
            health="none",
        )

    monkeypatch.setattr("app.remediation.executor.resolve_target", fake_resolve)
    outcome = await Executor().rollback(
        "restart_service", "email", {"container": {"running": False}}, reason="boom"
    )
    assert outcome["performed"] is False
    assert "was not running before" in outcome["detail"]
