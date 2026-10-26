"""Markdown export of the JSON report. Pure: renders the document only."""

from __future__ import annotations

import json
from typing import Any


def _cell(value: Any, limit: int = 160) -> str:
    if value is None or value == "":
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return [*lines, ""]


def _pct(value: Any) -> str:
    return f"{value:.1%}" if isinstance(value, int | float) else "—"


def _num(value: Any, unit: str = "") -> str:
    return f"{value:.0f}{unit}" if isinstance(value, int | float) else "—"


def _rate(value: Any) -> str:
    return f"{value:.2f}/s" if isinstance(value, int | float) else "—"


def _before_after(verification: dict[str, Any]) -> list[str]:
    before = (verification.get("before") or {}).get("metrics") or {}
    after = (verification.get("after") or {}).get("metrics") or {}
    rows = []
    for service in sorted(set(before) | set(after)):
        b, a = before.get(service) or {}, after.get(service) or {}
        rows.append(
            [
                service,
                f"{_pct(b.get('error_rate'))} → {_pct(a.get('error_rate'))}",
                f"{_num(b.get('p95_latency'), ' ms')} → {_num(a.get('p95_latency'), ' ms')}",
                f"{_rate(b.get('throughput'))} → {_rate(a.get('throughput'))}",
            ]
        )
    return _table(["Service", "Error rate", "p95", "Throughput"], rows)


def _state_line(state: dict[str, Any]) -> str:
    container = state.get("container") or {}
    parts = []
    if container:
        parts.append(
            f"container {container.get('status')}"
            f" (health {container.get('health')}, networks {container.get('networks')})"
        )
    if state.get("flags"):
        parts.append(f"flags {state['flags']}")
    return "; ".join(parts) or "—"


