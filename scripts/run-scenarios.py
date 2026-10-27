"""Run the deterministic-analysis scenarios against the live stack.

    uv run --project apps/api python scripts/run-scenarios.py
    uv run --project apps/api python scripts/run-scenarios.py --only S1,S6
    uv run --project apps/api python scripts/run-scenarios.py --agent --resolve

For each scenario: reset → settle → inject → wait for the detector to open exactly
one incident → run the deterministic analysis → record the top-1 suspect →
revert → close the incident → settle.

The gate from PLAN §15 is top-1 == injected target in at least 7 of 8 scenarios.

`--agent` also runs the P11 investigation (`/investigate?wait=true`) and checks
every cited evidence code against the incident's own evidence list. `--resolve`
lets the detector resolve the incident after the revert (DIAGNOSED → RESOLVED,
the input for P12 memory) instead of closing it by hand. `--history FILE` (a
previous result file) checks P12 retrieval: before investigating, the new
incident's memory search must rank the same scenario's earlier incident top-3.
`--remediate` (P15) approves the auto-planned proposal, lets the executor clear
the fault and the verifier decide (PASSED → RESOLVED), then checks the report;
scenario W1 replaces the proposal with a deliberately wrong action that must
FAIL verification and escalate.
The JSON result is written to `runtime/scenarios.json`; the console prints the
scoreboard. The stack is left clean (no faults, no open incidents).

Two PLAN §17 scenarios are substituted (see SUBSTITUTIONS below).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import _http
import httpx

API = "http://127.0.0.1:8001"
OFREP = "http://127.0.0.1:8016"
TIMEOUT = httpx.Timeout(180.0, connect=5.0)
RESULTS_FILE = "runtime/scenarios.json"

DETECT_TIMEOUT_S = 240
SETTLE_S = 900
# Must cover the analyzer's trace lookback (PRE_OPEN_S = 240 s before opening)
# so the previous fault's traces are out of the next analysis; it also drains
# the detector's 2-minute rate window.
QUIET_S = 240
# Mirrors DETECTOR_WARMUP_S (apps/api/app/config.py); the API does not expose it.
WARMUP_S = 300
# After a revert the detector needs its recovery window plus flagd propagation.
RESOLVE_TIMEOUT_S = 420


# --remediate: the verifier's own timeout (VERIFICATION_TIMEOUT_S) plus margin
# for the approval, the execution and the report.
VERIFY_TIMEOUT_S = 540
APPROVAL_WAIT_S = 60


@dataclass
class Scenario:
    id: str
    name: str
    fault: dict[str, Any]
    expected: str
    note: str = ""
    # --remediate only: replace the recommendation with this (action, target)
    # and expect this verdict. A deliberately wrong action must FAIL.
    override: tuple[str, str] | None = None
    expect_verdict: str = "PASSED"
    remediate_only: bool = False


SCENARIOS = [
    Scenario(
        "S1",
        "payment failure",
        {"fault_id": "payment_failure", "params": {"variant": "100%"}},
        "payment",
    ),
    Scenario(
        "S2",
        "catalog lock contention",
        {"fault_id": "catalog_lock_contention", "params": {"variant": "on"}},
        "product-catalog",
        note="the only ERROR is the proxy's 15 s timeout; the delay is product-catalog's DB call",
    ),
    Scenario(
        "S3",
        "payment unreachable",
        {"fault_id": "payment_unreachable", "params": {"variant": "on"}},
        "payment",
        note="enforced in checkout (badAddress:50051), so the symptom is a silent peer",
    ),
    Scenario(
        "S4",
        "slow image loading",
        {"fault_id": "image_slow_load", "params": {"variant": "5sec"}},
        "frontend",
        note="the delay is applied by the frontend's ProductCard",
    ),
    Scenario(
        "S5",
        "email service down",
        {"fault_id": "service_down", "target": "email"},
        "email",
    ),
    Scenario(
        "S6",
        "ad CPU pressure",
        {"fault_id": "cpu_pressure", "target": "ad", "params": {"cpus": 0.25}},
        "ad",
        note="ad uses 1.3 % CPU at this cap; the limit itself is the only symptom",
    ),
    Scenario(
        "S7",
        "payment network partition",
        {"fault_id": "network_partition", "target": "payment"},
        "payment",
    ),
    Scenario(
        "S8",
        "traffic flood",
        {"fault_id": "traffic_flood", "params": {"variant": "on"}},
        "frontend",
        note="catalog target is frontend; PLAN §17 expected frontend-proxy",
    ),
    # M4: restarting payment does not clear a feature-flag fault, so the
    # verification must fail and the incident escalate.
    Scenario(
        "W1",
        "payment failure, wrong action",
        {"fault_id": "payment_failure", "params": {"variant": "100%"}},
        "payment",
        note="operator replaces reset_fault with restart_service payment",
        override=("restart_service", "payment"),
        expect_verdict="FAILED",
        remediate_only=True,
    ),
]

# Two PLAN §17 entries were measured to be unobservable at the demo's default
# load and are therefore not part of the gate:
#   - `cart_failure` only fails EmptyCart (CartService.cs:82), which the load
#     generator almost never calls; the cart's `readiness_failure` changes a
#     probe Docker never runs (`docker inspect cart` has no healthcheck), so
#     neither produces a symptom.
#   - `recommendation_cache_failure` produced no latency or error change in
#     120 s (p95 actually fell from 960 to 300 ms).
# Their substitutes exercise the same detector signals: a 100 % silent-peer
# error (S3) and a 5 s injected latency on the frontend (S4).
SUBSTITUTIONS = {
    "cart_failure": "payment_unreachable",
    "recommendation_cache_failure": "image_slow_load",
}


@dataclass
class Result:
    id: str
    name: str
    expected: str
    incident: str | None = None
    primary_service: str | None = None
    detected_s: float | None = None
    top: str | None = None
    score: float | None = None
    ranking: list[dict[str, Any]] = field(default_factory=list)
    evidence: int = 0
    error: str | None = None
    note: str = ""
    agent: dict[str, Any] | None = None
    final_status: str | None = None
    memory: dict[str, Any] | None = None
    remediation: dict[str, Any] | None = None
    # --remediate: injection → incident closed by the verifier.
    recovered_s: float | None = None

    @property
    def passed(self) -> bool:
        return self.top == self.expected and not self.error

    @property
    def remediation_passed(self) -> bool:
        return bool((self.remediation or {}).get("ok"))

    @property
    def agent_passed(self) -> bool:
        agent = self.agent or {}
        return (
            agent.get("status") == "DIAGNOSED"
            and agent.get("root_cause") == self.expected
            and agent.get("invalid_codes") == []
            and int(agent.get("tool_calls") or 0) <= 20
        )


def _get(client: httpx.Client, path: str) -> httpx.Response:
    last: Exception | None = None
    for _ in range(4):
        try:
            return client.get(f"{API}{path}")
        except httpx.TransportError as exc:
            last = exc
            time.sleep(1.0)
    raise last if last is not None else RuntimeError("GET failed")


def _post(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    last: Exception | None = None
    for _ in range(3):
        try:
            return client.post(f"{API}{path}", **kwargs)
        except httpx.ReadTimeout as exc:
            # Injections and analyses can take longer than the read timeout;
            # retrying is safe because the fault API rejects duplicates.
            last = exc
            time.sleep(1.0)
    raise last if last is not None else RuntimeError("POST failed")


def _delete(client: httpx.Client, path: str) -> httpx.Response:
    last: Exception | None = None
    for _ in range(3):
        try:
            return client.delete(f"{API}{path}")
        except httpx.TransportError as exc:
            last = exc
            time.sleep(1.0)
    raise last if last is not None else RuntimeError("DELETE failed")


def reset(client: httpx.Client) -> None:
    _post(client, "/api/faults/reset-all?actor=scenarios")
    for incident in _get(client, "/api/incidents?status=open&limit=20").json().get("items", []):
        _post(
            client,
            f"/api/incidents/{incident['public_id']}/close?actor=scenarios&note=scenario_reset",
        )


def settle(client: httpx.Client, seconds: float) -> None:
    """Wait until the previous fault's incident and rate window have drained.

    A leftover incident would make the next fault's breaches attach to it as
    signals instead of opening a new incident, and the runner would report
    "no incident" while the detector is behaving correctly.
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        open_now = _get(client, "/api/incidents?status=open&limit=20").json().get("items", [])
        if not open_now:
            break
        for incident in open_now:
            _post(
                client,
                f"/api/incidents/{incident['public_id']}/close?actor=scenarios&note=scenario_settle",
            )
        time.sleep(5)
    # Quiet must *hold* for QUIET_S: right after a revert nothing may be
    # breaching yet the previous fault's errors are still inside the detector's
    # 2-minute rate window and the analyzer's trace lookback. Injecting then let
    # a stopped email container explain the next three scenarios.
    deadline = time.monotonic() + seconds
    quiet_since = time.monotonic()
    noisy: set[str] = set()
    while time.monotonic() < deadline:
        status = _get(client, "/api/detection/status").json()
        open_now = _get(client, "/api/incidents?status=open&limit=20").json().get("items", [])
        for incident in open_now:
            noisy.add(f"{incident['public_id']}:{incident['primary_service']}")
            _post(
                client,
                f"/api/incidents/{incident['public_id']}/close?actor=scenarios&note=scenario_settle",
            )
        # A streak still below its debounce counts too: S3 was once injected
        # into a checkout latency streak that opened as an incident 12 s later.
        # One noisy sample (consecutive 1) does not reset the quiet timer.
        breaches = (status.get("breaches") or []) + [
            item for item in status.get("pending") or [] if item["consecutive"] >= 2
        ]
        noisy.update(f"{item['service']}:{item['signal']}" for item in breaches)
        if breaches or open_now:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= QUIET_S:
            return
        time.sleep(5)
    print(
        f"  warning: not quiet for {QUIET_S}s within {seconds:.0f}s; injecting anyway "
        f"(seen: {', '.join(sorted(noisy))})"
    )


