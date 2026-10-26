"""Diagnosis validation (PLAN §11). Pure functions; no I/O.

Hard rejects (the orchestrator allows one repair attempt, then escalates):

1. every cited `E####` exists **in this incident** and was shown to the model;
2. `root_cause.service` is in the incident's observed topology;
3. the JSON matches the schema (`confidence ∈ [0, 1]`, known fault class, …);
4. `recommended_action` is in the catalog and allows that target here;
5. summary ≤ 1,200 chars, no markdown tables, and every metric-like number in
   it traces to the data of a cited evidence row;
6. `affected_services` ⊆ known services.

The confidence gate is separate: a valid diagnosis below the threshold is stored
as valid and escalated, it is not "invalid".
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.agent.schemas import SUMMARY_MAX_CHARS, Diagnosis, ValidationReport
from app.analysis.suspects import SERVICE_ALIASES
from app.evidence.store import is_valid_code
from app.remediation.catalog import target_allowed

# A number in prose: not part of an identifier (E0021, INC-00042, p95, v1.2),
# optionally followed by a unit.
_PROSE_NUMBER = re.compile(
    r"(?<![\w.\-])(\d+(?:\.\d+)?)(?=(?:ms|s|x|mb|MB|MiB|m|h|%|/s|rps)?(?![\w.]))"
)
_DATA_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$|\|\s*:?-{3,}")
# Ratios become percentages, milliseconds become seconds, MB become GB.
_SCALES = (1.0, 100.0, 0.001, 1000.0)


@dataclass
class ValidationContext:
    incident_codes: set[str]
    shown_codes: set[str]
    evidence_data: dict[str, Any]
    topology_services: set[str]
    known_services: set[str]
    fault_targets: set[str] = field(default_factory=set)
    flag_targets: set[str] = field(default_factory=set)
    threshold: float = 0.7


def canonical(service: str) -> str:
    return SERVICE_ALIASES.get(service, service)


def extract_json(text: str | None) -> dict[str, Any] | None:
    """The first JSON object in a model reply (tolerates ``` fences and prose)."""
    if not text:
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _numbers_in(value: Any) -> list[float]:
    text = json.dumps(value, default=str)
    return [float(item) for item in _DATA_NUMBER.findall(text)]


def untraced_numbers(summary: str, evidence: Iterable[Any]) -> list[str]:
    """Numbers in the summary that no cited evidence row contains.

    Single-digit integers are exempt: they are counts ("3 traces"), not
    metric values. Everything else must match a cited value within 2 %
    (after unit scaling) or be its rounding.
    """
    pool: list[float] = []
    for item in evidence:
        pool.extend(_numbers_in(item))
    missing: list[str] = []
    for raw in _PROSE_NUMBER.findall(summary):
        number = float(raw)
        if "." not in raw and number < 10:
            continue
        if not any(_matches(number, value, integer="." not in raw) for value in pool):
            missing.append(raw)
    return sorted(set(missing), key=missing.index)


def _matches(number: float, value: float, *, integer: bool) -> bool:
    for scale in _SCALES:
        target = value * scale
        if abs(number - target) <= max(0.02 * abs(target), 0.051):
            return True
        if integer and round(target) == number:
            return True
    return False


def validate(raw: dict[str, Any] | None, ctx: ValidationContext) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    if raw is None:
        return ValidationReport(
            valid=False, errors=["reply is not a JSON object"], threshold=ctx.threshold
        )
    try:
        diagnosis = Diagnosis.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            errors.append(f"schema: {loc}: {error['msg']}")
        return ValidationReport(valid=False, errors=errors, threshold=ctx.threshold)

    # Telemetry aliases are folded to deployables (frontend-web -> frontend).
    root = canonical(diagnosis.root_cause.service)
    if root != diagnosis.root_cause.service:
        warnings.append(f"root cause {diagnosis.root_cause.service} folded to {root}")
        diagnosis.root_cause.service = root
    target = canonical(diagnosis.recommended_action.target)
    diagnosis.recommended_action.target = target
    diagnosis.affected_services = list(
        dict.fromkeys(canonical(item) for item in diagnosis.affected_services)
    )
    codes = list(dict.fromkeys(diagnosis.evidence_codes))
    if len(codes) != len(diagnosis.evidence_codes):
        warnings.append("duplicate evidence codes removed")
    diagnosis.evidence_codes = codes

    # Rule 1: evidence codes.
    for code in codes:
        if not is_valid_code(code):
            errors.append(f"evidence: {code!r} is not an E#### code")
        elif code not in ctx.incident_codes:
            errors.append(f"evidence: {code} does not exist in this incident")
        elif code not in ctx.shown_codes:
            errors.append(f"evidence: {code} exists but was never shown to the model")

    # Rule 2: root cause in the observed topology.
    if root not in ctx.topology_services:
        errors.append(
            f"root_cause.service {root!r} is not in this incident's topology "
            f"({', '.join(sorted(ctx.topology_services)) or 'empty'})"
        )

    # Rule 4: action catalog + target.
    reason = target_allowed(
        diagnosis.recommended_action.action_id,
        target,
        fault_targets=ctx.fault_targets,
        flag_targets=ctx.flag_targets,
        known_services=ctx.known_services,
    )
    if reason:
        errors.append(f"recommended_action: {reason}")

    # Rule 5: summary.
    summary = diagnosis.summary
    if len(summary) > SUMMARY_MAX_CHARS:
        errors.append(f"summary: {len(summary)} chars > {SUMMARY_MAX_CHARS}")
    if any(_TABLE_LINE.search(line) for line in summary.splitlines()):
        errors.append("summary: markdown tables are not allowed")
    cited = [ctx.evidence_data[code] for code in codes if code in ctx.evidence_data]
    missing = untraced_numbers(summary, cited)
    if missing:
        errors.append(
            "summary: numbers not found in the cited evidence: "
            + ", ".join(missing[:8])
            + " (cite the evidence that contains them or drop them)"
        )

    # Rule 6: affected services.
    unknown = [item for item in diagnosis.affected_services if item not in ctx.known_services]
    if unknown:
        errors.append(f"affected_services: unknown services {', '.join(unknown)}")

    for alt in diagnosis.alternative_hypotheses:
        alt.service = canonical(alt.service)
        if alt.service not in ctx.known_services:
            warnings.append(f"alternative hypothesis {alt.service!r} is not a known service")

    valid = not errors
    return ValidationReport(
        valid=valid,
        errors=errors,
        warnings=warnings,
        passes_gate=valid and diagnosis.confidence >= ctx.threshold,
        threshold=ctx.threshold,
        diagnosis=diagnosis,
    )


__all__ = ["ValidationContext", "canonical", "extract_json", "untraced_numbers", "validate"]
