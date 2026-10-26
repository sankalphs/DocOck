"""Suspect scoring.

Weights are configuration, not magic numbers scattered in code:

| signal | weight | why |
|---|---|---|
| error origin | 0.30 | who failed first-party vs propagated |
| earliest onset | 0.20 | the fault usually starts at the affected service |
| local error ratio | 0.20 | share of the service's errors that are not downstream |
| latency anomaly | 0.10 | catches slow-path faults without errors |
| throughput anomaly | 0.10 | catches traffic faults (no starter-weight slot for `traffic_flood`) |
| container unhealthy | 0.10 | stopped/paused/unhealthy/limited beats a noisy metric |

Scores are normalised to sum to 1 and always carry the contributing signals —
the UI must show *why*.
"""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field

from app.analysis.metrics import MetricAnomaly
from app.analysis.signals import ContainerSignal, FaultSignal
from app.analysis.traces import OriginStats
from app.config import settings


class SuspectScore(BaseModel):
    service: str
    score: float
    rank: int
    signals: dict[str, dict[str, float | str | None]] = Field(default_factory=dict)


# The lab's own machinery is never a suspect: the load generator produces the
# traffic, flagd serves the flags, and the observability stack stores the
# evidence. An operator cannot "fix" them, and blaming the injector would hide
# the service that actually degraded.
NOT_SUSPECTS = frozenset(
    {
        "load-generator",
        "flagd",
        "flagd-ui",
        "opamp-server",
        "telemetry-docs",
        "prometheus",
        "jaeger",
        "grafana",
        "opensearch",
        "otel-collector",
    }
)


# Telemetry names that are not deployables. `frontend-web` is the browser bundle
# the frontend container serves (its OTel web tracer names itself that way);
# there is no frontend-web container to inspect, restart or roll back.
SERVICE_ALIASES = {"frontend-web": "frontend"}


def _canonical(service: str) -> str:
    return SERVICE_ALIASES.get(service, service)


def _merge_origins(origins: dict[str, OriginStats]) -> dict[str, OriginStats]:
    merged: dict[str, OriginStats] = {}
    for service, stats in origins.items():
        name = _canonical(service)
        into = merged.get(name)
        if into is None:
            merged[name] = stats.model_copy(update={"service": name, "peers": dict(stats.peers)})
            continue
        into.local_errors += stats.local_errors
        into.propagated_errors += stats.propagated_errors
        into.silent_peer_errors += stats.silent_peer_errors
        into.origins += stats.origins
        for peer, count in stats.peers.items():
            into.peers[peer] = into.peers.get(peer, 0) + count
    return merged


def _merge_onset(onset: dict[str, datetime]) -> dict[str, datetime]:
    merged: dict[str, datetime] = {}
    for service, at in onset.items():
        name = _canonical(service)
        merged[name] = min(at, merged.get(name, at))
    return merged


WEIGHTS = {
    "error_origin": "suspect_weight_error_origin",
    "earliest_onset": "suspect_weight_onset",
    "local_error_ratio": "suspect_weight_local_ratio",
    "latency_anomaly": "suspect_weight_latency",
    "throughput_anomaly": "suspect_weight_throughput",
    "container_unhealthy": "suspect_weight_container",
}


