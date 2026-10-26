"""P14 live check: approve / reject / expire / unsafe / audit on the real stack.

    uv run --project apps/api python -u scripts/remediation-check.py --out docs/evidence/p14-remediation.json

One flag fault (payment_failure 100 %) yields three incidents in a row, because
an escalated incident is terminal and the still-active fault opens the next:

1. auto-plan → dry-run → unsafe proposals refused → replace with a 20 s TTL →
   it expires → 409 on approve → incident ESCALATED;
2. auto-plan → reject → REJECTED → ESCALATED, nothing executed;
3. auto-plan → approve → token minted and consumed → fault reverted by the
   executor → VERIFYING; a second approve and a forged/replayed token are refused.

Then `service_down` on email: the operator replaces `reset_fault` with
`restart_service` → the executor's own `docker start` path.

Each step is recorded as a named check; the script exits non-zero if any fails.
The stack is left clean (faults reverted, incidents closed).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_scenarios", ROOT / "scripts" / "run-scenarios.py")
assert _spec and _spec.loader
rs = importlib.util.module_from_spec(_spec)
sys.modules["run_scenarios"] = rs
_spec.loader.exec_module(rs)

CHECKS: list[dict[str, Any]] = []
LOG: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "incidents": {}}


def check(name: str, ok: bool, detail: Any = None) -> bool:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail is not None else ""))
    return ok


def get(client: httpx.Client, path: str) -> Any:
    return rs._get(client, path).json()


def post(client: httpx.Client, path: str, body: Any = None) -> httpx.Response:
    return rs._post(client, path, json=body)


def wait_fresh_incident(client: httpx.Client, since: datetime, timeout_s: float = 300) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        items = get(client, "/api/incidents?status=open&limit=20").get("items", [])
        fresh = [
            item
            for item in items
            if datetime.fromisoformat(item["opened_at"].replace("Z", "+00:00")) >= since
        ]
        if fresh:
            return sorted(fresh, key=lambda item: item["opened_at"])[0]
        time.sleep(4)
    raise RuntimeError(f"no incident opened within {timeout_s}s")


def status_of(client: httpx.Client, ref: str) -> str:
    return str(get(client, f"/api/incidents/{ref}")["status"])


def pending_approval(client: httpx.Client, ref: str) -> dict[str, Any] | None:
    items = get(client, f"/api/incidents/{ref}/approvals").get("items", [])
    return next((item for item in items if item["status"] == "PENDING"), None)


def investigate_to_awaiting(client: httpx.Client, ref: str) -> dict[str, Any]:
    run = rs.investigate(client, ref)
    LOG["incidents"].setdefault(ref, {})["agent"] = run
    check(f"{ref}: agent DIAGNOSED with valid codes", run.get("status") == "DIAGNOSED"
          and run.get("invalid_codes") == [], {k: run.get(k) for k in ("status", "root_cause", "action")})
    status = status_of(client, ref)
    approval = pending_approval(client, ref)
    check(f"{ref}: auto-plan → AWAITING_APPROVAL with a PENDING approval",
          status == "AWAITING_APPROVAL" and approval is not None,
          {"status": status, "approval": approval and approval["id"]})
    if approval is None:
        raise RuntimeError(f"{ref}: no pending approval")
    return approval


def no_token_in(text: str) -> bool:
    return '"token"' not in text and "token_hash" not in text


def flow_flag(client: httpx.Client) -> None:
    print("== payment_failure: expire, reject, approve")
    started = datetime.now(UTC)
    response = post(client, "/api/faults", {"fault_id": "payment_failure", "params": {"variant": "100%"},
                                            "ttl_s": 1200, "actor": "p14-check"})
    if not check("inject payment_failure 100%", response.status_code == 201, response.status_code):
        raise RuntimeError(response.text)
    fault_id = response.json()["fault"]["id"]
    LOG["flag_fault"] = fault_id

    # --- incident 1: dry-run, unsafe, replace, expire ---------------------------
    inc = wait_fresh_incident(client, started)
    ref = inc["public_id"]
    LOG["incidents"][ref] = {"flow": "expire+unsafe"}
    approval = investigate_to_awaiting(client, ref)
    raw = rs._get(client, f"/api/approvals/{approval['id']}").text
    check(f"{ref}: approval response carries no token or hash", no_token_in(raw)
          and approval["token_issued"] is False)
    check(f"{ref}: proposal is the diagnosis' action",
          (approval["action"]["action_id"], approval["action"]["target"]) == ("reset_fault", "payment"),
          approval["action"]["action_id"] + " → " + approval["action"]["target"])
    dry = post(client, f"/api/approvals/{approval['id']}/dry-run").json()
    check(f"{ref}: dry-run would execute, changes nothing", dry["would_execute"] is True
          and get(client, f"/api/faults/{fault_id}")["status"] in ("ACTIVE", "PROPAGATING"),
          dry["check"]["steps"])
    unsafe = [
        ({"action_id": "drop_database", "target": "payment"}, 422, "unknown_action"),
        ({"action_id": "restart_service", "target": "postgres"}, 422, "target_not_allowed"),
        ({"action_id": "restart_service", "target": "flagd"}, 422, "target_not_allowed"),
        ({"action_id": "stop_load_generator", "target": "payment"}, 422, "target_not_allowed"),
        ({"action_id": "reset_fault", "target": "cart", "replace": True}, 409, "precondition_failed"),
        ({"action_id": "restore_network", "target": "payment", "replace": True}, 409,
         "precondition_failed"),
        ({"action_id": "reset_fault", "target": "payment"}, 409, "approval_pending"),
        ({"action_id": "reset_fault", "target": "payment", "token": "x"}, 422, None),
    ]
    for body, code, err in unsafe:
        res = post(client, f"/api/incidents/{ref}/approvals", body)
        got = (res.json().get("error") or {}).get("code") if res.headers.get("content-type", "").startswith(
            "application/json") else None
        check(f"{ref}: refuse {body}", res.status_code == code and (err is None or got == err),
              f"{res.status_code} {got}")
    check(f"{ref}: still exactly one PENDING after refusals",
          len([a for a in get(client, f"/api/incidents/{ref}/approvals")["items"]
               if a["status"] == "PENDING"]) == 1)
    res = post(client, f"/api/incidents/{ref}/approvals",
               {"action_id": "reset_fault", "target": "payment", "ttl_s": 20, "replace": True,
                "actor": "p14-check", "note": "short TTL to prove expiry"})
    replaced = res.json().get("approval") or {}
    check(f"{ref}: replace with a 20 s TTL", res.status_code == 201 and replaced.get("status") == "PENDING",
          res.status_code)
    old = get(client, f"/api/approvals/{approval['id']}")
    check(f"{ref}: replaced approval WITHDRAWN", old["status"] == "WITHDRAWN", old["status"])
    res = post(client, f"/api/approvals/{approval['id']}/approve", {"actor": "p14-check"})
    check(f"{ref}: approve a withdrawn approval → 409", res.status_code == 409, res.json()["error"]["code"])
    time.sleep(30)
    expired = get(client, f"/api/approvals/{replaced['id']}")
    res = post(client, f"/api/approvals/{replaced['id']}/approve", {"actor": "p14-check"})
    check(f"{ref}: approval EXPIRED after its TTL, approve → 409",
          expired["status"] == "EXPIRED" and res.status_code == 409,
          {"status": expired["status"], "approve": res.status_code, "code": res.json()["error"]["code"]})
    check(f"{ref}: expiry escalates the incident, fault untouched",
          status_of(client, ref) == "ESCALATED"
          and get(client, f"/api/faults/{fault_id}")["status"] in ("ACTIVE", "PROPAGATING"))
    LOG["incidents"][ref]["approvals"] = [approval["id"], replaced["id"]]

    # --- incident 2: reject ---------------------------------------------------------
    since = datetime.now(UTC)
    inc = wait_fresh_incident(client, since)
    ref = inc["public_id"]
    LOG["incidents"][ref] = {"flow": "reject"}
    approval = investigate_to_awaiting(client, ref)
    res = post(client, f"/api/approvals/{approval['id']}/reject",
               {"actor": "p14-check", "note": "operator declines"})
    body = res.json()
    check(f"{ref}: reject → REJECTED", res.status_code == 200 and body["status"] == "REJECTED"
          and body["action"]["status"] == "REJECTED", body["status"])
    events = [e for e in get(client, f"/api/incidents/{ref}")["events"] if e["kind"] in ("approval", "escalated")]
    path = [e["payload"].get("to") for e in events]
    check(f"{ref}: incident AWAITING_APPROVAL → REJECTED → ESCALATED",
          path[-2:] == ["REJECTED", "ESCALATED"] and status_of(client, ref) == "ESCALATED", path)
    check(f"{ref}: nothing executed after reject",
          get(client, f"/api/faults/{fault_id}")["status"] in ("ACTIVE", "PROPAGATING")
          and body["action"]["started_at"] is None)
    res = post(client, f"/api/approvals/{approval['id']}/approve", {"actor": "p14-check"})
    check(f"{ref}: approve after reject → 409", res.status_code == 409, res.json()["error"]["code"])
    LOG["incidents"][ref]["approvals"] = [approval["id"]]

    # --- incident 3: approve + execute -------------------------------------------
    since = datetime.now(UTC)
    inc = wait_fresh_incident(client, since)
    ref = inc["public_id"]
    LOG["incidents"][ref] = {"flow": "approve"}
    approval = investigate_to_awaiting(client, ref)
    t0 = time.monotonic()
    res = rs._post(client, f"/api/approvals/{approval['id']}/approve?wait=true",
                   json={"actor": "p14-check", "note": "approved by the P14 check"})
    body = res.json()
    LOG["incidents"][ref]["execute_s"] = round(time.monotonic() - t0, 1)
    check(f"{ref}: approve → APPROVED, token issued and used, never returned",
          res.status_code == 200 and body["status"] == "APPROVED" and body["token_issued"]
          and body["token_used"] and no_token_in(res.text),
          {k: body[k] for k in ("status", "token_issued", "token_used")})
    action = body["action"]
    check(f"{ref}: action EXECUTED with pre/post snapshots",
          action["status"] == "EXECUTED" and action["pre_snapshot"] and action["post_snapshot"],
          {"status": action["status"], "error": action["error"]})
    fault = get(client, f"/api/faults/{fault_id}")
    revert = (fault.get("injection_ref") or {}).get("revert") or {}
    check(f"{ref}: fault reverted by the executor through the controller",
          fault["status"] == "REVERTED" and str(revert.get("actor", "")).startswith("remediation:A"),
          {"status": fault["status"], "actor": revert.get("actor"), "reason": revert.get("reason")})
    flags = (action["post_snapshot"] or {}).get("flags") or {}
    check(f"{ref}: paymentFailure reads back off", flags.get("paymentFailure") == "off", flags)
    check(f"{ref}: incident REMEDIATING → VERIFYING", status_of(client, ref) == "VERIFYING",
          status_of(client, ref))
    res = post(client, f"/api/approvals/{approval['id']}/approve", {"actor": "p14-check"})
    check(f"{ref}: second approve → 409", res.status_code == 409, res.json()["error"]["code"])
    replay = asyncio.run(_replay(approval["id"]))
    check(f"{ref}: executor refuses a replayed/forged token", replay["refused"], replay)
    LOG["incidents"][ref]["approvals"] = [approval["id"]]
    LOG["incidents"][ref]["action"] = action
    _audit(client, ref, {"plan", "approve", "execute_started", "executed", "token_refused"})
    post(client, f"/api/incidents/{ref}/close?actor=p14-check&note=P15+verifier+not+built+yet")


async def _replay(approval_id: int) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.errors import ApiError
    from app.remediation import tokens
    from app.remediation.executor import Executor

    out: dict[str, Any] = {"refused": False}
    try:
        await Executor().execute(approval_id, tokens.mint()[0])
    except ApiError as exc:
        out.update(refused=exc.status_code == 403, code=exc.code, message=exc.message)
    return out


def _audit(client: httpx.Client, ref: str, expected: set[str]) -> None:
    entries = get(client, f"/api/approvals/audit?incident={ref}")
    seen = {entry["event"] for entry in entries}
    LOG["incidents"][ref]["audit"] = [
        {k: e.get(k) for k in ("at", "actor", "event", "outcome", "detail")} for e in entries
    ]
    check(f"{ref}: audit trail has {sorted(expected)}", expected <= seen, sorted(seen))


def flow_restart(client: httpx.Client) -> None:
    print("== service_down email: replace with restart_service, approve")
    rs.reset(client)
    rs.settle(client, rs.SETTLE_S)
    started = datetime.now(UTC)
    response = post(client, "/api/faults", {"fault_id": "service_down", "target": "email",
                                            "ttl_s": 900, "actor": "p14-check"})
    if not check("inject service_down email", response.status_code == 201, response.status_code):
        raise RuntimeError(response.text)
    fault_id = response.json()["fault"]["id"]
    inc = wait_fresh_incident(client, started)
    ref = inc["public_id"]
    LOG["incidents"][ref] = {"flow": "restart_service"}
    approval = investigate_to_awaiting(client, ref)
    res = post(client, f"/api/incidents/{ref}/approvals",
               {"action_id": "restart_service", "target": "email", "replace": True,
                "actor": "p14-check", "note": "operator prefers a restart"})
    new = res.json().get("approval") or {}
    check(f"{ref}: replace with restart_service email (medium risk, approval required)",
          res.status_code == 201 and new.get("action", {}).get("approval_mode") == "required",
          res.status_code)
    res = rs._post(client, f"/api/approvals/{new['id']}/approve?wait=true", json={"actor": "p14-check"})
    action = res.json()["action"]
    steps = (action.get("result") or {}).get("steps") or []
    check(f"{ref}: executor ran docker start and the fault row closed",
          action["status"] == "EXECUTED" and steps and steps[0].get("docker") == "start"
          and get(client, f"/api/faults/{fault_id}")["status"] == "REVERTED",
          {"status": action["status"], "steps": [s.get("docker") or s.get("fault_id") for s in steps]})
    check(f"{ref}: email running after restart",
          ((action.get("post_snapshot") or {}).get("container") or {}).get("running") is True)
    check(f"{ref}: rollback recorded only when needed", action["rollback"] is None)
    LOG["incidents"][ref]["action"] = action
    _audit(client, ref, {"plan", "approve", "execute_started", "executed"})
    post(client, f"/api/incidents/{ref}/close?actor=p14-check&note=P15+verifier+not+built+yet")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runtime/p14-remediation.json")
    parser.add_argument("--skip-restart", action="store_true")
    args = parser.parse_args()
    with httpx.Client(timeout=rs.TIMEOUT) as client:
        try:
            rs.wait_for_warmup(client)
            rs.reset(client)
            rs.settle(client, rs.SETTLE_S)
            flow_flag(client)
            if not args.skip_restart:
                flow_restart(client)
        except Exception as exc:
            check("script completed", False, f"{type(exc).__name__}: {exc}")
        finally:
            rs.reset(client)
    LOG["finished_at"] = datetime.now(UTC).isoformat()
    LOG["checks"] = CHECKS
    failed = [c for c in CHECKS if not c["ok"]]
    LOG["summary"] = {"checks": len(CHECKS), "failed": len(failed)}
    Path(args.out).write_text(json.dumps(LOG, indent=2, default=str), encoding="utf-8")
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed → {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
