"""Deterministic investigator for the offline stub provider.

It speaks the same protocol as a real model — tool calls, then one JSON answer —
so the orchestrator, validator, persistence and UI run the exact code path a
real provider uses. It reasons only from what it was shown: the context in the
first user message and the tool results that followed.

    turn 1: container health of the leading suspect(s) + active flags/faults + drift
    turn 2: error traces + the most telling metric of the leading suspect
    turn 3: the diagnosis

The root cause is the top deterministic suspect, unless a recorded fault, a
flag change or observed drift explains another top-3 suspect (a change on a
suspect is stronger evidence than a ranking margin). Drift reaches past the
span topology: a stopped Valkey or Postgres explains its caller's errors even
though it emits no spans. Confidence follows the ranking margin and whether a
direct cause (stopped container, fault, drift) was seen.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.validator import canonical
from app.faults.catalog import SERVICE_TARGETS
from app.models.llm.base import ChatMessage, ToolCall, ToolSpec
from app.models.llm.stub import StubReply

_CONTEXT = re.compile(r"```json\n(.*)\n```", re.DOTALL)
_CODE = re.compile(r"\bE\d{4}\b")

# Fault type → (fault_class, plain-language cause).
_FAULT_CLASS = {
    "payment_failure": ("application_error", "a feature flag makes its charges fail"),
    "payment_unreachable": ("configuration", "callers are configured with a bad address for it"),
    "cart_failure": ("application_error", "a feature flag makes cart calls fail"),
    "recommendation_cache_failure": ("application_error", "a feature flag breaks its cache"),
    "ad_failure": ("application_error", "a feature flag makes ad requests fail"),
    "ad_high_cpu": ("resource_exhaustion", "a feature flag drives its CPU up"),
    "ad_manual_gc": ("resource_exhaustion", "a feature flag forces full garbage collections"),
    "catalog_lock_contention": ("application_error", "a feature flag adds lock contention"),
    "email_memory_leak": ("resource_exhaustion", "a feature flag leaks memory"),
    "shipping_slowdown": ("application_error", "a feature flag slows its quotes"),
    "image_slow_load": ("application_error", "a feature flag delays image loading"),
    "readiness_failure": ("application_error", "a feature flag fails its readiness probe"),
    "traffic_flood": ("traffic", "a feature flag floods it with requests"),
    "service_down": ("dependency_unavailable", "its container is stopped"),
    "service_freeze": ("dependency_unavailable", "its container is paused"),
    "network_partition": ("network", "its container is detached from the network"),
    "cpu_pressure": ("resource_exhaustion", "a CPU limit throttles its container"),
    "memory_pressure": ("resource_exhaustion", "a memory limit constrains its container"),
    "load_spike": ("traffic", "a load spike hits it"),
    "product_catalog_failure": ("application_error", "a feature flag fails product lookups"),
    "cache_outage": ("dependency_unavailable", "its cache container is stopped"),
    "database_outage": ("dependency_unavailable", "its database container is stopped"),
    "database_freeze": ("dependency_unavailable", "its database container is paused"),
    "service_crash": ("dependency_unavailable", "its container was killed (SIGKILL)"),
    "oom_crash_loop": ("resource_exhaustion", "a memory limit OOM-kills it in a loop"),
}


def _context(messages: list[ChatMessage]) -> dict[str, Any]:
    for message in messages:
        if message.role == "user" and message.content:
            match = _CONTEXT.search(message.content)
            if match:
                try:
                    parsed = json.loads(match.group(1))
                except ValueError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_results(messages: list[ChatMessage]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        if message.role != "tool" or not message.content:
            continue
        try:
            parsed = json.loads(message.content)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append((message.name or "", parsed))
    return out


def _call(turn: int, index: int, name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=f"stub_{turn}_{index}",
        name=name,
        raw_arguments=json.dumps(arguments),
        arguments=arguments,
    )


def _metric_for(suspect: dict[str, Any]) -> str:
    signals = suspect.get("signals") or {}

    def contribution(name: str) -> float:
        return float((signals.get(name) or {}).get("contribution") or 0)

    if contribution("throughput_anomaly") > contribution("error_origin"):
        return "throughput"
    if contribution("latency_anomaly") > contribution("error_origin"):
        return "p95_latency"
    if contribution("container_unhealthy") > 0 and contribution("error_origin") == 0:
        return "container_cpu"
    return "error_rate"


def _plan(turn: int, context: dict[str, Any], offered: set[str]) -> list[ToolCall]:
    suspects = context.get("suspects") or []
    if not suspects:
        if turn != 0:
            return []
        return [
            _call(turn, index, name, {})
            for index, name in enumerate(("get_active_flags", "get_drift"))
            if name in offered
        ]
    top = suspects[0]
    service = str(top["service"])
    calls: list[tuple[str, dict[str, Any]]] = []
    if turn == 0:
        calls.append(("get_container_health", {"service": service}))
        if len(suspects) > 1 and float(suspects[1]["score"]) >= 0.8 * float(top["score"]):
            calls.append(("get_container_health", {"service": str(suspects[1]["service"])}))
        calls.append(("get_active_flags", {}))
        calls.append(("get_drift", {}))
    elif turn == 1:
        calls.append(("get_traces", {"service": service, "errors_only": True, "limit": 5}))
        calls.append(("get_metrics", {"service": service, "metric": _metric_for(top)}))
    return [
        _call(turn, index, name, args)
        for index, (name, args) in enumerate(calls)
        if name in offered
    ]


def _faults(results: list[tuple[str, dict[str, Any]]]) -> dict[str, tuple[str, str | None]]:
    """target → (fault type, flag_state code) from get_active_flags results."""
    found: dict[str, tuple[str, str | None]] = {}
    for name, output in results:
        if name != "get_active_flags":
            continue
        for item in output.get("during_incident") or []:
            target, kind = item.get("target"), item.get("fault_id")
            if target and kind:
                found[canonical(str(target))] = (str(kind), item.get("code"))
        for item in output.get("active_now") or []:
            target, kind = item.get("target"), item.get("fault")
            if target and kind:
                found.setdefault(canonical(str(target)), (str(kind), None))
    return found


def _containers(results: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, output in results:
        if name == "get_container_health" and output.get("container"):
            out[canonical(str(output["container"]))] = output
    return out


def _drift(results: list[tuple[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]], str | None]:
    """Drift items from get_drift (most causal first) and its evidence code."""
    for name, output in reversed(results):
        if name == "get_drift" and not output.get("error"):
            return list(output.get("items") or []), output.get("evidence_code")
    return [], None


def _drift_for(items: list[dict[str, Any]], service: str) -> dict[str, Any] | None:
    """The most causal drift item that explains `service`'s symptoms."""
    for item in items:
        explains = {canonical(str(name)) for name in item.get("explains") or []}
        if service in explains or canonical(str(item.get("service") or "")) == service:
            return item
    return None