def weights(*, load_incident: bool = False, latency_incident: bool = False) -> dict[str, float]:
    raw = {name: max(0.0, float(getattr(settings, attr))) for name, attr in WEIGHTS.items()}
    if load_incident:
        # In a traffic incident every service that the flood reaches shows
        # errors and latency; those are consequences of the load, not local
        # defects, so only load-shaped signals decide the ranking.
        for name in ("error_origin", "earliest_onset", "local_error_ratio"):
            raw[name] = 0.0
    if latency_incident:
        # An error at the edge (a proxy 504) is a *consequence* of the delay;
        # the delay itself is attributed through completion-order onset and the
        # latency anomaly.
        for name in ("error_origin", "local_error_ratio"):
            raw[name] = 0.0
    total = sum(raw.values()) or 1.0
    return {name: value / total for name, value in raw.items()}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_suspects(
    *,
    opened_at: datetime,
    origins: dict[str, OriginStats],
    onset: dict[str, datetime],
    anomalies: list[MetricAnomaly],
    containers: dict[str, ContainerSignal],
    faults: list[FaultSignal],
    incident_services: list[str],
    primary_service: str | None,
    load_incident: bool = False,
    latency_incident: bool = False,
) -> list[SuspectScore]:
    weights_map = weights(load_incident=load_incident, latency_incident=latency_incident)
    origins = _merge_origins(origins)
    onset = _merge_onset(onset)
    # Same metric order as the scan; within a metric the strongest reading comes
    # first, so an alias and its deployable keep the larger deviation.
    anomalies = sorted(
        (item.model_copy(update={"service": _canonical(item.service)}) for item in anomalies),
        key=lambda item: (
            item.metric,
            -abs(math.log(item.ratio)) if item.ratio and item.ratio > 0 else 0.0,
        ),
    )
    containers = {_canonical(name): signal for name, signal in containers.items()}
    incident_services = [_canonical(service) for service in incident_services]
    primary_service = _canonical(primary_service) if primary_service else None
    total_origins = sum(stats.origins for stats in origins.values()) or 1
    earliest = min(onset.values()) if onset else None
    latency_by_service: dict[str, MetricAnomaly] = {}
    throughput_by_service: dict[str, MetricAnomaly] = {}
    for anomaly in anomalies:
        if anomaly.metric in ("p95_latency", "container_cpu", "container_memory"):
            latency_by_service.setdefault(anomaly.service, anomaly)
        elif anomaly.metric == "throughput":
            throughput_by_service.setdefault(anomaly.service, anomaly)
    largest_traffic_delta = (
        max((abs(item.delta) for item in throughput_by_service.values()), default=0.0) or 1.0
    )

    container_fault_targets = {
        fault.target
        for fault in faults
        if fault.mechanism
        in ("container_stop", "container_pause", "network_partition", "resource_limit")
        and fault.status in ("PROPAGATING", "ACTIVE", "REVERTING")
    }

    universe = (
        set(origins)
        | set(onset)
        | {item.service for item in anomalies}
        | set(containers)
        | set(container_fault_targets)
        | set(incident_services)
        | ({primary_service} if primary_service else set())
    ) - NOT_SUSPECTS

    scored: list[
        tuple[float, float, float, float, str, dict[str, dict[str, float | str | None]]]
    ] = []
    for service in sorted(universe):
        stats = origins.get(service)
        origin_value = (stats.origins / total_origins) if stats else 0.0
        onset_value = 0.0
        onset_detail = "no anomalous span in the sample"
        if earliest is not None and service in onset:
            delta = max(0.0, (onset[service] - earliest).total_seconds())
            onset_value = math.exp(-delta / 120.0)
            onset_detail = f"first anomalous span {onset[service].isoformat()}" + (
                f" (+{delta:.0f}s)" if delta else " (earliest)"
            )
        local_ratio = 0.0
        if stats and (stats.local_errors + stats.propagated_errors) > 0:
            local_ratio = stats.local_errors / (stats.local_errors + stats.propagated_errors)
        latency_anomaly = latency_by_service.get(service)
        latency_value = 0.0
        latency_detail = "no latency anomaly"
        if latency_anomaly is not None:
            ratio = max(latency_anomaly.ratio or 1.0, 1.0)
            # Logarithmic: a 3x slowdown and a 200x slowdown are not equally
            # suspicious, but the 200x one must not swamp the rest either.
            latency_value = _clamp(math.log(ratio) / math.log(20.0))
            latency_detail = latency_anomaly.detail
        throughput_anomaly = throughput_by_service.get(service)
        throughput_value = 0.0
        throughput_detail = "no throughput anomaly"
        if throughput_anomaly is not None:
            # Scaled by this service's share of the largest absolute change: a
            # flood enters where the most requests appear, and a 0.2 req/s
            # downstream service doubling is a fraction of that, not a tie.
            share = abs(throughput_anomaly.delta) / largest_traffic_delta
            throughput_value = _clamp(abs((throughput_anomaly.ratio or 1.0) - 1.0)) * share
            throughput_detail = throughput_anomaly.detail
        container = containers.get(service)
        container_value = 0.0
        container_detail = "container healthy"
        observed_bad = 1.0 if container is not None and container.unhealthy else 0.0
        if observed_bad:
            container_value = 1.0
            container_detail = container.detail if container is not None else ""
        elif service in container_fault_targets:
            container_value = 1.0
            container_detail = "an active container/resource fault targets this service"

        signals: dict[str, dict[str, float | str | None]] = {
            "error_origin": {
                "value": round(origin_value, 4),
                "weight": round(weights_map["error_origin"], 4),
                "detail": (
                    f"{stats.origins if stats else 0} origin span(s): "
                    f"{stats.local_errors if stats else 0} local, "
                    f"{stats.silent_peer_errors if stats else 0} silent peer"
                ),
            },
            "earliest_onset": {
                "value": round(onset_value, 4),
                "weight": round(weights_map["earliest_onset"], 4),
                "detail": onset_detail,
            },
            "local_error_ratio": {
                "value": round(local_ratio, 4),
                "weight": round(weights_map["local_error_ratio"], 4),
                "detail": f"{local_ratio:.0%} of this service's sampled errors are local",
            },
            "latency_anomaly": {
                "value": round(latency_value, 4),
                "weight": round(weights_map["latency_anomaly"], 4),
                "detail": latency_detail,
            },
            "throughput_anomaly": {
                "value": round(throughput_value, 4),
                "weight": round(weights_map["throughput_anomaly"], 4),
                "detail": throughput_detail,
            },
            "container_unhealthy": {
                "value": round(container_value, 4),
                "weight": round(weights_map["container_unhealthy"], 4),
                "detail": container_detail,
            },
        }
        weighted = sum(
            weights_map[name] * float(signal["value"] or 0.0) for name, signal in signals.items()
        )
        for name, signal in signals.items():
            signal["contribution"] = round(weights_map[name] * float(signal["value"] or 0.0), 4)
        if weighted > 0.005:
            scored.append(
                (weighted, observed_bad, container_value, throughput_value, service, signals)
            )

    total = sum(item[0] for item in scored) or 1.0
    # Direct observations first. A container Docker reports stopped/paused is a
    # cause, not a statistic: no amount of downstream latency should outrank it.
    # Then the traffic signal: when two services are explained equally well, the
    # one whose throughput actually moved is the more useful answer (a flood
    # entry point, not a downstream victim with the same latency).
    scored.sort(key=lambda item: (item[1], item[0], item[2], item[3]), reverse=True)
    results: list[SuspectScore] = []
    for rank, (weighted, _bad, _container, _traffic, service, signals) in enumerate(
        scored[:8], start=1
    ):
        results.append(
            SuspectScore(
                service=service,
                score=round(weighted / total, 4),
                rank=rank,
                signals=signals,
            )
        )
    return results


__all__ = ["NOT_SUSPECTS", "WEIGHTS", "SuspectScore", "score_suspects", "weights"]