def wait_for_warmup(client: httpx.Client) -> None:
    """Block until the detector has left its warm-up gate.

    After an API restart the detector refuses to open incidents for
    DETECTOR_WARMUP_S; a fault injected inside that gate is detected late or
    attaches to whatever opened when the gate lifted.
    """
    while True:
        status = _get(client, "/api/detection/status").json()
        watched = int(status.get("cycles", 0)) * int(status.get("interval_s", 10))
        if watched >= WARMUP_S:
            return
        print(f"detector warm-up: {watched}/{WARMUP_S}s")
        time.sleep(min(30.0, WARMUP_S - watched + 5))


def investigate(client: httpx.Client, ref: str) -> dict[str, Any]:
    response = _post(client, f"/api/incidents/{ref}/investigate?wait=true&actor=scenarios")
    if response.status_code not in (200, 202):
        return {"error": f"investigate {response.status_code}: {response.text[:200]}"}
    run = response.json()
    diagnosis = run.get("diagnosis") or {}
    known = {row["code"] for row in _get(client, f"/api/incidents/{ref}/evidence").json()}
    cited = diagnosis.get("evidence_codes") or []
    return {
        "run_id": run["id"],
        "status": run["status"],
        "error": run.get("error"),
        "root_cause": diagnosis.get("root_cause_service"),
        "fault_class": diagnosis.get("fault_class"),
        "confidence": diagnosis.get("confidence"),
        "valid": diagnosis.get("valid"),
        "validation_errors": diagnosis.get("validation_errors") or [],
        "action": diagnosis.get("recommended_action"),
        "cited": cited,
        # Independent of the validator: every cited code must be this incident's.
        "invalid_codes": [code for code in cited if code not in known],
        "tool_calls": run.get("tool_calls"),
        "llm_calls": run.get("llm_calls"),
        "latency_ms": run.get("latency_ms"),
        "cost_usd": run.get("cost_usd"),
        "model": run.get("model"),
    }


