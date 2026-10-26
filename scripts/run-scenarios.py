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


@dataclass
class Scenario:
    id: str
    name: str
    fault: dict[str, Any]
    expected: str
    note: str = ""


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

    @property
    def passed(self) -> bool:
        return self.top == self.expected and not self.error

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
        breaches = status.get("breaches") or []
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


def run_one(
    client: httpx.Client,
    scenario: Scenario,
    *,
    agent: bool = False,
    resolve: bool = False,
    previous: list[str] | None = None,
) -> Result:
    result = Result(
        id=scenario.id,
        name=scenario.name,
        expected=scenario.expected,
        note=scenario.note,
    )
    reset(client)
    settle(client, SETTLE_S)
    baseline = _get(client, "/api/incidents?limit=200").json()
    baseline_total = baseline["total"]

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
    if agent:
        result.agent = investigate(client, incident["public_id"])

    _delete(client, f"/api/faults/{fault_id}?actor=scenarios")
    if resolve:
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
    args = parser.parse_args()
    history: dict[str, list[str]] = {}
    if args.history:
        with open(args.history, encoding="utf-8") as handle:
            for item in json.load(handle)["results"]:
                if item.get("incident") and item.get("final_status") == "RESOLVED":
                    history.setdefault(item["id"], []).append(item["incident"])
    wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    scenarios = [item for item in SCENARIOS if not wanted or item.id in wanted]

    client = httpx.Client(timeout=TIMEOUT, transport=httpx.HTTPTransport(retries=2))
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
            if result.ranking:
                print(
                    "       ranking: "
                    + ", ".join(f"{item['service']}={item['score']}" for item in result.ranking[:4])
                )
    finally:
        try:
            reset(client)
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
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "total": len(results),
        "agent_passed": agent_passed if agent_runs else None,
        "invalid_evidence_codes": invalid if agent_runs else None,
        "results": [
            {**asdict(item), "passed": item.passed, "agent_passed": item.agent_passed}
            for item in results
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"\nwrote {args.out}")
    if agent_runs and (invalid or agent_passed < len(agent_runs)):
        return 1
    return 0 if passed >= 7 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
