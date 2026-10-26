"""P15: verification criteria, verdict text for memory, report rendering."""

from __future__ import annotations

from app.db.models import Verification
from app.memory.service import _verified
from app.reports.builder import event_detail
from app.reports.markdown import render
from app.verification.criteria import (
    Observation,
    all_ok,
    error_ceiling,
    evaluate,
    focus_services,
)

RUNNING = {
    "status": "running",
    "running": True,
    "paused": False,
    "health": "healthy",
    "networks": ["opentelemetry-demo"],
}


def ids(criteria: list) -> dict[str, bool]:
    return {c.id: c.ok for c in criteria}


def test_focus_is_target_root_primary_and_trigger() -> None:
    focus = focus_services(
        target="payment", primary="checkout", trigger=["checkout", "frontend"], root="payment"
    )
    assert focus == ["checkout", "frontend", "payment"]
    assert focus_services(
        target="load-generator", primary="frontend-proxy", trigger=None, root=None
    ) == ["frontend-proxy"]


def test_breach_on_a_focus_service_fails_noise_elsewhere_does_not() -> None:
    obs = Observation(
        at="t",
        breaches=[{"service": "checkout", "signal": "p95_latency", "detail": "slow"}],
        faults=[{"id": 1, "fault_id": "payment_failure", "status": "REVERTED"}],
    )
    assert ids(evaluate("reset_fault", obs, ["payment"])) == {
        "symptoms_clear": True,
        "faults_reverted": True,
    }
    assert not all_ok(evaluate("reset_fault", obs, ["payment", "checkout"]))


def test_reverted_fault_must_stay_reverted() -> None:
    obs = Observation(at="t", faults=[{"id": 7, "fault_id": "cart_failure", "status": "ACTIVE"}])
    result = ids(evaluate("reset_fault", obs, ["cart"]))
    assert result["faults_reverted"] is False


def test_clear_feature_flag_needs_the_flag_off() -> None:
    faults = [{"id": 2, "fault_id": "ad_failure", "status": "REVERTED"}]
    on = Observation(at="t", faults=faults, flags={"adFailure": "on"})
    off = Observation(at="t", faults=faults, flags={"adFailure": "off"})
    unread = Observation(at="t", faults=faults)
    assert ids(evaluate("clear_feature_flag", on, ["ad"]))["flag_off"] is False
    assert all_ok(evaluate("clear_feature_flag", off, ["ad"]))
    assert ids(evaluate("clear_feature_flag", unread, ["ad"]))["flag_off"] is False


def test_restart_service_health_restart_loop_and_error_ceiling() -> None:
    start = ("2026-10-26T02:00:00Z", 0)
    good = Observation(
        at="t",
        container=RUNNING,
        started=start,
        started_initial=start,
        error_rate=0.01,
        baseline_error=0.0,
    )
    assert all_ok(evaluate("restart_service", good, ["email"]))

    looped = Observation(
        at="t",
        container=RUNNING,
        started=("2026-10-26T02:01:00Z", 1),
        started_initial=start,
        error_rate=0.0,
    )
    assert ids(evaluate("restart_service", looped, ["email"]))["no_restart_loop"] is False

    unhealthy = Observation(
        at="t",
        container={**RUNNING, "health": "unhealthy"},
        started=start,
        started_initial=start,
    )
    assert ids(evaluate("restart_service", unhealthy, ["email"]))["container_healthy"] is False

    stopped = Observation(
        at="t",
        container={**RUNNING, "running": False, "status": "exited"},
        started=start,
        started_initial=start,
    )
    assert ids(evaluate("restart_service", stopped, ["email"]))["container_healthy"] is False

    # The wrong action for a flag fault: the container is fine, errors are not.
    erroring = Observation(
        at="t",
        breaches=[{"service": "payment", "signal": "error_rate", "detail": "100%"}],
        container=RUNNING,
        started=start,
        started_initial=start,
        error_rate=1.0,
        baseline_error=0.0,
    )
    result = ids(evaluate("restart_service", erroring, ["payment"]))
    assert result["error_rate"] is False and result["symptoms_clear"] is False


