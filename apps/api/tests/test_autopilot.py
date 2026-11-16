"""Desired-state drift, generic remediation scope, the ladder, the planner and
the drift-aware investigator. No Docker, no database."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.stub_policy import diagnose
from app.agent.validator import ValidationContext, validate
from app.autopilot.ladder import LadderInput, build
from app.autopilot.planner import RemediationPlanner, deterministic
from app.domain.state import IncidentStatus, can_transition
from app.faults.catalog import (
    ALLOWED_TARGETS,
    DEFINITIONS,
    DEPENDENCY_TARGETS,
    Mechanism,
    SymptomSpec,
)
from app.faults.mechanisms import ProbeReading, symptom_satisfied
from app.remediation.catalog import ACTIONS, risk_at_most, target_allowed
from app.remediation.drift import (
    DesiredContainer,
    DesiredStateStore,
    DriftReport,
    container_drift,
    flag_drift,
    load_drift,
)
from app.remediation.reconcile import relevant

NET = "opentelemetry-demo"


def live(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "running",
        "running": True,
        "paused": False,
        "restarting": False,
        "exit_code": 0,
        "oom_killed": False,
        "restart_count": 0,
        "health": "healthy",
        "networks": [NET],
        "aliases": ["cart"],
        "cpu_limit_cores": None,
        "memory_limit_mb": 160,
        "memory_swap_mb": 320,
    }
    base.update(overrides)
    return base


def desired(**overrides: Any) -> DesiredContainer:
    base: dict[str, Any] = {
        "service": "cart",
        "network": NET,
        "aliases": ["cart"],
        "cpu_limit_cores": None,
        "memory_limit_mb": 160,
        "limits_learned": True,
    }
    base.update(overrides)
    return DesiredContainer(**base)


def drift_of(view: dict[str, Any] | None, want: DesiredContainer | None = None) -> list[Any]:
    return container_drift("cart", view, want or desired(), unlimited_cpu=4, demo_network=NET)


# --- catalog ------------------------------------------------------------------------
def test_catalog_has_25_faults_with_dependency_targets() -> None:
    assert len(DEFINITIONS) == 25
    assert set(DEPENDENCY_TARGETS) <= ALLOWED_TARGETS
    assert DEFINITIONS["cache_outage"].selectable_targets == ["valkey-cart"]
    assert DEFINITIONS["database_outage"].selectable_targets == ["astronomy-db"]
    assert DEFINITIONS["service_crash"].mechanism == Mechanism.CONTAINER_KILL
    assert DEFINITIONS["oom_crash_loop"].cold_apply
    assert DEFINITIONS["product_catalog_failure"].flag == "productCatalogFailure"


def test_restart_count_symptom_needs_a_new_restart_or_an_oom_kill() -> None:
    spec = SymptomSpec(probe="restart_count", service="ad", comparator=">=", threshold=1)
    assert not symptom_satisfied(spec, ProbeReading(value=2.0), 2.0)
    assert symptom_satisfied(spec, ProbeReading(value=3.0), 2.0)
    assert symptom_satisfied(spec, ProbeReading(value=0.0, meta={"oom_killed": True}), 0.0)


def test_new_actions_are_catalogued_with_risk_order() -> None:
    assert ACTIONS["reconcile_service"].risk == "medium"
    assert ACTIONS["reconcile_all"].risk == "high"
    assert risk_at_most("medium", "high") and not risk_at_most("high", "medium")
    assert target_allowed("restart_service", "valkey-cart") is None
    assert target_allowed("reconcile_all", "lab") is None
    assert target_allowed("reconcile_all", "cart") is not None


def test_retry_edges_exist_for_the_autopilot() -> None:
    assert can_transition(IncidentStatus.VERIFYING, IncidentStatus.AWAITING_APPROVAL)
    assert can_transition(IncidentStatus.REMEDIATING, IncidentStatus.AWAITING_APPROVAL)
    assert not can_transition(IncidentStatus.ESCALATED, IncidentStatus.AWAITING_APPROVAL)


# --- container drift ----------------------------------------------------------------
def test_healthy_container_has_no_drift() -> None:
    assert drift_of(live()) == []


@pytest.mark.parametrize(
    ("view", "kind", "action"),
    [
        (live(running=False, status="exited", exit_code=0), "container_stopped", "restart_service"),
        (
            live(running=False, status="exited", exit_code=137),
            "container_crashed",
            "restart_service",
        ),
        (live(paused=True, status="paused"), "container_paused", "restart_service"),
        (live(networks=[]), "network_detached", "restore_network"),
        (live(cpu_limit_cores=0.25), "cpu_limited", "remove_resource_limit"),
        (live(health="unhealthy"), "container_unhealthy", "restart_service"),
    ],
)
def test_each_container_drift_maps_to_its_fix(view: dict[str, Any], kind: str, action: str) -> None:
    items = drift_of(view)
    assert [item.kind for item in items] == [kind]
    assert items[0].fix is not None and items[0].fix.action_id == action
    assert items[0].fix.target == "cart"


def test_cpu_cap_at_host_cores_is_unlimited_not_drift() -> None:
    assert drift_of(live(cpu_limit_cores=4.0)) == []


def test_oom_loop_is_fixed_by_the_limit_not_a_restart() -> None:
    items = drift_of(
        live(
            running=False,
            restarting=True,
            status="restarting",
            exit_code=137,
            oom_killed=True,
            memory_limit_mb=12,
        )
    )
    kinds = {item.kind: item for item in items}
    assert set(kinds) == {"memory_limited", "container_restarting"}
    assert kinds["memory_limited"].fix is not None
    assert kinds["memory_limited"].fix.action_id == "remove_resource_limit"
    # The restart would only loop again under the same limit.
    assert kinds["container_restarting"].fix is None
    assert items[0].kind == "memory_limited"  # most causal first


def test_dependency_drift_explains_its_caller() -> None:
    items = container_drift(
        "valkey-cart",
        live(running=False, status="exited"),
        desired(service="valkey-cart"),
        unlimited_cpu=4,
        demo_network=NET,
    )
    assert items[0].explains == ["valkey-cart", "cart"]


def test_missing_container_has_no_automatic_fix() -> None:
    items = drift_of(None)
    assert items[0].kind == "container_missing" and items[0].fix is None


def test_flag_drift_names_the_owner_and_traffic_flags_stop_load() -> None:
    items = flag_drift(
        {"paymentFailure": "50%", "loadGeneratorFloodHomepage": "on", "adFailure": "off"},
        {"paymentFailure": "off", "loadGeneratorFloodHomepage": "off", "adFailure": "off"},
    )
    by_flag = {item.component: item for item in items}
    assert set(by_flag) == {"flag:paymentFailure", "flag:loadGeneratorFloodHomepage"}
    pay = by_flag["flag:paymentFailure"].fix
    assert pay is not None and (pay.action_id, pay.target) == ("clear_feature_flag", "payment")
    flood = by_flag["flag:loadGeneratorFloodHomepage"].fix
    assert flood is not None and flood.action_id == "stop_load_generator"


def test_load_drift_only_above_desired() -> None:
    assert load_drift(5, 5) == []
    assert load_drift(None, 5) == []
    assert load_drift(40, 5)[0].fix is not None


# --- desired state learning ---------------------------------------------------------
def test_learned_limits_are_a_high_water_mark(tmp_path: Any) -> None:
    store = DesiredStateStore(tmp_path / "d.json")
    assert store.learn_container("cart", live(memory_limit_mb=160), unlimited_cpu=4)
    # A lower limit later is drift, not a new desired value.
    store.learn_container("cart", live(memory_limit_mb=64, cpu_limit_cores=0.25), unlimited_cpu=4)
    entry = store.state.containers["cart"]
    assert entry.memory_limit_mb == 160 and entry.cpu_limit_cores is None
    # Raising a limit is never a fault.
    store.learn_container("cart", live(memory_limit_mb=320), unlimited_cpu=4)
    assert store.state.containers["cart"].memory_limit_mb == 320
    store.save()
    reloaded = DesiredStateStore(tmp_path / "d.json").load()
    assert reloaded.containers["cart"].memory_limit_mb == 320


def test_open_fault_pre_image_wins_and_live_limits_are_not_learned(tmp_path: Any) -> None:
    store = DesiredStateStore(tmp_path / "d.json")
    store.learn_container(
        "ad",
        live(memory_limit_mb=12),
        unlimited_cpu=4,
        pre_image={"cpu_limit_cores": None, "memory_limit_mb": 300, "memory_swap_mb": 600},
    )
    assert store.state.containers["ad"].memory_limit_mb == 300
    store.learn_container("email", live(memory_limit_mb=8), unlimited_cpu=4, limits=False)
    email = store.state.containers["email"]
    assert email.memory_limit_mb is None and not email.limits_learned
    # Limits not learned yet: a limit is not compared, so it is not "drift".
    assert (
        container_drift("email", live(memory_limit_mb=8), email, unlimited_cpu=4, demo_network=NET)
        == []
    )
    # The next healthy observation without a fault learns them.
    store.learn_container("email", live(memory_limit_mb=100), unlimited_cpu=4)
    assert store.state.containers["email"].memory_limit_mb == 100


# --- reconcile scope ----------------------------------------------------------------
def _report(*items: Any) -> DriftReport:
    from datetime import UTC, datetime

    return DriftReport(at=datetime.now(UTC), items=list(items))


def test_actions_only_fix_drift_in_their_scope() -> None:
    detached = drift_of(live(networks=[]))[0]
    limited = drift_of(live(cpu_limit_cores=0.25))[0]
    report = _report(detached, limited)
    assert relevant("restore_network", "cart", report) == [detached]
    assert relevant("remove_resource_limit", "cart", report) == [limited]
    assert relevant("restart_service", "cart", report) == []
    assert relevant("reconcile_service", "cart", report) == [detached, limited]
    assert relevant("reconcile_service", "ad", report) == []
    assert relevant("reconcile_all", "lab", report) == [detached, limited]


# --- ladder -------------------------------------------------------------------------
def test_ladder_puts_a_gated_diagnosis_first_then_related_drift() -> None:
    cache = container_drift(
        "valkey-cart",
        live(running=False, status="exited"),
        desired(service="valkey-cart"),
        unlimited_cpu=4,
        demo_network=NET,
    )[0]
    other = flag_drift({"adFailure": "on"}, {"adFailure": "off"})[0]
    ladder = build(
        LadderInput(
            focus=["cart", "frontend"],
            root="cart",
            recommended={"action_id": "restart_service", "target": "cart"},
            passes_gate=True,
            confidence=0.8,
            drift=[other, cache],
        )
    )
    keys = [c.key for c in ladder]
    assert keys[0] == ("restart_service", "cart")
    assert keys[1] == ("restart_service", "valkey-cart")  # explains cart
    assert keys.index(("clear_feature_flag", "ad")) > 1  # unrelated drift later
    assert keys[-1] == ("reconcile_all", "lab")


def test_ladder_skips_attempts_and_demotes_a_low_confidence_recommendation() -> None:
    flag = flag_drift({"paymentFailure": "100%"}, {"paymentFailure": "off"})[0]
    ladder = build(
        LadderInput(
            focus=["payment", "checkout"],
            root="payment",
            recommended={"action_id": "restart_service", "target": "payment"},
            passes_gate=False,
            confidence=0.4,
            drift=[flag],
            attempted={("clear_feature_flag", "payment")},
        )
    )
    keys = [c.key for c in ladder]
    assert ("clear_feature_flag", "payment") not in keys
    assert keys[0] == ("restart_service", "payment")
    assert ladder[0].source == "diagnosis"


def test_ladder_without_drift_falls_back_to_a_restart_only() -> None:
    ladder = build(LadderInput(focus=["email"], root="email"))
    assert [c.key for c in ladder] == [("restart_service", "email")]


def test_ladder_uses_recorded_faults() -> None:
    ladder = build(LadderInput(focus=["payment"], faults=[("payment", "payment_failure")]))
    assert ladder[0].key == ("reset_fault", "payment")


# --- planner ------------------------------------------------------------------------
class _FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def chat(self, messages: Any, **_kwargs: Any) -> Any:
        from app.models.llm.base import ChatResponse, Usage

        return ChatResponse(
            provider="fake", model="fake-1", content=self.reply, usage=Usage(tokens_in=10)
        )


class _FakeGateway:
    def __init__(self, reply: str) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(
            llm=SimpleNamespace(provider="zenmux", json_mode=True, timeout_s=30)
        )
        self.llm_ready = True
        self._llm = _FakeLLM(reply)

    def llm(self) -> _FakeLLM:
        return self._llm


def _ladder() -> list[Any]:
    return build(
        LadderInput(
            focus=["payment"],
            faults=[("payment", "payment_failure")],
            root="payment",
        )
    )


async def test_llm_planner_picks_from_the_list() -> None:
    candidates = _ladder()
    planner = RemediationPlanner(_FakeGateway(json.dumps({"choice": 1, "rationale": "why"})))  # type: ignore[arg-type]
    choice = await planner.choose(candidates, incident={}, diagnosis=None, drift=[], attempts=[])
    assert (choice.index, choice.by, choice.rationale) == (1, "llm", "why")


@pytest.mark.parametrize("reply", ['{"choice": 9}', '{"choice": "x"}', "not json"])
async def test_llm_planner_falls_back_when_out_of_bounds(reply: str) -> None:
    candidates = _ladder()
    planner = RemediationPlanner(_FakeGateway(reply))  # type: ignore[arg-type]
    choice = await planner.choose(candidates, incident={}, diagnosis=None, drift=[], attempts=[])
    assert choice.index == 0 and choice.by == "deterministic" and choice.error


def test_deterministic_choice_explains_itself() -> None:
    choice = deterministic(_ladder(), [{"action_id": "x"}])
    assert choice.index == 0 and "after 1 failed attempt" in choice.rationale
    assert deterministic([], []).index == -1


# --- drift-aware investigator -------------------------------------------------------
CONTEXT = {
    "incident": {"id": "INC-0009", "primary_service": "cart", "signals": [{"service": "cart"}]},
    "suspects": [
        {
            "rank": 1,
            "service": "cart",
            "score": 0.7,
            "signals": {"error_origin": {"contribution": 0.5}},
            "evidence_codes": ["E0001"],
        },
        {
            "rank": 2,
            "service": "frontend",
            "score": 0.3,
            "signals": {"error_origin": {"contribution": 0.2}},
            "evidence_codes": ["E0002"],
        },
    ],
    "services": ["cart", "frontend", "valkey-cart"],
    "fault_targets": [],
}


def test_stub_blames_a_drifted_dependency_over_its_caller() -> None:
    results = [
        (
            "get_drift",
            {
                "evidence_code": "E0042",
                "items": [
                    {
                        "kind": "container_stopped",
                        "service": "valkey-cart",
                        "fault_class": "dependency_unavailable",
                        "explains": ["valkey-cart", "cart"],
                        "fix": {"action_id": "restart_service", "target": "valkey-cart"},
                    }
                ],
            },
        )
    ]
    diagnosis = diagnose(CONTEXT, results)
    assert diagnosis["root_cause"] == {
        "service": "valkey-cart",
        "fault_class": "dependency_unavailable",
    }
    assert diagnosis["recommended_action"] == {
        "action_id": "restart_service",
        "target": "valkey-cart",
    }
    assert "E0042" in diagnosis["evidence_codes"]
    assert diagnosis["affected_services"][:2] == ["valkey-cart", "cart"]
    shown = {"E0001", "E0002", "E0042"}
    report = validate(
        diagnosis,
        ValidationContext(
            incident_codes=shown,
            shown_codes=shown,
            evidence_data={code: {} for code in shown},
            topology_services={"cart", "frontend", "valkey-cart"},
            known_services=set(ALLOWED_TARGETS) | {"frontend"},
            fault_targets={"valkey-cart"},
            flag_targets=set(),
            threshold=0.7,
        ),
    )
    assert report.valid, report.errors
    assert report.passes_gate


def test_stub_uses_drift_when_no_suspect_ranked() -> None:
    results = [
        (
            "get_drift",
            {
                "evidence_code": "E0005",
                "items": [
                    {
                        "kind": "flag_on",
                        "service": "ad",
                        "fault_class": "application_error",
                        "explains": ["ad"],
                        "fix": {"action_id": "clear_feature_flag", "target": "ad"},
                    }
                ],
            },
        )
    ]
    diagnosis = diagnose({**CONTEXT, "suspects": []}, results)
    assert diagnosis["root_cause"]["service"] == "ad"
    assert diagnosis["recommended_action"] == {"action_id": "clear_feature_flag", "target": "ad"}


def test_targeted_flag_effective_variant_follows_the_match_branch() -> None:
    from app.faults.flagd import effective_variant, targeted_rule

    flag = {
        "defaultVariant": "off",
        "variants": {"off": False, "on": True},
        "targeting": {"if": [{"==": [{"var": "product_id"}, "OLJCESPC7Z"]}, "off", "off"]},
    }
    assert targeted_rule(flag) == ("product_id", "OLJCESPC7Z")
    assert effective_variant(flag) == "off"
    flag["targeting"]["if"][1] = "on"
    assert effective_variant(flag) == "on"
    assert targeted_rule({"defaultVariant": "on"}) is None
    assert effective_variant({"defaultVariant": "on"}) == "on"