def memory_check(client: httpx.Client, ref: str, previous: list[str]) -> dict[str, Any]:
    response = _get(client, f"/api/memory/search?incident={ref}&k=5")
    if response.status_code != 200:
        return {"error": f"search {response.status_code}: {response.text[:200]}"}
    hits = response.json().get("hits") or []
    refs = [hit.get("incident_ref") for hit in hits]
    rank = next((index + 1 for index, item in enumerate(refs) if item in previous), None)
    return {
        "previous": previous,
        "rank": rank,
        "top3": rank is not None and rank <= 3,
        "hits": [
            {
                "incident": hit.get("incident_ref") or f"seed-{hit.get('memory_id')}",
                "root_cause": hit.get("root_cause"),
                "score": hit.get("score"),
            }
            for hit in hits
        ],
    }


def wait_resolved(client: httpx.Client, ref: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    status = ""
    while time.monotonic() < deadline:
        status = _get(client, f"/api/incidents/{ref}").json()["status"]
        if status in ("RESOLVED", "ESCALATED", "FAILED"):
            return status
        time.sleep(5)
    return status


def _pending(client: httpx.Client, ref: str) -> dict[str, Any] | None:
    items = _get(client, f"/api/incidents/{ref}/approvals").json().get("items", [])
    return next((item for item in items if item["status"] == "PENDING"), None)


def report_check(client: httpx.Client, ref: str) -> dict[str, Any]:
    """The M4 report gate: timeline + cited evidence + before/after, both formats."""
    response = _get(client, f"/api/incidents/{ref}/report")
    if response.status_code != 200:
        return {"ok": False, "error": f"report {response.status_code}"}
    report = response.json()
    verified = [r["verification"] for r in report.get("remediation") or [] if r["verification"]]
    last = verified[-1] if verified else {}
    before = (last.get("before") or {}).get("metrics") or {}
    after = (last.get("after") or {}).get("metrics") or {}
    md = _get(client, f"/api/incidents/{ref}/report.md?download=true")
    text = md.text if md.status_code == 200 else ""
    checks = {
        "timeline": len(report.get("timeline") or []),
        "cited_evidence": len((report.get("evidence") or {}).get("cited") or []),
        "cited_with_data": all(
            "data" in row for row in (report.get("evidence") or {}).get("cited") or []
        ),
        "missing_citations": (report.get("evidence") or {}).get("missing_citations"),
        "criteria": len((last.get("criteria") or {}).get("final") or []),
        "before_after_services": sorted(set(before) & set(after)),
        "markdown_bytes": len(text.encode("utf-8")),
        "markdown_attachment": "attachment" in md.headers.get("content-disposition", ""),
        "markdown_sections": all(
            section in text
            for section in ("## Timeline", "### Cited evidence", "#### Verification", "→")
        ),
    }
    checks["ok"] = bool(
        checks["timeline"]
        and checks["cited_evidence"]
        and checks["cited_with_data"]
        and not checks["missing_citations"]
        and checks["criteria"]
        and checks["before_after_services"]
        and checks["markdown_attachment"]
        and checks["markdown_sections"]
    )
    return checks


def remediate(client: httpx.Client, ref: str, scenario: Scenario) -> dict[str, Any]:
    """Approve the proposal (or the override), then wait for the verifier."""
    out: dict[str, Any] = {"ok": False, "expect_verdict": scenario.expect_verdict}
    started = time.monotonic()
    approval = None
    deadline = time.monotonic() + APPROVAL_WAIT_S
    while time.monotonic() < deadline and approval is None:
        approval = _pending(client, ref)
        if approval is None:
            time.sleep(3)
    if approval is None:
        status = _get(client, f"/api/incidents/{ref}").json()["status"]
        out["error"] = f"no pending approval (incident {status})"
        return out
    out["proposed"] = f"{approval['action']['action_id']} on {approval['action']['target']}"
    if scenario.override:
        action_id, target = scenario.override
        response = _post(
            client,
            f"/api/incidents/{ref}/approvals",
            json={
                "action_id": action_id,
                "target": target,
                "replace": True,
                "actor": "scenarios",
                "note": scenario.note or "scenario override",
            },
        )
        if response.status_code != 201 or not (response.json() or {}).get("approval"):
            out["error"] = f"override {response.status_code}: {response.text[:200]}"
            return out
        approval = response.json()["approval"]
    response = _post(
        client,
        f"/api/approvals/{approval['id']}/approve?wait=true",
        json={"actor": "scenarios", "note": "scenario runner"},
    )
    if response.status_code != 200:
        out["error"] = f"approve {response.status_code}: {response.text[:200]}"
        return out
    decided = response.json()
    action = decided["action"]
    out.update(
        approval_id=approval["id"],
        action=f"{action['action_id']} on {action['target']}",
        action_status=action["status"],
        token_used=decided.get("token_used"),
        executed_s=round(time.monotonic() - started, 1),
    )
    out["final_status"] = wait_resolved(client, ref, VERIFY_TIMEOUT_S)
    out["remediate_s"] = round(time.monotonic() - started, 1)
    verifications = _get(client, f"/api/incidents/{ref}/verifications").json().get("items", [])
    if verifications:
        last = verifications[-1]
        failing = [c["id"] for c in last["criteria"].get("final", []) if not c.get("ok")]
        out.update(
            verdict=last["verdict"],
            observed_for_s=last["observed_for_s"],
            failing=failing,
            rollback=(last["criteria"].get("rollback") or {}).get("detail"),
        )
    out["report"] = report_check(client, ref)
    expected_final = "RESOLVED" if scenario.expect_verdict == "PASSED" else "ESCALATED"
    out["ok"] = (
        out.get("verdict") == scenario.expect_verdict
        and out["final_status"] == expected_final
        and bool(out["report"].get("ok"))
    )
    return out


def run_one(
    client: httpx.Client,
    scenario: Scenario,
    *,
    agent: bool = False,
    resolve: bool = False,
    previous: list[str] | None = None,
    remediate_flow: bool = False,
) -> Result:
    result = Result(
        id=scenario.id,
        name=scenario.name,
        expected=scenario.expected,
        note=scenario.note,
    )
    reset(client)
    settle(client, SETTLE_S)
    baseline = _get(client, "/api/incidents?limit=200")
    if baseline.status_code != 200:
        # The API answers 503 when Docker is wedged; record it, do not crash the suite.
        result.error = f"incidents {baseline.status_code}: {baseline.text[:200]}"
        return result
    baseline_total = baseline.json()["total"]

    payload = dict(scenario.fault)
    payload.update({"ttl_s": 480, "actor": "scenarios"})
    started = time.monotonic()
    started_at = datetime.now(UTC)
    response = _post(client, "/api/faults", json=payload)
    if response.status_code != 201:
        result.error = f"inject {response.status_code}: {response.text[:200]}"
        return result
    fault_id = response.json()["fault"]["id"]

    incident: dict[str, Any] | None = None
    deadline = time.monotonic() + DETECT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(4)
        open_now = _get(client, "/api/incidents?status=open&limit=20").json().get("items", [])
        fresh = [
            item
            for item in open_now
            if datetime.fromisoformat(item["opened_at"].replace("Z", "+00:00")) >= started_at
        ]
        if fresh:
            incident = sorted(fresh, key=lambda item: item["opened_at"])[0]
            result.detected_s = time.monotonic() - started
            break
    if incident is None:
        result.error = f"no incident within {DETECT_TIMEOUT_S}s"
        _delete(client, f"/api/faults/{fault_id}?actor=scenarios")
        return result
    result.incident = incident["public_id"]
    result.primary_service = incident["primary_service"]
    analysis = _post(client, f"/api/incidents/{incident['public_id']}/analyze")
    if analysis.status_code != 200:
        result.error = f"analyze {analysis.status_code}: {analysis.text[:200]}"
    else:
        body = analysis.json()
        result.top = body.get("top_suspect")
        result.ranking = [
            {"service": item["service"], "score": item["score"]}
            for item in (body.get("suspects") or [])
        ]
        result.score = result.ranking[0]["score"] if result.ranking else None
        result.evidence = body.get("evidence_total", 0)
    if previous:
        result.memory = memory_check(client, incident["public_id"], previous)
    if agent or remediate_flow:
        result.agent = investigate(client, incident["public_id"])
    if remediate_flow:
        # The approved action must clear the fault itself; the revert below is
        # only cleanup (idempotent) for a wrong or failed action.
        result.remediation = remediate(client, incident["public_id"], scenario)
        result.final_status = result.remediation.get("final_status")
        if result.final_status in ("RESOLVED", "ESCALATED"):
            result.recovered_s = round(time.monotonic() - started, 1)

    _delete(client, f"/api/faults/{fault_id}?actor=scenarios")
    if resolve and not remediate_flow:
        result.final_status = wait_resolved(client, incident["public_id"], RESOLVE_TIMEOUT_S)
    if result.final_status not in ("RESOLVED", "ESCALATED", "FAILED"):
        _post(
            client,
            f"/api/incidents/{incident['public_id']}/close?actor=scenarios&note=scenario_done",
        )
        result.final_status = _get(client, f"/api/incidents/{incident['public_id']}").json()[
            "status"
        ]
    total_after = _get(client, "/api/incidents?limit=200").json()["total"]
    if total_after != baseline_total + 1:
        result.note = (
            result.note + " | " if result.note else ""
        ) + f"{total_after - baseline_total} incidents for this fault"
    return result


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def overall(item: Result) -> bool:
    """Top-1 plus, when they ran, the agent and remediation checks."""
    return (
        item.passed
        and (item.agent is None or item.agent_passed)
        and (item.remediation is None or item.remediation_passed)
    )


def _fmt(value: Any, spec: str = "") -> str:
    if value is None or value == "":
        return "—"
    return format(value, spec) if spec else str(value)


def scoreboard(results: list[Result], stats: dict[str, Any] | None, started_at: str) -> str:
    """The P16 matrix: one row per scenario, then cost/latency totals."""
    lines = [
        "# DocOck scenario scoreboard",
        "",
        f"Run started {started_at}; generated {datetime.now(UTC).isoformat(timespec='seconds')}.",
        "",
        "| Scenario | Expected | Detect s | Top-1 | Agent root (conf) | Tools | Agent ms "
        "| Cost $ | Action | Verdict | Verify s | Recover s | Final | Report | Pass |",
        "|---|---|---:|---|---|---:|---:|---:|---|---|---:|---:|---|---|---|",
    ]
    for item in results:
        agent = item.agent or {}
        rem = item.remediation or {}
        root = agent.get("root_cause")
        conf = agent.get("confidence")
        report = rem.get("report")
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{item.id} {item.name}",
                    item.expected,
                    _fmt(item.detected_s, ".0f"),
                    f"{item.top or '—'} ({'hit' if item.passed else 'miss'})",
                    f"{root} ({conf})" if root else "—",
                    _fmt(agent.get("tool_calls")),
                    _fmt(agent.get("latency_ms")),
                    _fmt(agent.get("cost_usd")),
                    _fmt(rem.get("action")),
                    f"{_fmt(rem.get('verdict'))} (want {rem.get('expect_verdict')})"
                    if rem
                    else "—",
                    _fmt(rem.get("observed_for_s")),
                    _fmt(item.recovered_s, ".0f"),
                    _fmt(item.final_status),
                    ("ok" if report.get("ok") else "incomplete") if report else "—",
                    "PASS" if overall(item) else "FAIL",
                ]
            )
            + " |"
        )
    passed = sum(1 for item in results if overall(item))
    agent_runs = [item for item in results if item.agent is not None]
    latencies = sorted(
        int(item.agent["latency_ms"])
        for item in agent_runs
        if item.agent and item.agent.get("latency_ms")
    )
    cost = sum(float((item.agent or {}).get("cost_usd") or 0) for item in agent_runs)
    detect = sorted(item.detected_s for item in results if item.detected_s is not None)
    recover = sorted(item.recovered_s for item in results if item.recovered_s is not None)
    lines += [
        "",
        f"**Score: {passed}/{len(results)} scenarios pass every check** "
        f"(top-1 {sum(1 for item in results if item.passed)}/{len(results)}"
        + (
            f", agent {sum(1 for item in agent_runs if item.agent_passed)}/{len(agent_runs)}"
            if agent_runs
            else ""
        )
        + (
            f", remediation {sum(1 for item in results if item.remediation_passed)}"
            f"/{sum(1 for item in results if item.remediation is not None)}"
            if any(item.remediation is not None for item in results)
            else ""
        )
        + ").",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Detection time p50 / max | {_span(detect, 's')} |",
        f"| Recovery time (inject → closed) p50 / max | {_span(recover, 's')} |",
        f"| Agent latency p50 / max | {_span(latencies, 'ms')} |",
        f"| Agent cost (this run) | ${cost:.4f} over {len(agent_runs)} run(s) |",
    ]
    if stats:
        agent_stats = stats.get("agent") or {}
        limits = stats.get("rate_limits") or {}
        lines += [
            f"| LLM spend today / daily cap | ${agent_stats.get('spent_today_usd', 0):.4f} / "
            f"${agent_stats.get('daily_budget_usd', 0):.2f} |",
            "| Models | "
            + ", ".join(
                f"{m['provider']}/{m['model']} ×{m['runs']}" for m in agent_stats.get("models", [])
            )
            + " |",
            "| Verification verdicts (window) | "
            + (
                ", ".join(
                    f"{verdict} {count}"
                    for verdict, count in (
                        (stats.get("remediation") or {}).get("verdicts") or {}
                    ).items()
                )
                or "none"
            )
            + " |",
            f"| Rate limits | {'on' if limits.get('enabled') else 'off'}, "
            f"{limits.get('rejected', 0)} rejected |",
            f"| Open incidents after the run | {(stats.get('incidents') or {}).get('open')} |",
        ]
    return "\n".join(lines) + "\n"


