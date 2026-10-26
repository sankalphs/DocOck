"""P11: validator (seeded bad outputs), budget, prompts and the stub investigator."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.budget import FINAL_RESERVE_S, Budget, BudgetExceeded
from app.agent.prompts import initial_user_message
from app.agent.stub_policy import diagnose, investigator_policy
from app.agent.validator import (
    ValidationContext,
    canonical,
    extract_json,
    untraced_numbers,
    validate,
)
from app.models.llm.base import ChatMessage, ChatResponse, ToolSpec, Usage

EVIDENCE = {
    "E0001": {"detail": "payment error_rate 0.98 vs baseline 0.0"},
    "E0002": {"origins": 42, "local_errors": 40},
    "E0003": {"fault_id": "payment_failure", "target": "payment", "mechanism": "flagd"},
    "E0004": {"edges": []},
    "E0099": {"detail": "not shown"},
}


def ctx(**overrides: Any) -> ValidationContext:
    base: dict[str, Any] = {
        "incident_codes": set(EVIDENCE),
        "shown_codes": {"E0001", "E0002", "E0003", "E0004"},
        "evidence_data": EVIDENCE,
        "topology_services": {"payment", "checkout", "frontend", "frontend-proxy"},
        "known_services": {"payment", "checkout", "frontend", "frontend-proxy", "load-generator"},
        "fault_targets": {"payment"},
        "flag_targets": {"payment"},
        "threshold": 0.7,
    }
    base.update(overrides)
    return ValidationContext(**base)


def good(**overrides: Any) -> dict[str, Any]:
    diagnosis: dict[str, Any] = {
        "root_cause": {"service": "payment", "fault_class": "application_error"},
        "confidence": 0.85,
        "summary": "payment fails its charges; checkout errors are consequences.",
        "evidence_codes": ["E0001", "E0002", "E0003"],
        "affected_services": ["payment", "checkout"],
        "recommended_action": {"action_id": "reset_fault", "target": "payment"},
        "alternative_hypotheses": [{"service": "checkout", "confidence": 0.1}],
    }
    diagnosis.update(overrides)
    return diagnosis


def test_valid_diagnosis_passes_gate() -> None:
    report = validate(good(), ctx())
    assert report.valid, report.errors
    assert report.passes_gate


def test_low_confidence_is_valid_but_fails_gate() -> None:
    report = validate(good(confidence=0.4), ctx())
    assert report.valid and not report.passes_gate


SEEDED_BAD = {
    "invented code": (good(evidence_codes=["E9999"]), "does not exist"),
    "malformed code": (good(evidence_codes=["X1"]), "not an E#### code"),
    "code never shown": (good(evidence_codes=["E0001", "E0099"]), "never shown"),
    "no codes": (good(evidence_codes=[]), "schema: evidence_codes"),
    "root outside topology": (
        good(root_cause={"service": "redis", "fault_class": "network"}),
        "not in this incident's topology",
    ),
    "unknown fault class": (
        good(root_cause={"service": "payment", "fault_class": "gremlins"}),
        "schema: root_cause.fault_class",
    ),
    "action not in catalog": (
        good(recommended_action={"action_id": "rm_rf", "target": "payment"}),
        "not in the catalog",
    ),
    "action target not allowed": (
        good(recommended_action={"action_id": "reset_fault", "target": "checkout"}),
        "recommended_action",
    ),
    "restart outside allowlist": (
        good(recommended_action={"action_id": "restart_service", "target": "postgres"}),
        "recommended_action",
    ),
    "summary too long": (good(summary="payment failed. " * 100), "chars >"),
    "markdown table": (
        good(summary="| service | rate |\n|---|---|\n| payment | high |"),
        "markdown tables",
    ),
    "invented number": (
        good(summary="payment error rate reached 73 percent."),
        "numbers not found",
    ),
    "unknown affected service": (
        good(affected_services=["payment", "warp-drive"]),
        "unknown services",
    ),
    "confidence out of range": (good(confidence=1.5), "schema: confidence"),
    "smuggled field": (good(memory_ids=["M1"]), "schema: memory_ids"),
}


@pytest.mark.parametrize("name", sorted(SEEDED_BAD))
def test_validator_rejects_seeded_bad_outputs(name: str) -> None:
    raw, fragment = SEEDED_BAD[name]
    report = validate(raw, ctx())
    assert not report.valid
    assert not report.passes_gate
    assert any(fragment in error for error in report.errors), report.errors


def test_code_from_another_incident_is_rejected() -> None:
    # E0005 exists for some other incident but not in this incident's code set.
    report = validate(good(evidence_codes=["E0001", "E0005"]), ctx())
    assert any("E0005 does not exist" in error for error in report.errors)


def test_traced_numbers_pass() -> None:
    summary = "payment error rate was 98% with 42 origin spans."
    assert untraced_numbers(summary, [EVIDENCE["E0001"], EVIDENCE["E0002"]]) == []
    report = validate(good(summary=summary), ctx())
    assert report.valid, report.errors


def test_alias_is_folded() -> None:
    report = validate(
        good(
            root_cause={"service": "frontend-web", "fault_class": "application_error"},
            recommended_action={"action_id": "escalate_human", "target": "incident"},
        ),
        ctx(),
    )
    assert canonical("frontend-web") == "frontend"
    assert report.valid, report.errors
    assert report.diagnosis is not None and report.diagnosis.root_cause.service == "frontend"


def test_extract_json_tolerates_fences_and_rejects_prose() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here it is: {"a": 1}') == {"a": 1}
    assert extract_json("no json here") is None
    assert extract_json(None) is None
    assert validate(None, ctx()).errors == ["reply is not a JSON object"]


# --- budget -------------------------------------------------------------------
def test_budget_caps_tools_and_calls() -> None:
    budget = Budget(max_tool_calls=2, wall_clock_s=90)
    assert budget.max_llm_calls == 6
    assert budget.take_tool() and budget.take_tool()
    assert not budget.take_tool()
    assert budget.tool_calls == 2 and budget.must_finish
    for _ in range(6):
        budget.before_llm()
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_llm()
    assert exc.value.kind == "llm_calls"


def test_budget_reserves_time_for_the_answer() -> None:
    budget = Budget(max_tool_calls=20, wall_clock_s=FINAL_RESERVE_S + 1)
    budget.started -= 2
    assert budget.must_finish
    budget.started -= FINAL_RESERVE_S
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_llm()
    assert exc.value.kind == "wall_clock"


def test_budget_daily_cap() -> None:
    with pytest.raises(BudgetExceeded):
        Budget(spent_today_usd=2.0, daily_budget_usd=2.0).check_start()
    Budget(spent_today_usd=5.0, daily_budget_usd=0).check_start()  # 0 = no cap
    budget = Budget(spent_today_usd=1.99, daily_budget_usd=2.0)
    budget.charge_llm(
        ChatResponse(provider="x", model="y", usage=Usage(tokens_in=1), cost_usd=0.02)
    )
    with pytest.raises(BudgetExceeded):
        budget.before_llm()


# --- stub investigator ----------------------------------------------------------
CONTEXT = {
    "incident": {
        "id": "INC-0001",
        "primary_service": "checkout",
        "signals": [{"service": "checkout"}, {"service": "frontend-proxy"}],
    },
    "suspects": [
        {
            "rank": 1,
            "service": "checkout",
            "score": 0.6,
            "signals": {"error_origin": {"contribution": 0.4}},
            "evidence_codes": ["E0001"],
        },
        {
            "rank": 2,
            "service": "payment",
            "score": 0.55,
            "signals": {"error_origin": {"contribution": 0.3}},
            "evidence_codes": ["E0002"],
        },
    ],
    "services": ["checkout", "frontend-proxy", "payment"],
    "fault_targets": ["payment"],
}
TOOLS = [
    ToolSpec(name=name, description="", parameters={})
    for name in ("get_container_health", "get_active_flags", "get_traces", "get_metrics")
]


def _conversation() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content=initial_user_message(json.dumps(CONTEXT), tool_budget=20)),
    ]


def test_stub_follows_tool_protocol_then_answers() -> None:
    messages = _conversation()
    first = investigator_policy(messages, TOOLS, False)
    assert isinstance(first, list)
    names = [call.name for call in first]
    # The runner-up is within 80 % of the top score, so both containers are checked.
    assert names == ["get_container_health", "get_container_health", "get_active_flags"]
    messages.append(ChatMessage(role="assistant", tool_calls=first))
    outputs = [
        {"evidence_code": "E0010", "container": "checkout", "state": "running", "networks": ["n"]},
        {"evidence_code": "E0011", "container": "payment", "state": "running", "networks": ["n"]},
        {
            "evidence_code": "E0012",
            "during_incident": [
                {"code": "E0003", "fault_id": "payment_failure", "target": "payment"}
            ],
            "active_now": [],
        },
    ]
    for call, output in zip(first, outputs, strict=True):
        messages.append(
            ChatMessage(
                role="tool", tool_call_id=call.id, name=call.name, content=json.dumps(output)
            )
        )
    second = investigator_policy(messages, TOOLS, False)
    assert isinstance(second, list)
    assert [call.name for call in second] == ["get_traces", "get_metrics"]
    messages.append(ChatMessage(role="assistant", tool_calls=second))
    answer = investigator_policy(messages, TOOLS, False)
    assert isinstance(answer, str)
    diagnosis = json.loads(answer)
    # A recorded fault on the runner-up outweighs the ranking margin.
    assert diagnosis["root_cause"] == {"service": "payment", "fault_class": "application_error"}
    assert diagnosis["recommended_action"] == {"action_id": "reset_fault", "target": "payment"}
    assert set(diagnosis["evidence_codes"]) >= {"E0002", "E0003", "E0011", "E0012"}
    assert "E0010" not in diagnosis["evidence_codes"]  # about checkout, not the root
    assert not any(char.isdigit() for char in diagnosis["summary"])


def test_stub_answers_without_tools_and_repairs() -> None:
    messages = _conversation()
    answer = investigator_policy(messages, None, True)
    assert isinstance(answer, str)
    first = json.loads(answer)
    assert first["root_cause"]["service"] == "payment"
    messages.append(ChatMessage(role="assistant", content=answer))
    messages.append(
        ChatMessage(role="user", content="Your diagnosis was rejected: E0002 never shown")
    )
    repaired = json.loads(str(investigator_policy(messages, None, True)))
    assert "E0002" not in repaired["evidence_codes"]


def test_stub_diagnosis_validates() -> None:
    diagnosis = diagnose(CONTEXT, [])
    shown = {"E0001", "E0002"}
    report = validate(
        diagnosis,
        ctx(
            incident_codes=shown,
            shown_codes=shown,
            evidence_data={code: {} for code in shown},
        ),
    )
    assert report.valid, report.errors


def test_stopped_container_maps_to_restart() -> None:
    context = {**CONTEXT, "fault_targets": []}
    results = [
        (
            "get_container_health",
            {"evidence_code": "E0020", "container": "checkout", "state": "exited"},
        )
    ]
    diagnosis = diagnose(context, results)
    assert diagnosis["root_cause"] == {
        "service": "checkout",
        "fault_class": "dependency_unavailable",
    }
    assert diagnosis["recommended_action"] == {"action_id": "restart_service", "target": "checkout"}
    assert "E0020" in diagnosis["evidence_codes"]
