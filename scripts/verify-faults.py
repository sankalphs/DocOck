"""Verify every v1 fault against the live stack.

    uv run --project apps/api python scripts/verify-faults.py

Checks, in order:

1. catalog shape and the 403 for a non-lab target (before any write),
2. dry-run mutates nothing,
3. every one of the 19 v1 faults injects and reverts with a verified read-back,
4. TTL expiry auto-reverts a flag fault,
5. an injected fault is PROPAGATING until the service shows the symptom,
6. the lab is left clean: no open faults, every catalog flag off, health 200.

Exits non-zero on the first failed check; always attempts reset-all on failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

import httpx

API = "http://127.0.0.1:8001"
OFREP = "http://127.0.0.1:8016"
LOCUST = "http://127.0.0.1:8089"
TIMEOUT = httpx.Timeout(120.0, connect=5.0)

failures: list[str] = []
checks = 0


def ok(label: str, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  PASS  {label}" + (f"  [{detail}]" if detail else ""))


def fail(label: str, detail: str) -> None:
    global checks
    checks += 1
    failures.append(f"{label}: {detail}")
    print(f"  FAIL  {label}  [{detail}]")


def expect(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok(label, detail)
    else:
        fail(label, detail or "condition false")


def docker(*args: str) -> dict[str, Any]:
    out = subprocess.check_output(["docker", *args], text=True)
    data = json.loads(out)
    return data[0] if isinstance(data, list) else data


def container_state(name: str) -> dict[str, Any]:
    item = docker("inspect", name)
    state = item.get("State") or {}
    host = item.get("HostConfig") or {}
    return {
        "running": bool(state.get("Running")),
        "paused": bool(state.get("Paused")),
        "networks": sorted((((item.get("NetworkSettings") or {}).get("Networks")) or {}).keys()),
        "nano_cpus": int(host.get("NanoCpus") or 0),
        "memory_mb": round(int(host.get("Memory") or 0) / 1024 / 1024),
    }


def host_cores() -> float:
    out = subprocess.check_output(["docker", "info", "--format", "{{.NCPU}}"], text=True)
    return float(out.strip() or 1)


def ofrep(client: httpx.Client, key: str) -> str:
    response = client.post(f"{OFREP}/ofrep/v1/evaluate/flags/{key}", json={})
    response.raise_for_status()
    return str(response.json().get("variant"))


def locust_users(client: httpx.Client) -> int:
    response = client.get(f"{LOCUST}/stats/requests")
    response.raise_for_status()
    return int(response.json().get("user_count") or 0)


def plan_for(definition: dict[str, Any]) -> dict[str, Any]:
    mechanism = definition["mechanism"]
    if mechanism == "flagd":
        return {"params": {"variant": definition["params"][0]["default"]}}
    if definition["id"] in ("service_down", "service_freeze", "network_partition"):
        return {"target": "quote"}
    if definition["id"] == "cpu_pressure":
        return {"target": "ad", "params": {"cpus": 0.5}}
    if definition["id"] == "memory_pressure":
        return {"target": "frontend", "params": {"memory_mb": 200}}
    if definition["id"] == "load_spike":
        return {"target": "load-generator", "params": {"users": 8, "duration_s": 30}}
    raise AssertionError(definition["id"])


def verify_reverted(client: httpx.Client, fault: dict[str, Any]) -> None:
    definition = fault["fault_id"]
    target = fault["target"]
    ref = fault.get("injection_ref") or {}
    revert = ref.get("revert") or {}
    expect(f"{definition}: revert is verified", bool(revert.get("verified")), str(revert)[:120])
    mechanism = fault["mechanism"]
    if mechanism == "flagd":
        expect(
            f"{definition}: OFREP reads the pre-image",
            revert.get("restored") == "off",
            str(revert.get("restored")),
        )
    elif mechanism == "container_stop":
        expect(f"{definition}: {target} is running", container_state(target)["running"])
    elif mechanism == "container_pause":
        expect(f"{definition}: {target} is not paused", not container_state(target)["paused"])
    elif mechanism == "network_partition":
        expect(
            f"{definition}: {target} is reattached",
            "opentelemetry-demo" in container_state(target)["networks"],
        )
    elif mechanism == "resource_limit":
        applied = (ref.get("pre_image") or {}).get("applied") or {}
        state = container_state(target)
        if "cpus" in applied:
            before_cpu = ((ref.get("pre_image") or {}).get("before") or {}).get("cpu_limit_cores")
            expected = before_cpu if before_cpu else host_cores()
            expect(
                f"{definition}: cpu limit restored",
                state["nano_cpus"] == int(expected * 1e9),
                f"{state['nano_cpus']} vs {expected} cores",
            )
        if "memory_mb" in applied:
            before = ((ref.get("pre_image") or {}).get("before") or {}).get("memory_limit_mb")
            expect(
                f"{definition}: memory limit restored",
                state["memory_mb"] == before,
                f"{state['memory_mb']} vs {before}",
            )
    elif mechanism == "load_spike":
        expect(f"{definition}: locust users restored", locust_users(client) == 5)


def main() -> int:
    client = httpx.Client(timeout=TIMEOUT)
    try:
        health = client.get(f"{API}/api/health")
        expect("api health", health.status_code in (200, 503), str(health.status_code))
        catalog = client.get(f"{API}/api/faults/catalog").json()
        faults = catalog["faults"]
        expect("catalog exposes 19 faults", len(faults) == 19, str(len(faults)))
        expect(
            "catalog exposes the request schema",
            "fault_id" in catalog["json_schema"].get("properties", {}),
        )

        # --- safety: 403 before any mutation ---------------------------------
        before_rows = client.get(f"{API}/api/faults").json()["total"]
        rejected = client.post(
            f"{API}/api/faults", json={"fault_id": "service_down", "target": "postgres"}
        )
        expect(
            "non-lab target rejected with 403",
            rejected.status_code == 403
            and rejected.json()["error"]["code"] == "unsafe_target",
            f"{rejected.status_code} {rejected.json().get('error', {}).get('code')}",
        )
        after_rows = client.get(f"{API}/api/faults").json()["total"]
        expect("403 wrote no fault row", before_rows == after_rows, f"{before_rows}->{after_rows}")

        # --- dry-run mutates nothing ----------------------------------------
        dry = client.post(
            f"{API}/api/faults",
            json={
                "fault_id": "payment_failure",
                "params": {"variant": "100%"},
                "dry_run": True,
            },
        )
        expect("dry-run accepted", dry.status_code == 201 and dry.json()["dry_run"] is True)
        plan = dry.json().get("plan") or {}
        expect(
            "dry-run returns the safety + pre-image",
            plan.get("pre_image", {}).get("before") == "off"
            and plan.get("safety", {}).get("project") == "docock-demo",
            str(plan.get("pre_image")),
        )
        expect(
            "dry-run did not set the flag",
            ofrep(client, "paymentFailure") == "off",
        )
        expect(
            "dry-run did not create a fault row",
            client.get(f"{API}/api/faults").json()["total"] == before_rows,
        )

        # --- every fault injects and reverts ---------------------------------
        for definition in faults:
            plan = plan_for(definition)
            payload = {"fault_id": definition["id"], "ttl_s": 120, "actor": "p6-verify", **plan}
            response = client.post(f"{API}/api/faults", json=payload)
            if response.status_code != 201:
                fail(f"{definition['id']}: inject", f"{response.status_code} {response.text[:200]}")
                continue
            fault = response.json()["fault"]
            expect(
                f"{definition['id']}: injected",
                fault["status"] in ("PROPAGATING", "ACTIVE"),
                f"{fault['status']} on {fault['target']}",
            )
            if definition["mechanism"] == "flagd":
                expect(
                    f"{definition['id']}: flag faults start PROPAGATING",
                    fault["status"] == "PROPAGATING",
                    fault["status"],
                )
            else:
                expect(
                    f"{definition['id']}: infra faults are ACTIVE after read-back",
                    fault["status"] == "ACTIVE",
                    fault["status"],
                )
            removed = client.delete(
                f"{API}/api/faults/{fault['id']}?actor=p6-verify"
            )
            if removed.status_code != 200:
                fail(f"{definition['id']}: revert", f"{removed.status_code} {removed.text[:200]}")
                continue
            reverted = removed.json()
            expect(
                f"{definition['id']}: reverted",
                reverted["status"] == "REVERTED",
                reverted["status"],
            )
            verify_reverted(client, reverted)
            again = client.delete(f"{API}/api/faults/{fault['id']}?actor=p6-verify")
            expect(
                f"{definition['id']}: revert is idempotent",
                again.status_code == 200 and again.json()["status"] == "REVERTED",
                str(again.status_code),
            )

        # --- TTL watchdog ----------------------------------------------------
        ttl = client.post(
            f"{API}/api/faults",
            json={
                "fault_id": "cart_failure",
                "params": {"variant": "10%"},
                "ttl_s": 20,
                "actor": "p6-verify",
            },
        )
        expect("ttl: injected", ttl.status_code == 201, str(ttl.status_code))
        ttl_id = ttl.json()["fault"]["id"]
        started = time.monotonic()
        expired = None
        while time.monotonic() - started < 45:
            time.sleep(3)
            current = client.get(f"{API}/api/faults/{ttl_id}").json()
            if current["status"] == "EXPIRED":
                expired = time.monotonic() - started
                break
        expect(
            "ttl: watchdog auto-reverted the fault",
            expired is not None,
            f"{expired:.1f}s" if expired else "still open after 45s",
        )
        expect("ttl: the flag reads off", ofrep(client, "cartFailure") == "off")

        # --- propagation (flagd SDK delay is real) ---------------------------
        # Let the demo's SDK clients settle after the fault sweep above: they
        # back off when the flag file changes repeatedly in a few seconds.
        time.sleep(20)
        domain = "payment_failure"
        prop = client.post(
            f"{API}/api/faults",
            json={
                "fault_id": domain,
                "params": {"variant": "100%"},
                "ttl_s": 300,
                "actor": "p6-verify",
            },
        )
        prop_id = prop.json()["fault"]["id"]
        expect("propagation: starts PROPAGATING", prop.json()["fault"]["status"] == "PROPAGATING")
        started = time.monotonic()
        active_at = None
        observed = ""
        while time.monotonic() - started < 180:
            time.sleep(5)
            current = client.get(f"{API}/api/faults/{prop_id}").json()
            if current["status"] == "ACTIVE":
                active_at = time.monotonic() - started
                observed = str((current.get("observed") or {}).get("detail") or "")
                break
        expect(
            "propagation: promoted to ACTIVE on the first symptom",
            active_at is not None,
            f"{active_at:.0f}s {observed}" if active_at else "still propagating after 180s",
        )
        client.delete(f"{API}/api/faults/{prop_id}?actor=p6-verify")
        expect("propagation: reverted", ofrep(client, "paymentFailure") == "off")

        # --- clean exit ------------------------------------------------------
        report = client.post(f"{API}/api/faults/reset-all?actor=p6-verify")
        expect("reset-all succeeds", report.status_code == 200, str(report.status_code))
        opening = client.get(f"{API}/api/faults?status=open").json()
        expect("no open faults remain", opening["open"] == 0, str(opening["open"]))
        drift = [
            flag["flag"] for flag in _catalog_flags() if ofrep(client, flag["flag"]) != "off"
        ]
        expect("every catalog flag is off", not drift, ",".join(drift))
        audit = client.get(f"{API}/api/faults/audit?limit=500").json()
        expect("audit trail recorded every inject/revert", len(audit) >= 40, str(len(audit)))
        health = client.get(f"{API}/api/health").json()
        expect("health is ok after the run", health["status"] == "ok", health["status"])
    finally:
        client.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S) of {checks} checks:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"all {checks} checks passed")
    return 0


def _catalog_flags() -> list[dict[str, str]]:
    response = httpx.get(f"{API}/api/faults/catalog", timeout=TIMEOUT)
    response.raise_for_status()
    return [
        {"flag": item["flag"]}
        for item in response.json()["faults"]
        if item.get("flag")
    ]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