def _span(values: list[float] | list[int], unit: str) -> str:
    """Median (lower) and maximum, e.g. `45 s / 126 s`."""
    if not values:
        return "—"
    ordered = sorted(values)
    return f"{ordered[(len(ordered) - 1) // 2]:.0f} {unit} / {ordered[-1]:.0f} {unit}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated scenario ids, e.g. S1,S6")
    parser.add_argument("--agent", action="store_true", help="run the P11 investigation too")
    parser.add_argument(
        "--resolve", action="store_true", help="wait for the detector to resolve after revert"
    )
    parser.add_argument("--out", default=RESULTS_FILE, help="where to write the JSON result")
    parser.add_argument(
        "--history", default="", help="earlier result file: check memory retrieval against it"
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="approve the proposal, let the verifier decide, check the report (implies --agent)",
    )
    parser.add_argument(
        "--scoreboard",
        default="runtime/scoreboard.md",
        help="where to write the Markdown scoreboard matrix (P16)",
    )
    args = parser.parse_args()
    run_started = datetime.now(UTC)
    # The console is often redirected to a log; keep the scoreboard's symbols printable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    history: dict[str, list[str]] = {}
    if args.history:
        with open(args.history, encoding="utf-8") as handle:
            for item in json.load(handle)["results"]:
                if item.get("incident") and item.get("final_status") == "RESOLVED":
                    history.setdefault(item["id"], []).append(item["incident"])
    wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    scenarios = [
        item
        for item in SCENARIOS
        if (not wanted or item.id in wanted) and (args.remediate or not item.remediate_only)
    ]

    client = _http.client(TIMEOUT, retries=2)
    results: list[Result] = []
    try:
        health = _get(client, "/api/health").json()
        print(f"api health: {health['status']}")
        wait_for_warmup(client)
        for scenario in scenarios:
            print(f"\n== {scenario.id} {scenario.name} -> expect {scenario.expected}")
            result = run_one(
                client,
                scenario,
                agent=args.agent,
                resolve=args.resolve,
                previous=history.get(scenario.id),
                remediate_flow=args.remediate,
            )
            results.append(result)
            verdict = "PASS" if result.passed else "FAIL"
            print(
                f"  {verdict} incident={result.incident} primary={result.primary_service} "
                f"detected={result.detected_s or 0:.0f}s top={result.top} "
                f"score={result.score} evidence={result.evidence}"
            )
            if result.error:
                print(f"       error: {result.error}")
            if result.memory is not None:
                print(
                    f"       memory rank={result.memory.get('rank')} "
                    f"top3={result.memory.get('top3')} previous={result.memory.get('previous')} "
                    f"hits={[hit['incident'] for hit in result.memory.get('hits') or []]}"
                )
            if result.agent is not None:
                item = result.agent
                print(
                    f"       agent {'PASS' if result.agent_passed else 'FAIL'} "
                    f"status={item.get('status')} root={item.get('root_cause')} "
                    f"class={item.get('fault_class')} conf={item.get('confidence')} "
                    f"valid={item.get('valid')} invalid_codes={item.get('invalid_codes')} "
                    f"tools={item.get('tool_calls')} ms={item.get('latency_ms')} "
                    f"final={result.final_status}"
                )
                if item.get("error") or item.get("validation_errors"):
                    print(
                        f"       agent error: {item.get('error')} {item.get('validation_errors')}"
                    )
            if result.remediation is not None:
                item = result.remediation
                print(
                    f"       remediation {'PASS' if result.remediation_passed else 'FAIL'} "
                    f"proposed={item.get('proposed')} action={item.get('action')} "
                    f"status={item.get('action_status')} verdict={item.get('verdict')} "
                    f"(expect {item.get('expect_verdict')}) observed={item.get('observed_for_s')}s "
                    f"failing={item.get('failing')} final={item.get('final_status')} "
                    f"report={'ok' if (item.get('report') or {}).get('ok') else item.get('report')}"
                )
                if item.get("error"):
                    print(f"       remediation error: {item['error']}")
            if result.ranking:
                print(
                    "       ranking: "
                    + ", ".join(f"{item['service']}={item['score']}" for item in result.ranking[:4])
                )
    finally:
        stats: dict[str, Any] | None = None
        try:
            reset(client)
            hours = int((datetime.now(UTC) - run_started).total_seconds() // 3600) + 1
            response = _get(client, f"/api/stats?window_h={hours}")
            stats = response.json() if response.status_code == 200 else None
        finally:
            client.close()

    passed = sum(1 for item in results if item.passed)
    print(f"\nscore: {passed}/{len(results)} top-1")
    for item in results:
        print(
            f"  {item.id} {'PASS' if item.passed else 'FAIL'} "
            f"expected={item.expected} top={item.top or '—'} "
            f"incident={item.incident or '—'} detected={item.detected_s or 0:.0f}s"
        )
    agent_runs = [item for item in results if item.agent is not None]
    agent_passed = sum(1 for item in agent_runs if item.agent_passed)
    invalid = sum(len((item.agent or {}).get("invalid_codes") or []) for item in agent_runs)
    if agent_runs:
        print(
            f"agent: {agent_passed}/{len(agent_runs)} diagnosed correctly, "
            f"{invalid} invalid evidence code(s), max tool calls "
            f"{max(int((item.agent or {}).get('tool_calls') or 0) for item in agent_runs)}"
        )
    memory_runs = [item for item in results if item.memory is not None]
    if memory_runs:
        top3 = sum(1 for item in memory_runs if (item.memory or {}).get("top3"))
        print(f"memory: {top3}/{len(memory_runs)} matching incident in top-3")
    remediation_runs = [item for item in results if item.remediation is not None]
    remediation_passed = sum(1 for item in remediation_runs if item.remediation_passed)
    if remediation_runs:
        print(
            f"remediation: {remediation_passed}/{len(remediation_runs)} verdicts as expected "
            "(verified recovery, wrong action escalated, report complete)"
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "total": len(results),
        "agent_passed": agent_passed if agent_runs else None,
        "invalid_evidence_codes": invalid if agent_runs else None,
        "remediation_passed": remediation_passed if remediation_runs else None,
        "results": [
            {
                **asdict(item),
                "passed": item.passed,
                "agent_passed": item.agent_passed,
                "remediation_passed": item.remediation_passed,
            }
            for item in results
        ],
    }
    payload["stats"] = stats
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    board = scoreboard(results, stats, run_started.isoformat(timespec="seconds"))
    with open(args.scoreboard, "w", encoding="utf-8") as handle:
        handle.write(board)
    print("\n" + board)
    print(f"wrote {args.out} and {args.scoreboard}")
    if agent_runs and (invalid or agent_passed < len(agent_runs)):
        return 1
    if remediation_runs and remediation_passed < len(remediation_runs):
        return 1
    # PLAN §15: 7 of the 8 analysis scenarios; a subset (--only) must pass fully.
    need = len(results) - 1 if len(results) >= 8 else len(results)
    return 0 if passed >= need else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