def render(report: dict[str, Any]) -> str:
    incident = report["incident"]
    outcome = report["outcome"]
    out: list[str] = [
        f"# {incident['ref']}: {incident['title']}",
        "",
        f"_Generated {report['generated_at']} · schema `{report['schema']}`_",
        "",
        "## Summary",
        "",
    ]
    out += _table(
        ["Field", "Value"],
        [
            ["Status", incident["status"]],
            ["Severity", incident["severity"]],
            ["Primary service", incident["primary_service"]],
            ["Opened", incident["opened_at"]],
            ["Closed", incident["closed_at"]],
            ["Duration", f"{incident['duration_s']} s" if incident["duration_s"] else None],
            ["Action", outcome.get("action")],
            [
                "Verification",
                f"{outcome['verdict']} after {outcome['observed_for_s']} s"
                if outcome.get("verdict")
                else None,
            ],
            ["Closed by", outcome.get("closed_by")],
        ],
    )

    diagnosis = report.get("diagnosis")
    out += ["## Diagnosis", ""]
    if diagnosis:
        action = diagnosis.get("recommended_action") or {}
        out += [
            f"**Root cause:** `{diagnosis['root_cause_service']}` ({diagnosis['fault_class']}), "
            f"confidence {diagnosis['confidence']:.2f}"
            + ("" if diagnosis.get("valid") else " — **not valid**"),
            "",
            diagnosis["summary"],
            "",
            f"**Recommended action:** `{action.get('action_id')}` on `{action.get('target')}`",
            "",
            f"**Affected services:** {', '.join(diagnosis.get('affected_services') or []) or '—'}",
            "",
        ]
        for alt in diagnosis.get("alternative_hypotheses") or []:
            out.append(f"- Alternative: {_cell(alt, 240)}")
        if diagnosis.get("alternative_hypotheses"):
            out.append("")
    else:
        out += ["_No diagnosis was produced._", ""]

    evidence = report["evidence"]
    out += ["### Cited evidence", ""]
    out += _table(
        ["Code", "Type", "Source", "Service", "At", "Summary"],
        [
            [e["code"], e["type"], e["source"], e["service"], e["at"], e["summary"]]
            for e in evidence["cited"]
        ],
    )
    if evidence.get("missing_citations"):
        out += [f"**Missing citations:** {', '.join(evidence['missing_citations'])}", ""]

    out += ["## Suspects", ""]
    out += _table(
        ["Rank", "Service", "Score"],
        [[s["rank"], s["service"], s["score"]] for s in report["suspects"]],
    )

    out += ["## Remediation", ""]
    if not report["remediation"]:
        out += ["_No action was proposed._", ""]
    for item in report["remediation"]:
        action = item["action"]
        approval = item.get("approval") or {}
        out += [
            f"### Action #{action['id']}: `{action['action_id']}` on `{action['target']}`",
            "",
        ]
        out += _table(
            ["Field", "Value"],
            [
                ["Status", action["status"]],
                ["Risk / approval", f"{action.get('risk')} / {action.get('approval_mode')}"],
                ["Requested by", action.get("requested_by")],
                [
                    "Decision",
                    f"{approval.get('status')} by {approval.get('decided_by')} at "
                    f"{approval.get('decided_at')}"
                    if approval
                    else None,
                ],
                ["Note", approval.get("note")],
                [
                    "Token",
                    (
                        f"issued={approval.get('token_issued')}, used={approval.get('token_used')}"
                        " (never stored in clear)"
                    )
                    if approval
                    else None,
                ],
                ["Started", action.get("started_at")],
                ["Finished", action.get("finished_at")],
                ["Error", action.get("error")],
                ["Before", _state_line(action.get("pre_snapshot") or {})],
                ["After", _state_line(action.get("post_snapshot") or {})],
            ],
        )
        steps = (action.get("result") or {}).get("steps") or []
        if steps:
            out += ["Steps:", ""] + [f"- {_cell(step, 240)}" for step in steps] + [""]
        rollback = action.get("rollback")
        if rollback:
            out += [f"Rollback: {_cell(rollback, 400)}", ""]
        verification = item.get("verification")
        if verification:
            criteria = verification.get("criteria") or {}
            out += [
                f"#### Verification: **{verification['verdict']}** "
                f"after {verification['observed_for_s']} s",
                "",
                f"Focus services: {', '.join(criteria.get('focus') or []) or '—'}; "
                f"clean cycles {criteria.get('clean_cycles')}/"
                f"{criteria.get('required_clean_cycles')}; timeout {criteria.get('timeout_s')} s.",
                "",
            ]
            out += _table(
                ["Criterion", "Result", "Detail"],
                [
                    [
                        c.get("label") or c.get("id"),
                        "pass" if c.get("ok") else "FAIL",
                        c.get("detail"),
                    ]
                    for c in criteria.get("final") or []
                ],
            )
            out += ["Before → after (detector window):", ""]
            out += _before_after(verification)

    out += ["## Timeline", ""]
    out += _table(
        ["#", "At", "Kind", "Detail"],
        [[e["seq"], e["at"], e["kind"], e["detail"]] for e in report["timeline"]],
    )

    out += ["## Faults overlapping the incident", ""]
    out += _table(
        ["#", "Fault", "Target", "Status", "Injected", "Reset"],
        [
            [f["id"], f["fault_id"], f["target"], f["status"], f["injected_at"], f.get("reset_at")]
            for f in report["faults"]
        ],
    )

    out += ["## Agent runs", ""]
    out += _table(
        ["Run", "Status", "Model", "Tokens in/out", "Cost", "Latency"],
        [
            [
                r["id"],
                r["status"],
                f"{r['provider']}/{r['model']}",
                f"{r['tokens_in']}/{r['tokens_out']}",
                f"${r['cost_usd']:.4f}",
                f"{r['latency_ms']} ms" if r["latency_ms"] is not None else None,
            ]
            for r in report["agent_runs"]
        ],
    )

    out += ["## Audit trail", ""]
    out += _table(
        ["At", "Actor", "Event", "Outcome", "Detail"],
        [[a["at"], a["actor"], a["event"], a["outcome"], a["detail"]] for a in report["audit"]],
    )

    out += ["## Evidence index", ""]
    out += _table(
        ["Code", "Type", "Service", "Summary"],
        [[e["code"], e["type"], e["service"], e["summary"]] for e in evidence["index"]],
    )
    return "\n".join(out).rstrip() + "\n"


__all__ = ["render"]
