"""Run the deterministic-analysis scenarios against the live stack.

    uv run --project apps/api python scripts/run-scenarios.py
    uv run --project apps/api python scripts/run-scenarios.py --only S1,S6

For each scenario: reset → settle → inject → wait for the detector to open exactly
one incident → run the deterministic analysis → record the top-1 suspect →
revert → close the incident → settle.

The gate from PLAN §15 is top-1 == injected target in at least 7 of 8 scenarios.
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

    @property
    def passed(self) -> bool:
        return self.top == self.expected and not self.error


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


def run_one(client: httpx.Client, scenario: Scenario) -> Result:
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

    _delete(client, f"/api/faults/{fault_id}?actor=scenarios")
    _post(
        client, f"/api/incidents/{incident['public_id']}/close?actor=scenarios&note=scenario_done"
    )
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
    args = parser.parse_args()
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
            result = run_one(client, scenario)
            results.append(result)
            verdict = "PASS" if result.passed else "FAIL"
            print(
                f"  {verdict} incident={result.incident} primary={result.primary_service} "
                f"detected={result.detected_s or 0:.0f}s top={result.top} "
                f"score={result.score} evidence={result.evidence}"
            )
            if result.error:
                print(f"       error: {result.error}")
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
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "total": len(results),
        "results": [{**asdict(item), "passed": item.passed} for item in results],
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"\nwrote {RESULTS_FILE}")
    return 0 if passed >= 7 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