def _container_cause(info: dict[str, Any] | None) -> tuple[str, str] | None:
    if not info:
        return None
    state = str(info.get("state") or "")
    if state and state != "running":
        return ("dependency_unavailable", f"Docker reports its container as {state}")
    if info.get("paused"):
        return ("dependency_unavailable", "Docker reports its container as paused")
    if info.get("networks") == []:
        return ("network", "Docker shows its container detached from every network")
    if info.get("oom_killed"):
        return ("resource_exhaustion", "Docker reports an out-of-memory kill")
    return None


def diagnose(
    context: dict[str, Any],
    results: list[tuple[str, dict[str, Any]]],
    *,
    drop: set[str] | None = None,
) -> dict[str, Any]:
    drop = drop or set()
    suspects = context.get("suspects") or []
    services = {canonical(str(item)) for item in context.get("services") or []}
    fault_targets = {canonical(str(item)) for item in context.get("fault_targets") or []}
    faults = _faults(results)
    containers = _containers(results)

    if not suspects and _drift(results)[0]:
        items, code = _drift(results)
        top = items[0]
        service = canonical(str(top.get("service") or "unknown"))
        fix = top.get("fix") or {"action_id": "escalate_human", "target": "incident"}
        return {
            "root_cause": {
                "service": service,
                "fault_class": str(top.get("fault_class") or "unknown"),
            },
            "confidence": 0.75,
            "summary": (
                f"No suspect ranked from telemetry, but {service} drifted from the desired "
                f"state ({top.get('kind')}); that drift is the most likely cause."
            ),
            "evidence_codes": [code] if code and code not in drop else ["E0001"],
            "affected_services": [service],
            "recommended_action": {"action_id": fix["action_id"], "target": fix["target"]},
            "alternative_hypotheses": [],
        }
    if not suspects:
        return {
            "root_cause": {
                "service": context.get("incident", {}).get("primary_service") or "unknown",
                "fault_class": "unknown",
            },
            "confidence": 0.2,
            "summary": "No suspect could be ranked from the collected evidence.",
            "evidence_codes": [c for c in _all_codes(results) if c not in drop][:1] or ["E0001"],
            "affected_services": [],
            "recommended_action": {"action_id": "escalate_human", "target": "incident"},
            "alternative_hypotheses": [],
        }

    ranked = [canonical(str(item["service"])) for item in suspects]
    drift_items, drift_code = _drift(results)
    root_index = 0
    drifted: dict[str, Any] | None = None
    for index, service in enumerate(ranked[:3]):
        if service in faults or service in fault_targets:
            root_index = index
            break
        drifted = _drift_for(drift_items, service)
        if drifted is not None:
            root_index = index
            break
    if (
        drifted is None
        and root_index == 0
        and drift_items
        and not (ranked[0] in faults or ranked[0] in fault_targets)
    ):
        primary = canonical(str((context.get("incident") or {}).get("primary_service") or ""))
        drifted = _drift_for(drift_items, primary) if primary else None
    root_row = suspects[root_index]
    root = ranked[root_index]
    via: str | None = None
    if drifted is not None:
        drift_root = canonical(str(drifted.get("service") or root))
        if drift_root != root:
            via = root
            root = drift_root

    cause = _container_cause(containers.get(root))
    fault = faults.get(root)
    if drifted is not None and fault is None:
        fault_class = str(drifted.get("fault_class") or "unknown")
        reason = f"its live state drifted from the desired state: {drifted.get('kind')}"
        if via is not None:
            reason += f", and {via} depends on it"
    elif fault is not None and fault[0] in _FAULT_CLASS:
        fault_class, reason = _FAULT_CLASS[fault[0]]
        reason = f"a recorded {fault[0].replace('_', ' ')} change targets it: {reason}"
    elif cause is not None:
        fault_class, reason = cause
    else:
        metric = _metric_for(root_row)
        fault_class = "traffic" if metric == "throughput" else "application_error"
        reason = {
            "throughput": "its request rate departs from the baseline first",
            "p95_latency": "its latency rises before its callers' latency",
        }.get(metric, "the failing spans originate in it, not in its dependencies")
    direct = fault is not None or cause is not None or drifted is not None

    # Citations: the suspect's own codes, the fault record, and tool observations
    # about the root (everything here was shown in context or returned by a tool).
    codes: list[str] = []

    def cite(code: Any) -> None:
        if (
            isinstance(code, str)
            and _CODE.fullmatch(code)
            and code not in drop
            and code not in codes
        ):
            codes.append(code)

    for code in root_row.get("evidence_codes") or []:
        cite(code)
    if fault is not None:
        cite(fault[1])
    if drifted is not None:
        cite(drift_code)
    for name, output in results:
        if output.get("error"):
            continue
        about = canonical(str(output.get("service") or output.get("container") or ""))
        if about == root or name == "get_active_flags":
            cite(output.get("evidence_code"))
    codes = codes[:20]

    scores = [float(item.get("score") or 0) for item in suspects]
    top_score = scores[root_index]
    others = [score for index, score in enumerate(scores) if index != root_index]
    runner_up = max(others) if others else 0.0
    margin = (top_score - runner_up) / top_score if top_score > 0 else 0.0
    confidence = round(min(0.95, 0.5 + 0.3 * max(0.0, margin) + (0.15 if direct else 0.0)), 2)
    if drifted is not None and drifted.get("fix"):
        # Drift is an observation, not an inference: the component's live state
        # differs from its desired state and a known fix exists. A tie between
        # suspects (a dependency with no spans and its caller) must not drop a
        # directly observed cause below the gate.
        confidence = max(confidence, 0.8)

    action = _action(root, fault_class, fault_targets | set(faults), direct=fault is not None)
    fix = (drifted or {}).get("fix") if fault is None else None
    if isinstance(fix, dict) and fix.get("action_id") and fix.get("target"):
        action = {"action_id": str(fix["action_id"]), "target": str(fix["target"])}

    affected = [root] + ([via] if via else [])
    for item in (context.get("incident") or {}).get("signals") or []:
        service = canonical(str(item.get("service") or ""))
        if service and service in services and service not in affected:
            affected.append(service)

    alternatives = [
        {
            "service": ranked[index],
            "confidence": round(
                max(
                    0.0,
                    min(
                        1.0 - confidence, scores[index] / max(top_score, 1e-6) * (1.0 - confidence)
                    ),
                ),
                2,
            ),
        }
        for index in range(min(len(ranked), 4))
        if index != root_index
    ][:3]

    callers = [service for service in affected if service != root]
    summary = f"{root} is the most likely root cause: {reason}."
    if via is not None:
        summary += f" It emits no spans of its own; {via} shows the symptoms."
    elif root_index == 0:
        summary += " It also ranks first among the deterministic suspects."
    else:
        summary += f" It outranks the top-ranked suspect {ranked[0]} because of that change."
    if cause is not None and fault is not None:
        summary += f" {cause[1]}."
    if callers:
        summary += f" Symptoms in {', '.join(callers[:4])} are consequences of it."

    return {
        "root_cause": {"service": root, "fault_class": fault_class},
        "confidence": confidence,
        "summary": summary,
        "evidence_codes": codes or [c for c in _all_codes(results) if c not in drop][:3],
        "affected_services": affected[:10],
        "recommended_action": action,
        "alternative_hypotheses": alternatives,
    }


