"""Verify the incident engine against the live stack.

    uv run --project apps/api python scripts/verify-incidents.py

Checks the P8 DoD:

1. an injected fault opens exactly one incident within 90 s,
2. a second, different signal attaches to that incident instead of opening a
   second one,
3. reverting the fault lets the incident close itself,
4. the SSE stream replays the incident after a reconnect with `Last-Event-ID`,
5. the lab is left clean (no open faults, no open incidents, flags off).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

API = "http://127.0.0.1:8001"
OFREP = "http://127.0.0.1:8016"
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
    ok(label, detail) if condition else fail(label, detail or "condition false")


def read_sse(
    url: str, *, headers: dict[str, str] | None = None, seconds: float = 4.0
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    deadline = time.monotonic() + seconds
    try:
        with httpx.stream("GET", url, headers=headers, timeout=httpx.Timeout(2.0)) as response:
            if response.status_code != 200:
                return events
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    break
                if line.startswith("id: "):
                    current["id"] = int(line[4:])
                elif line.startswith("event: "):
                    current["type"] = line[7:]
                elif line.startswith("data: "):
                    try:
                        current["data"] = json.loads(line[6:])
                    except json.JSONDecodeError:
                        current["data"] = {}
                elif line == "" and current:
                    events.append(current)
                    current = {}
    except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError):
        pass
    return events


def get(client: httpx.Client, path: str) -> httpx.Response:
    """GET with retries: Docker Desktop drops idle loopback sockets under load."""
    last: Exception | None = None
    for _ in range(4):
        try:
            return client.get(f"{API}{path}")
        except httpx.TransportError as exc:
            last = exc
            time.sleep(1.0)
    raise last if last is not None else RuntimeError("GET failed")


def open_incidents(client: httpx.Client) -> list[dict[str, Any]]:
    payload = get(client, "/api/incidents?status=open&limit=20").json()
    return list(payload.get("items") or [])


def run_checks(client: httpx.Client) -> None:
    health = get(client, "/api/health").json()
    expect("api health ok", health["status"] == "ok", health["status"])
    status = get(client, "/api/detection/status").json()
    expect("detector is running", status.get("task_running") is True)
    expect(
        "detector interval is 10 s", status.get("interval_s") == 10, str(status.get("interval_s"))
    )
    baselines = status.get("baselines") or {}
    expect(
        "baselines exist for the demo services",
        (baselines.get("payment") or {}).get("error_rate") is not None,
        str(baselines.get("payment")),
    )

    # Clean slate.
    client.post(f"{API}/api/faults/reset-all?actor=verify-incidents")
    for incident in open_incidents(client):
        client.post(f"{API}/api/incidents/{incident['public_id']}/close?actor=verify-incidents")
    baseline_incidents = get(client, "/api/incidents?limit=200").json()["total"]
    anchor_events = read_sse(f"{API}/api/events", seconds=3.0)
    anchor = max((event.get("id", 0) for event in anchor_events), default=0)
    expect("sse stream replays history on connect", anchor > 0, f"anchor id {anchor}")

    # 1. one incident from payment_failure, within 90 s.
    started = time.monotonic()
    injected = client.post(
        f"{API}/api/faults",
        json={
            "fault_id": "payment_failure",
            "params": {"variant": "100%"},
            "ttl_s": 480,
            "actor": "verify-incidents",
        },
    )
    expect("payment fault injected", injected.status_code == 201, str(injected.status_code))
    if injected.status_code != 201:
        return
    payment_fault = injected.json()["fault"]["id"]

    incident: dict[str, Any] | None = None
    elapsed = 0.0
    while time.monotonic() - started < 150:
        time.sleep(3)
        open_now = open_incidents(client)
        if open_now:
            incident = open_now[0]
            elapsed = time.monotonic() - started
            break
    expect(
        "a fault opens an incident within 90 s",
        incident is not None and elapsed <= 90,
        f"{elapsed:.0f}s" if incident else "no incident after 150s",
    )
    if incident is None:
        return

    public_id = incident["public_id"]
    expect(
        "exactly one incident is open",
        len(open_incidents(client)) == 1,
        str(len(open_incidents(client))),
    )
    expect(
        "the incident names the origin or its caller",
        incident["primary_service"] in ("payment", "checkout"),
        str(incident["primary_service"]),
    )
    detail = get(client, f"/api/incidents/{public_id}").json()
    kinds = [event["kind"] for event in detail["events"]]
    expect("the timeline starts with opened", bool(kinds) and kinds[0] == "opened", str(kinds[:4]))
    expect(
        "the trigger carries the error-rate signal",
        any(sig.get("signal") == "error_rate" for sig in detail["signals"]),
        str(detail["signals"])[:160],
    )

    # 2. a second signal attaches instead of opening a second incident.
    # A stopped container is a direct mechanism: the detector sees it after
    # three cycles, with no flagd propagation to wait for.
    second = client.post(
        f"{API}/api/faults",
        json={
            "fault_id": "service_down",
            "target": "email",
            "ttl_s": 240,
            "actor": "verify-incidents",
        },
    )
    expect("second fault injected", second.status_code == 201, str(second.status_code))
    second_fault = second.json()["fault"]["id"] if second.status_code == 201 else None
    attached = False
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(5)
        detail = get(client, f"/api/incidents/{public_id}").json()
        signals = detail.get("signals") or []
        if any(item.get("service") == "email" for item in signals):
            attached = True
            break
    expect(
        "a duplicate signal attaches to the same incident",
        attached,
        f"services={detail.get('services')}",
    )
    expect(
        "no second incident was opened",
        get(client, "/api/incidents?limit=200").json()["total"] == baseline_incidents + 1,
        str(get(client, "/api/incidents?limit=200").json()["total"]),
    )
    if second_fault is not None:
        client.delete(f"{API}/api/faults/{second_fault}?actor=verify-incidents")

    # 3. revert and wait for the detector to close it.
    reverted = client.delete(f"{API}/api/faults/{payment_fault}?actor=verify-incidents")
    expect("payment fault reverted", reverted.status_code == 200, str(reverted.status_code))
    closed = False
    closed_at = 0.0
    current = detail
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        time.sleep(5)
        current = get(client, f"/api/incidents/{public_id}").json()
        if current["status"] in ("RESOLVED", "ESCALATED"):
            closed = True
            closed_at = time.monotonic() - started
            break
    expect(
        "recovery closes the incident",
        closed,
        f"{current['status']} after {closed_at:.0f}s"
        if closed
        else f"still {current['status']} after 360s",
    )
    if closed:
        expect(
            "the close is RESOLVED by the detector",
            current["status"] == "RESOLVED",
            current["status"],
        )
        kinds = [event["kind"] for event in current["events"]]
        expect("a recovered event is on the timeline", "recovered" in kinds, str(kinds[-3:]))

    # 4. SSE replay from Last-Event-ID.
    replay = read_sse(
        f"{API}/api/events",
        headers={"Last-Event-ID": str(anchor)},
        seconds=6.0,
    )
    replayed = [
        event
        for event in replay
        if event.get("type") == "incident.opened"
        and event.get("data", {}).get("public_id") == public_id
    ]
    expect(
        "SSE replays the incident from Last-Event-ID",
        bool(replayed),
        f"{len(replay)} replayed events",
    )
    expect(
        "replayed events are ordered after the anchor",
        all(event.get("id", 0) > anchor for event in replay),
    )


def cleanup(client: httpx.Client) -> None:
    client.post(f"{API}/api/faults/reset-all?actor=verify-incidents")
    for incident in open_incidents(client):
        client.post(f"{API}/api/incidents/{incident['public_id']}/close?actor=verify-incidents")
    open_faults = get(client, "/api/faults?status=open").json()["open"]
    expect("no open faults remain", open_faults == 0, str(open_faults))
    drift: list[str] = []
    for definition in get(client, "/api/faults/catalog").json()["faults"]:
        key = definition.get("flag")
        if not key:
            continue
        response = client.post(f"{OFREP}/ofrep/v1/evaluate/flags/{key}", json={})
        if response.json().get("variant") != "off":
            drift.append(key)
    expect("every catalog flag is off", not drift, ",".join(drift))
    health = get(client, "/api/health").json()
    expect("health is ok after the run", health["status"] == "ok", health["status"])


def main() -> int:
    client = httpx.Client(
        timeout=TIMEOUT, transport=httpx.HTTPTransport(retries=3)
    )
    try:
        run_checks(client)
    except Exception as exc:
        fail("unexpected error", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        try:
            cleanup(client)
        except Exception as exc:
            fail("cleanup failed", f"{type(exc).__name__}: {exc}"[:300])
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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
