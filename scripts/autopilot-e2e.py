"""Autonomous end-to-end check: break the lab, touch nothing, expect it fixed.

For each scenario the script breaks the lab — through the fault API (`fault:`)
or *behind DocOck's back* with a raw Docker/flag change (`manual:`), so there is
no fault row to revert — then only watches. It passes when the autopilot opens,
investigates, remediates and verifies the incident (RESOLVED) and the lab has no
drift left. Nobody approves anything.

    uv run --project apps/api python scripts/autopilot-e2e.py fault:payment_failure \
        manual:stop:email fault:cache_outage

Scenario syntax:
    fault:<fault_id>[@<target>][=<variant or param>]   e.g. fault:service_crash@shipping
    manual:stop:<container> | manual:pause:<container> | manual:kill:<container>
    multi:<scenario>+<scenario>                        several breaks at once

Writes runtime/autopilot-e2e.json. Exit code 0 only when every scenario passes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = "http://127.0.0.1:8001/api"
ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"RESOLVED", "ESCALATED"}


def api(client: httpx.Client, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    response = client.request(method, API + path, timeout=120, **kwargs)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def latest_incident_id(client: httpx.Client) -> int:
    _, data = api(client, "GET", "/incidents", params={"limit": 1})
    items = (data or {}).get("items") or []
    return int(items[0]["id"]) if items else 0


def wait_quiet(client: httpx.Client, timeout_s: float) -> None:
    """No active incident and no drift before the next scenario starts."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _, incidents = api(client, "GET", "/incidents", params={"limit": 5})
        _, drift = api(client, "GET", "/remediation/drift")
        if not (incidents or {}).get("open") and not (drift or {}).get("items"):
            return
        time.sleep(10)
    print("   warning: lab not quiet before the next scenario")


def break_lab(client: httpx.Client, spec: str) -> dict[str, Any]:
    if spec.startswith("multi:"):
        parts = spec.removeprefix("multi:").split("+")
        return {"multi": [break_lab(client, part) for part in parts]}
    kind, _, rest = spec.partition(":")
    if kind == "manual":
        verb, _, container = rest.partition(":")
        command = {
            "stop": ["docker", "stop", container],
            "pause": ["docker", "pause", container],
            "kill": ["docker", "kill", "--signal", "KILL", container],
        }[verb]
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        return {"manual": " ".join(command), "rc": done.returncode}
    fault_id, _, value = rest.partition("=")
    fault_id, _, target = fault_id.partition("@")
    body: dict[str, Any] = {"fault_id": fault_id, "ttl_s": 1800, "actor": "autopilot-e2e"}
    if target:
        body["target"] = target
    if value:
        key = "variant" if not value.replace(".", "").isdigit() else "users"
        body["params"] = {key: value if key == "variant" else float(value)}
    status, data = api(client, "POST", "/faults", json=body)
    if status >= 300:
        raise RuntimeError(f"inject {spec}: HTTP {status} {data}")
    return {"fault_row": data["fault"]["id"], "status": data["fault"]["status"]}


def follow(client: httpx.Client, after_id: int, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    ref: str | None = None
    last = ""
    while time.monotonic() < deadline:
        _, data = api(client, "GET", "/incidents", params={"limit": 5})
        fresh = [item for item in (data or {}).get("items") or [] if item["id"] > after_id]
        if fresh:
            ref = fresh[-1]["public_id"]
            _, detail = api(client, "GET", f"/incidents/{ref}")
            status = detail["status"]
            if status != last:
                print(f"   {time.strftime('%H:%M:%S')} {ref} {status}")
                last = status
            if status in TERMINAL:
                return {"ref": ref, "status": status, "detail": detail}
        time.sleep(5)
    return {"ref": ref, "status": last or "NO_INCIDENT", "detail": None}


def summarize(client: httpx.Client, ref: str) -> list[dict[str, Any]]:
    _, approvals = api(client, "GET", "/approvals", params={"limit": 50})
    rows = [item for item in (approvals or {}).get("items") or [] if item["incident_ref"] == ref]
    out = []
    for item in sorted(rows, key=lambda row: row["id"]):
        action = item["action"]
        auto = (action.get("args") or {}).get("autopilot") or {}
        out.append(
            {
                "action": f"{action['action_id']} {action['target']}",
                "status": action["status"],
                "decided_by": item.get("decided_by"),
                "planner": auto.get("planner"),
                "source": auto.get("source"),
                "rationale": (auto.get("rationale") or "")[:160],
                "error": action.get("error"),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenarios", nargs="+")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    results: list[dict[str, Any]] = []
    with httpx.Client() as client:
        _, pilot = api(client, "GET", "/autopilot")
        if not (pilot or {}).get("enabled"):
            print("autopilot is disabled (AUTOPILOT_ENABLED=false)")
            return 2
        for spec in args.scenarios:
            print(f"== {spec}")
            wait_quiet(client, 600)
            after = latest_incident_id(client)
            started = time.monotonic()
            broke = break_lab(client, spec)
            print(f"   broke: {broke}")
            outcome = follow(client, after, args.timeout)
            _, drift = api(client, "GET", "/remediation/drift")
            remaining = [f"{i['kind']}:{i['service']}" for i in (drift or {}).get("items") or []]
            attempts = summarize(client, outcome["ref"]) if outcome["ref"] else []
            for attempt in attempts:
                print(
                    f"   - {attempt['action']:34} {attempt['status']:10} by={attempt['decided_by']}"
                    f" planner={attempt['planner']} :: {attempt['rationale']}"
                )
            passed = outcome["status"] == "RESOLVED" and not remaining
            elapsed = round(time.monotonic() - started)
            print(f"   => {'PASS' if passed else 'FAIL'} {outcome['status']} in {elapsed}s"
                  + (f"; drift left: {remaining}" if remaining else ""))
            results.append(
                {
                    "scenario": spec,
                    "passed": passed,
                    "incident": outcome["ref"],
                    "status": outcome["status"],
                    "elapsed_s": elapsed,
                    "attempts": attempts,
                    "drift_left": remaining,
                    "broke": broke,
                }
            )
    out = ROOT / "runtime" / "autopilot-e2e.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(1 for item in results if item["passed"])
    print(f"\n{passed}/{len(results)} scenarios passed; wrote {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
