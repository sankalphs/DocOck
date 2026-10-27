"""P5 DoD: inject a real fault and time its appearance in all three panes.

Flips `paymentFailure` to the `100%` variant exactly the way the fault controller
will (atomic replace of the runtime flag file, no BOM, mtime forward), confirms
the change through OFREP, then polls the API until the symptom is visible in
metrics, traces, and logs.

    uv run --project apps/api python scripts/verify-observability.py inject
    uv run --project apps/api python scripts/verify-observability.py revert
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAG_FILE = ROOT / "runtime" / "flagd" / "demo.flagd.json"
OFREP = "http://127.0.0.1:8016/ofrep/v1/evaluate/flags/paymentFailure"
API = "http://127.0.0.1:8001/api/telemetry"

TARGET_VARIANT = "100%"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def ofrep_state() -> dict:
    """OFREP evaluates flags with POST + an empty context."""
    request = urllib.request.Request(
        OFREP,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def set_variant(variant: str) -> None:
    data = json.loads(FLAG_FILE.read_text(encoding="utf-8"))
    flag = data["flags"]["paymentFailure"]
    if variant not in flag["variants"]:
        raise SystemExit(f"variant {variant!r} not declared for paymentFailure")
    flag["defaultVariant"] = variant

    temp = FLAG_FILE.with_suffix(".tmp")
    # UTF-8 without BOM: flagd rejects a BOM, and os.replace is atomic on one
    # filesystem, which the directory mount guarantees.
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, FLAG_FILE)
    os.utime(FLAG_FILE, None)  # flagd ignores an mtime that moved backwards


def wait_for_variant(variant: str, timeout_s: float = 20.0) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        try:
            state = ofrep_state()
            if state.get("variant") == variant:
                return time.monotonic() - started
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    raise SystemExit(f"OFREP did not report {variant!r} within {timeout_s}s")


def payment_error_rate() -> float | None:
    payload = get_json(f"{API}/metrics?metric=error_rate&window=1m&service=payment")
    series = payload.get("series") or []
    return series[0]["points"][-1]["v"] if series and series[0]["points"] else None


def payment_error_traces() -> int:
    payload = get_json(f"{API}/traces?service=payment&errors_only=true&lookback_s=60&limit=5")
    return len(payload.get("traces") or [])


def payment_error_logs() -> int:
    # payment logs a failed charge at level=warn, so the text match is the
    # reliable signal. (otelcol-contrib emits unrelated level=error lines.)
    payload = get_json(
        f"{API}/logs?service=payment&q=Payment%20request%20failed&lookback_s=60&limit=5"
    )
    return int(payload.get("total") or 0)


def inject() -> int:
    print(f"flag before: {ofrep_state()['variant']}")
    set_variant(TARGET_VARIANT)
    confirmed = wait_for_variant(TARGET_VARIANT)
    print(f"OFREP confirms {TARGET_VARIANT} after {confirmed:.2f}s")
    started = time.monotonic()

    seen: dict[str, float] = {}
    metrics_value = None
    traces = 0
    logs = 0
    deadline = started + 120
    while time.monotonic() < deadline and len(seen) < 3:
        metrics_value = payment_error_rate()
        traces = payment_error_traces()
        logs = payment_error_logs()
        elapsed = time.monotonic() - started
        if "metrics" not in seen and metrics_value is not None and metrics_value > 0.2:
            seen["metrics"] = elapsed
        if "traces" not in seen and traces > 0:
            seen["traces"] = elapsed
        if "logs" not in seen and logs > 0:
            seen["logs"] = elapsed
        rate = None if metrics_value is None else round(metrics_value * 100, 1)
        print(
            f"  t+{elapsed:5.1f}s  payment error_rate={rate}%"
            f"  error_traces={traces}  error_logs={logs}"
        )
        if len(seen) < 3:
            time.sleep(3)

    print("\nfirst visible at:")
    for pane in ("metrics", "traces", "logs"):
        if pane in seen:
            print(f"  {pane:8} t+{seen[pane]:.1f}s")
        else:
            print(f"  {pane:8} NOT SEEN within 120s")

    ok = len(seen) == 3 and all(value < 60 for value in seen.values())
    print("\nDoD (<60 s in all three panes):", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def revert() -> int:
    set_variant("off")
    confirmed = wait_for_variant("off")
    print(f"OFREP confirms off after {confirmed:.2f}s")

    started = time.monotonic()
    value = None
    while time.monotonic() - started < 180:
        value = payment_error_rate()
        traces = payment_error_traces()
        logs = payment_error_logs()
        elapsed = time.monotonic() - started
        print(
            f"  t+{elapsed:5.1f}s  payment error_rate="
            f"{value if value is None else round(value * 100, 1)}%"
            f"  error_traces={traces}  error_logs={logs}"
        )
        if value is not None and value < 0.05:
            print(f"\nrecovered in {elapsed:.1f}s")
            return 0
        time.sleep(5)
    print("\nrecovery not observed within 180s")
    return 1


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "inject"
    sys.exit(inject() if action == "inject" else revert())