def _action(
    root: str, fault_class: str, fault_targets: set[str], *, direct: bool
) -> dict[str, str]:
    if direct and root in fault_targets:
        return {"action_id": "reset_fault", "target": root}
    if fault_class == "traffic":
        return {"action_id": "stop_load_generator", "target": "load-generator"}
    if root in SERVICE_TARGETS:
        if fault_class == "dependency_unavailable":
            return {"action_id": "restart_service", "target": root}
        if fault_class == "network":
            return {"action_id": "restore_network", "target": root}
        if fault_class == "resource_exhaustion":
            return {"action_id": "remove_resource_limit", "target": root}
    return {"action_id": "escalate_human", "target": "incident"}


def _all_codes(results: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [
        str(output["evidence_code"])
        for _name, output in results
        if isinstance(output.get("evidence_code"), str)
    ]


def investigator_policy(
    messages: list[ChatMessage], tools: list[ToolSpec] | None, json_mode: bool
) -> StubReply:
    context = _context(messages)
    results = _tool_results(messages)
    if tools:
        turn = sum(1 for message in messages if message.role == "assistant" and message.tool_calls)
        calls = _plan(turn, context, {spec.name for spec in tools})
        if calls:
            return calls
    drop: set[str] = set()
    last = messages[-1] if messages else None
    if last is not None and last.role == "user" and last.content and "rejected" in last.content:
        drop = set(_CODE.findall(last.content))
    return json.dumps(diagnose(context, results, drop=drop))


__all__ = ["diagnose", "investigator_policy"]
