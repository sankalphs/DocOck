"""The PLAN §12 memory template, filled from stored rows only — never raw logs.

    <service>. <fault class>. <symptom phrase>.
    Onset: <service> errors preceded <downstream> by <x>s.
    Container: <healthy|crashed|oom|paused|detached>. Restart: <helped|no effect|not attempted>.
    Action: <action_id> on <target>. Verification: <verdict>.

The same builder produces the *query* for retrieval while an incident is still
open: the root is then the leading suspect and the class may be unknown, so the
query reads like the memories it should match.

Nothing here contains an evidence code: a memory is never evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agent.validator import canonical
from app.analysis.suspects import NOT_SUSPECTS
from app.db.models import Evidence, Incident

CPU_CAP_LIMITED = 2.0
SIGNAL_PHRASES = {
    "error_rate": "error rate breach",
    "error_count": "error burst",
    "p95_latency": "latency breach",
    "throughput": "throughput anomaly",
    "silence": "telemetry silence",
    "container": "container failure",
}


@dataclass
class MemoryFacts:
    root: str
    fault_class: str
    symptom: str
    onset: str
    container: str
    restart: str
    action: str
    verification: str
    services: list[str]

    def text(self) -> str:
        return (
            f"{self.root}. {self.fault_class}. {self.symptom}.\n"
            f"Onset: {self.onset}.\n"
            f"Container: {self.container}. Restart: {self.restart}.\n"
            f"Action: {self.action}. Verification: {self.verification}."
        )


def symptom_phrase(incident: Incident) -> str:
    trigger = incident.trigger or {}
    signals = [item for item in trigger.get("signals") or [] if isinstance(item, dict)]
    primary = str(trigger.get("primary_signal") or (signals[0].get("signal") if signals else ""))
    where = canonical(incident.primary_service or "") or "unknown service"
    phrase = SIGNAL_PHRASES.get(primary, primary.replace("_", " ") or "anomaly")
    others = sorted(
        {canonical(str(item.get("service"))) for item in signals if item.get("service")} - {where}
    )
    tail = f" with {', '.join(others[:3])}" if others else ""
    return f"{phrase} at {where}{tail}"


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def onset_phrase(root: str, evidence: list[Evidence]) -> str:
    """`<root> errors preceded <downstream> by <x>s`, from the onset rows."""
    onsets: dict[str, datetime] = {}
    for row in evidence:
        if row.type != "onset" or not row.service:
            continue
        at = _parse((row.data or {}).get("first_anomalous_span_at"))
        if at is not None:
            service = canonical(row.service)
            if service in NOT_SUSPECTS:
                continue  # infrastructure (flagd, collector) is never "downstream"
            onsets[service] = min(at, onsets.get(service, at))
    if root not in onsets:
        return f"no onset measured for {root}"
    later = {svc: at for svc, at in onsets.items() if svc != root and at >= onsets[root]}
    if not later:
        earlier = [svc for svc in onsets if svc != root]
        return (
            f"{earlier[0]} errors preceded {root}"
            if earlier
            else f"{root} errors were the only onset"
        )
    downstream = min(later, key=lambda svc: later[svc])
    delta = (later[downstream] - onsets[root]).total_seconds()
    if delta < 1:
        return f"{root} and {downstream} errors started together"
    return f"{root} errors preceded {downstream} by {round(delta)}s"


def container_phrase(root: str, evidence: list[Evidence]) -> str:
    rows = [
        row
        for row in evidence
        if row.type in ("container_state", "container_health")
        and canonical(row.service or "") == root
    ]
    if not rows:
        return "not observed"
    data = rows[0].data or {}  # the analysis row: state during the incident
    state = str(data.get("state") or "")
    if data.get("oom_killed"):
        return "oom"
    if data.get("paused") or state == "paused":
        return "paused"
    if state and state != "running":
        return "crashed"
    if data.get("networks") == []:
        return "detached"
    # Demo containers always carry a memory limit (and `ad` a 24-core CPU cap);
    # only a cap within the cpu_pressure range (<= 2 cores) is the CPU-pressure signature.
    cpu = data.get("cpu_limit_cores")
    if isinstance(cpu, int | float) and 0 < cpu <= CPU_CAP_LIMITED:
        return "limited"
    return "healthy"


def build_facts(
    incident: Incident,
    evidence: list[Evidence],
    *,
    root: str,
    fault_class: str,
    action: dict[str, Any] | None,
    affected: list[str],
    verification: str,
    restart: str = "not attempted",
) -> MemoryFacts:
    root = canonical(root)
    action_text = (
        f"{action.get('action_id')} on {action.get('target')}"
        if action and action.get("action_id")
        else "none recorded"
    )
    services = list(dict.fromkeys([root, *(canonical(item) for item in affected if item)]))
    return MemoryFacts(
        root=root,
        fault_class=fault_class,
        symptom=symptom_phrase(incident),
        onset=onset_phrase(root, evidence),
        container=container_phrase(root, evidence),
        restart=restart,
        action=action_text,
        verification=verification,
        services=services[:12],
    )


def query_text(incident: Incident, evidence: list[Evidence], *, lead: str) -> str:
    """Retrieval query for an open incident: the template's first three lines."""
    lead = canonical(lead)
    return (
        f"{lead}. {symptom_phrase(incident)}.\n"
        f"Onset: {onset_phrase(lead, evidence)}.\n"
        f"Container: {container_phrase(lead, evidence)}."
    )


__all__ = [
    "SIGNAL_PHRASES",
    "MemoryFacts",
    "build_facts",
    "container_phrase",
    "onset_phrase",
    "query_text",
    "symptom_phrase",
]