def test_error_ceiling_is_max_of_floor_and_scaled_baseline() -> None:
    assert error_ceiling(None) == 0.02
    assert error_ceiling(0.01) == 0.02
    assert abs(error_ceiling(0.10) - 0.15) < 1e-9


def test_restore_network_and_resource_and_load_criteria() -> None:
    faults = [{"id": 3, "fault_id": "network_partition", "status": "REVERTED"}]
    detached = Observation(at="t", faults=faults, container={**RUNNING, "networks": []})
    assert ids(evaluate("restore_network", detached, ["payment"]))["network_attached"] is False
    attached = Observation(at="t", faults=faults, container=RUNNING)
    assert all_ok(evaluate("restore_network", attached, ["payment"]))

    start = ("s", 0)
    limited = Observation(
        at="t",
        breaches=[{"service": "ad", "signal": "container_resource", "detail": "cpu 0.25"}],
        started=start,
        started_initial=start,
    )
    assert ids(evaluate("remove_resource_limit", limited, []))["limits_restored"] is False

    flood = Observation(
        at="t",
        breaches=[{"service": "frontend-proxy", "signal": "throughput", "detail": "4x"}],
    )
    assert ids(evaluate("stop_load_generator", flood, []))["request_rate_normal"] is False
    assert all_ok(evaluate("stop_load_generator", Observation(at="t"), ["frontend-proxy"]))


def test_memory_verdict_text_uses_the_real_criteria() -> None:
    passed = Verification(
        verdict="PASSED",
        observed_for_s=52,
        criteria={"final": [{"id": "symptoms_clear", "ok": True}, {"id": "flag_off", "ok": True}]},
    )
    failed = Verification(
        verdict="FAILED",
        observed_for_s=420,
        criteria={
            "final": [{"id": "symptoms_clear", "ok": False}, {"id": "no_restart_loop", "ok": True}]
        },
    )
    assert _verified(passed) == "PASSED after 52 s (symptoms_clear, flag_off)"
    assert _verified(failed) == "FAILED after 420 s (symptoms_clear)"


def test_event_detail_lines() -> None:
    assert (
        event_detail(
            "verification",
            {"from": "VERIFYING", "to": "RESOLVED", "actor": "verifier", "verdict": "PASSED"},
        )
        == "VERIFYING → RESOLVED by verifier; verdict=PASSED"
    )
    assert event_detail("signal", {"service": "cart", "signal": "error_rate", "detail": "x"}) == (
        "cart error_rate: x"
    )
    assert event_detail("approval_replaced", {"approval_id": 4}) == "approval_id=4"
    # The opened event carries the detector trigger, not flat service/signal keys.
    signal = {"service": "payment", "signal": "error_rate", "detail": "inbound 100%"}
    opened = {"severity": "sev1", "signals": [signal, {**signal, "service": "checkout"}]}
    assert event_detail("opened", opened) == (
        "sev1 payment error_rate: inbound 100% (+1 more signal(s))"
    )
    assert event_detail("opened", {"severity": "sev2"}) == "sev2"


