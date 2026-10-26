"""Metric anomaly scan.

For every service and for the incident's container counterparts, compare the
window against the pre-incident part of the same query. The comparison is
per-service and per-metric: this demo's healthy checkout p95 is 15 s, so any
absolute rule is nonsense.

The scan uses `query_range`, so one Prometheus call per metric covers every
service and the pre-window in the same response.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.telemetry.dto import ContainerInfo
from app.telemetry.prometheus import PrometheusAdapter

METRICS: tuple[tuple[str, str, str], ...] = (
    # metric id, unit, direction
    ("error_rate", "ratio", "up"),
    ("p95_latency", "ms", "up"),
    ("throughput", "rps", "change"),
    ("container_cpu", "%", "up"),
    ("container_memory", "MB", "up"),
)

MIN_PRE_POINTS = 3


class MetricAnomaly(BaseModel):
    metric: str
    service: str
    unit: str
    current: float
    baseline: float
    delta: float
    ratio: float | None
    z_score: float | None
    direction: str
    detail: str
    points: int = 0


class MetricsScan(BaseModel):
    anomalies: list[MetricAnomaly] = Field(default_factory=list)
    baselines: dict[str, dict[str, float]] = Field(default_factory=dict)
    current: dict[str, dict[str, float]] = Field(default_factory=dict)


def _split(
    points: list[tuple[datetime, float]], floor: datetime
) -> tuple[list[float], list[float]]:
    pre: list[float] = []
    window: list[float] = []
    for at, value in points:
        if at < floor:
            pre.append(value)
        else:
            window.append(value)
    return pre, window


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _z(current: float, pre: list[float]) -> float | None:
    if len(pre) < MIN_PRE_POINTS:
        return None
    mean = statistics.fmean(pre)
    std = statistics.pstdev(pre)
    if std < 1e-9:
        return None if abs(current - mean) < 1e-9 else 6.0 if current > mean else -6.0
    return (current - mean) / std


def _anomaly(
    metric: str,
    unit: str,
    direction: str,
    service: str,
    pre: list[float],
    window: list[float],
) -> MetricAnomaly | None:
    if not pre or not window:
        return None
    baseline = _median(pre)
    if metric == "error_rate":
        current = max(window)
    elif metric == "throughput":
        current = _median(window)
    else:
        current = max(window)
    delta = current - baseline
    ratio = (current / baseline) if baseline > 0 else None
    z = _z(current, pre)

    anomalous = False
    if metric == "error_rate":
        anomalous = delta >= 0.02 and (z is None or z >= 2.0)
        detail = f"error rate {current:.1%} vs {baseline:.1%} before onset"
    elif metric == "throughput":
        if baseline <= 0.01:
            return None
        ratio_value = current / baseline
        anomalous = ratio_value >= 1.5 or ratio_value <= 0.5
        detail = f"throughput {current:.2f}/s vs {baseline:.2f}/s ({ratio_value:.2f}x)"
    elif metric in ("p95_latency", "container_cpu", "container_memory"):
        if baseline <= 0:
            return None
        ratio_value = current / baseline
        if unit == "MB":
            anomalous = ratio_value >= 1.3 and delta >= 20
        else:
            anomalous = ratio_value >= 1.5 and delta >= 50
        detail = (
            f"{metric.replace('_', ' ')} {current:.1f} {unit} vs {baseline:.1f} {unit} before onset"
        )
    else:  # pragma: no cover
        return None

    if not anomalous:
        return None
    return MetricAnomaly(
        metric=metric,
        service=service,
        unit=unit,
        current=round(current, 4),
        baseline=round(baseline, 4),
        delta=round(delta, 4),
        ratio=round(ratio, 3) if ratio is not None else None,
        z_score=round(z, 2) if z is not None else None,
        direction=direction,
        detail=detail,
        points=len(window),
    )


async def scan_metrics(
    prometheus: PrometheusAdapter,
    *,
    opened_at: datetime,
    end: datetime,
    pre_s: int = 300,
    containers: list[ContainerInfo] | None = None,
) -> MetricsScan:
    start = opened_at - timedelta(seconds=pre_s)
    # The detector opens an incident after three breaches, so by then the fault
    # is already visible. Start the "current" half 45 s before open so those
    # breach samples are included instead of waiting for a point after `open`.
    floor = opened_at - timedelta(seconds=45)
    span = max(60, int((end - start).total_seconds()))
    step = 15 if span <= 900 else 30
    by_container = {item.name: item.service for item in (containers or []) if item.name}
    scan = MetricsScan()
    for metric, unit, direction in METRICS:
        try:
            result = await prometheus.metric(metric, window="2m", start=start, end=end, step_s=step)
        except Exception:
            continue
        for series in result.series:
            service: str | None
            if metric.startswith("container_"):
                container_name = series.labels.get("container_name")
                service = by_container.get(container_name or "", container_name or "")
            else:
                service = series.labels.get("service_name")
            if not service:
                continue
            pre, window = _split([(point.t, point.v) for point in series.points], floor)
            if not pre or not window:
                continue
            baseline = _median(pre)
            scan.baselines.setdefault(service, {})[metric] = round(baseline, 4)
            current = max(window) if metric != "throughput" else _median(window)
            scan.current.setdefault(service, {})[metric] = round(current, 4)
            anomaly = _anomaly(metric, unit, direction, service, pre, window)
            if anomaly is not None:
                scan.anomalies.append(anomaly)
    scan.anomalies.sort(key=lambda item: (item.metric, item.service))
    return scan


__all__ = ["METRICS", "MetricAnomaly", "MetricsScan", "scan_metrics"]
