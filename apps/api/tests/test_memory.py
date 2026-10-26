"""P12: memory template, hybrid scoring parts, and the memory-is-never-evidence guardrails."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.schemas import Diagnosis
from app.agent.validator import ValidationContext, validate
from app.db.models import Evidence, Incident
from app.memory.service import MemoryHit, hypothesis, lexical_overlap, recency_weight
from app.memory.summarizer import build_facts, container_phrase, onset_phrase, query_text

OPENED = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)


def incident() -> Incident:
    return Incident(
        id=7,
        public_id="INC-00007",
        title="checkout error rate",
        status="RESOLVED",
        primary_service="checkout",
        trigger={
            "primary_signal": "error_rate",
            "signals": [
                {"service": "checkout", "signal": "error_rate"},
                {"service": "frontend-web", "signal": "error_rate"},
            ],
        },
        opened_at=OPENED,
    )


def row(code: str, type_: str, service: str | None, data: dict[str, Any]) -> Evidence:
    return Evidence(incident_id=7, code=code, type=type_, service=service, data=data)


EVIDENCE = [
    row("E0001", "onset", "payment", {"first_anomalous_span_at": OPENED.isoformat()}),
    row(
        "E0002",
        "onset",
        "checkout",
        {"first_anomalous_span_at": (OPENED + timedelta(seconds=4)).isoformat()},
    ),
    row("E0003", "container_state", "payment", {"state": "running", "networks": []}),
    row("E0004", "container_state", "email", {"state": "exited"}),
]


def test_template_matches_plan_shape() -> None:
    facts = build_facts(
        incident(),
        EVIDENCE,
        root="payment",
        fault_class="network",
        action={"action_id": "restore_network", "target": "payment"},
        affected=["payment", "checkout", "frontend-web"],
        verification="symptoms cleared and the detector resolved it",
    )
    lines = facts.text().splitlines()
    assert lines[0] == "payment. network. error rate breach at checkout with frontend."
    assert lines[1] == "Onset: payment errors preceded checkout by 4s."
    assert lines[2] == "Container: detached. Restart: not attempted."
    assert lines[3].startswith("Action: restore_network on payment. Verification: ")
    assert facts.services == ["payment", "checkout", "frontend"]
    assert not re.search(r"\bE\d{4}\b", facts.text())


def test_container_and_onset_phrases() -> None:
    assert container_phrase("email", EVIDENCE) == "crashed"
    assert container_phrase("cart", EVIDENCE) == "not observed"
    assert onset_phrase("checkout", EVIDENCE) == "payment errors preceded checkout"
    assert onset_phrase("cart", EVIDENCE) == "no onset measured for cart"


def test_limits_and_infrastructure_do_not_mislead() -> None:
    rows = [
        row("E0010", "container_state", "ad", {"state": "running", "cpu_limit_cores": 24.0}),
        row("E0011", "container_state", "cart", {"state": "running", "memory_limit_mb": 160}),
        row("E0012", "container_state", "quote", {"state": "running", "cpu_limit_cores": 0.25}),
        row("E0013", "onset", "flagd", {"first_anomalous_span_at": OPENED.isoformat()}),
        row("E0014", "onset", "cart", {"first_anomalous_span_at": OPENED.isoformat()}),
        row("E0015", "onset", "frontend", {"first_anomalous_span_at": OPENED.isoformat()}),
    ]
    assert container_phrase("ad", rows) == "healthy"
    assert container_phrase("cart", rows) == "healthy"
    assert container_phrase("quote", rows) == "limited"
    assert onset_phrase("cart", rows) == "cart and frontend errors started together"


def test_query_reads_like_a_memory() -> None:
    query = query_text(incident(), EVIDENCE, lead="payment")
    assert query.splitlines()[0] == "payment. error rate breach at checkout with frontend."
    assert query.endswith("Container: detached.")


def test_hybrid_score_parts() -> None:
    assert lexical_overlap("payment network silence", "payment network detached") == 0.5
    assert lexical_overlap("", "x") == 0.0
    now = datetime.now(UTC)
    assert recency_weight(now, now) == 1.0
    assert abs(recency_weight(now - timedelta(days=7), now) - 0.5) < 1e-6


def hit() -> MemoryHit:
    return MemoryHit(
        memory_id=3,
        incident_ref="INC-00003",
        root_cause="payment",
        fault_class="network",
        summary="payment. network. telemetry silence at payment.",
        services=["payment"],
        seed=False,
        cosine=0.8,
        lexical=0.4,
        recency=1.0,
        score=0.76,
        created_at=datetime.now(UTC),
    )


def test_hypothesis_carries_no_evidence_codes() -> None:
    item = hypothesis(hit())
    assert set(item) == {"past_incident", "root_cause", "fault_class", "similarity", "summary"}
    assert not re.search(r"\bE\d{4}\b", str(item))


def test_memory_cannot_be_cited_as_evidence() -> None:
    ctx = ValidationContext(
        incident_codes={"E0001"},
        shown_codes={"E0001"},
        evidence_data={"E0001": {}},
        topology_services={"payment"},
        known_services={"payment"},
        fault_targets={"payment"},
    )
    base = {
        "root_cause": {"service": "payment", "fault_class": "network"},
        "confidence": 0.9,
        "summary": "payment was detached.",
        "affected_services": ["payment"],
        "recommended_action": {"action_id": "reset_fault", "target": "payment"},
    }
    # A past incident id or memory id in the evidence list is not an E#### code.
    for cited in (["INC-00003"], ["M3"], ["seed-3"]):
        report = validate({**base, "evidence_codes": cited}, ctx)
        assert not report.valid
        assert "not an E#### code" in report.errors[0]
    # And there is no field to smuggle memories through.
    report = validate({**base, "evidence_codes": ["E0001"], "memories": ["INC-00003"]}, ctx)
    assert any("schema: memories" in error for error in report.errors)
    assert "memories" not in Diagnosis.model_fields