def _report() -> dict:
    return {
        "schema": "docock.report/v1",
        "generated_at": "2026-10-26T03:00:00+00:00",
        "incident": {
            "ref": "INC-00001",
            "title": "payment: error rate | 100%",
            "severity": "sev2",
            "status": "RESOLVED",
            "primary_service": "payment",
            "signature": "error_rate:payment",
            "opened_at": "2026-10-26T02:50:00+00:00",
            "closed_at": "2026-10-26T02:58:00+00:00",
            "duration_s": 480,
            "trigger": {},
        },
        "outcome": {
            "status": "RESOLVED",
            "action": "reset_fault on payment",
            "verdict": "PASSED",
            "observed_for_s": 61,
            "closed_by": "verifier",
        },
        "timeline": [{"seq": 1, "at": "t1", "kind": "opened", "detail": "payment error_rate"}],
        "suspects": [{"rank": 1, "service": "payment", "score": 0.5, "signals": {}}],
        "diagnosis": {
            "root_cause_service": "payment",
            "fault_class": "application_error",
            "confidence": 0.82,
            "summary": "Payment charges fail [E0010].",
            "evidence_codes": ["E0010"],
            "affected_services": ["checkout"],
            "recommended_action": {"action_id": "reset_fault", "target": "payment"},
            "alternative_hypotheses": [],
            "valid": True,
        },
        "agent_runs": [
            {
                "id": 9,
                "status": "DIAGNOSED",
                "provider": "stub",
                "model": "stub-1",
                "tokens_in": 10,
                "tokens_out": 5,
                "cost_usd": 0.0,
                "latency_ms": 1200,
            }
        ],
        "evidence": {
            "cited": [
                {
                    "code": "E0010",
                    "type": "error_origin",
                    "source": "jaeger",
                    "service": "payment",
                    "at": "t",
                    "summary": "origins=10",
                    "data": {},
                }
            ],
            "missing_citations": [],
            "index": [
                {
                    "code": "E0010",
                    "type": "error_origin",
                    "source": "jaeger",
                    "service": "payment",
                    "at": "t",
                    "summary": "origins=10",
                }
            ],
        },
        "faults": [
            {
                "id": 5,
                "fault_id": "payment_failure",
                "target": "payment",
                "status": "REVERTED",
                "injected_at": "t0",
                "reset_at": "t2",
            }
        ],
        "remediation": [
            {
                "action": {
                    "id": 4,
                    "action_id": "reset_fault",
                    "target": "payment",
                    "status": "EXECUTED",
                    "risk": "low",
                    "approval_mode": "auto",
                    "requested_by": "agent",
                    "started_at": "t3",
                    "finished_at": "t4",
                    "error": None,
                    "pre_snapshot": {"flags": {"paymentFailure": "100%"}},
                    "post_snapshot": {"flags": {"paymentFailure": "off"}},
                    "result": {"steps": [{"fault_row_id": 5, "status": "REVERTED"}]},
                    "rollback": None,
                },
                "approval": {
                    "status": "APPROVED",
                    "decided_by": "alice",
                    "decided_at": "t3",
                    "note": None,
                    "token_issued": True,
                    "token_used": True,
                },
                "verification": {
                    "verdict": "PASSED",
                    "observed_for_s": 61,
                    "criteria": {
                        "final": [
                            {
                                "id": "symptoms_clear",
                                "label": "No breach",
                                "ok": True,
                                "detail": "ok",
                            }
                        ],
                        "focus": ["payment"],
                        "clean_cycles": 4,
                        "required_clean_cycles": 4,
                        "timeout_s": 420,
                    },
                    "before": {"metrics": {"payment": {"error_rate": 1.0, "p95_latency": 30.0}}},
                    "after": {"metrics": {"payment": {"error_rate": 0.0, "p95_latency": 12.0}}},
                },
            }
        ],
        "audit": [
            {
                "at": "t3",
                "actor": "dashboard",
                "event": "approve",
                "outcome": "APPROVED",
                "detail": "",
            }
        ],
    }


def test_markdown_report_is_self_contained() -> None:
    text = render(_report())
    assert text.startswith("# INC-00001: payment: error rate | 100%")
    for section in (
        "## Summary",
        "## Diagnosis",
        "### Cited evidence",
        "## Remediation",
        "#### Verification: **PASSED** after 61 s",
        "## Timeline",
        "## Audit trail",
        "## Evidence index",
    ):
        assert section in text
    assert "100.0% → 0.0%" in text  # before -> after error rate
    assert "30 ms → 12 ms" in text
    assert "| E0010 | error_origin |" in text
    assert "issued=True, used=True" in text
    assert "| Action | reset_fault on payment |" in text


def test_markdown_handles_an_incident_without_diagnosis_or_action() -> None:
    report = _report()
    report["diagnosis"] = None
    report["remediation"] = []
    report["evidence"] = {"cited": [], "missing_citations": [], "index": []}
    text = render(report)
    assert "_No diagnosis was produced._" in text
    assert "_No action was proposed._" in text
