"""Detection rules — pure functions so the thresholds are testable.

error_rate > max(0.02, 3 x baseline)
errors     >= 2                    (one failed order is not an incident)
p95        > max(2 x baseline, 500 ms)
container  not running / paused / unhealthy
three consecutive samples, then one incident per (service, signature)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

SEVERITY_RANK = {"sev1": 0, "sev2": 1, "sev3": 2, "sev4": 3}


@dataclass(frozen=True, slots=True)
class Thresholds:
    error_floor: float
    error_multiplier: float
    latency_multiplier: float
    latency_floor_ms: float
    min_errors: float
    consecutive: int
    recovery_samples: int
    cooldown_s: int
    interval_s: int
    window: str

    @classmethod
    def from_settings(cls) -> Thresholds:
        return cls(
            error_floor=settings.detector_error_rate_floor,
            error_multiplier=settings.detector_error_rate_multiplier,
            latency_multiplier=settings.detector_latency_multiplier,
            latency_floor_ms=settings.detector_latency_floor_ms,
            min_errors=float(settings.detector_min_errors),
            consecutive=settings.detector_consecutive_breaches,
            recovery_samples=settings.detector_recovery_samples,
            cooldown_s=settings.detector_cooldown_s,
            interval_s=settings.detector_interval_s,
            window=settings.detector_window,
        )


def error_threshold(baseline: float | None, rules: Thresholds) -> float | None:
    if baseline is None:
        return None
    return max(rules.error_floor, rules.error_multiplier * baseline)


def error_breach(
    value: float | None,
    baseline: float | None,
    errors: float | None,
    rules: Thresholds,
) -> bool:
    if value is None or errors is None:
        return False
    threshold = error_threshold(baseline, rules)
    if threshold is None:
        return False
    # An absolute floor of 2% is already the first guard; the count guard
    # suppresses a single failed request in a low-traffic service, where a
    # two-minute rate can jump to 25% from one error. A service failing *most*
    # of its requests is not noise and does not need two errors to qualify.
    if errors >= rules.min_errors:
        return value > threshold
    return value >= SEVERE_ERROR_RATE and value > threshold


SEVERE_ERROR_RATE = 0.5
CONTAINER_HEALTH_BREACHES = 6  # health probes flap; 60 s of unhealthy is real
# Latency is the noisiest signal in this demo: checkout's healthy p95 has been
# anything from 385 ms to 15 s depending on how long the stack has been under
# load. For an already-slow service, require a large absolute increase in
# addition to the 2x ratio; fast services keep the 500 ms floor.
SLOW_BASELINE_MS = 100.0
SLOW_DELTA_MS = 1500.0


def latency_threshold(baseline: float | None, rules: Thresholds) -> float | None:
    if baseline is None or baseline <= 0:
        return None
    return max(rules.latency_multiplier * baseline, rules.latency_floor_ms)


def latency_breach(value: float | None, baseline: float | None, rules: Thresholds) -> bool:
    if value is None or baseline is None:
        return False
    threshold = latency_threshold(baseline, rules)
    if threshold is None or value <= threshold:
        return False
    return not (baseline >= SLOW_BASELINE_MS and (value - baseline) < SLOW_DELTA_MS)


def container_breach(*, state: str, paused: bool, health: str) -> bool:
    if paused:
        return True
    if state in ("exited", "dead", "restarting"):
        return True
    return health == "unhealthy"


THROUGHPUT_MULTIPLIER = 2.0
# A baseline below this is a handful of requests over the window; its ratio is
# not meaningful (payment serves ~0.1 req/s).
THROUGHPUT_MIN_BASELINE = 0.25


def throughput_threshold(baseline: float | None) -> float | None:
    if baseline is None or baseline < THROUGHPUT_MIN_BASELINE:
        return None
    return THROUGHPUT_MULTIPLIER * baseline


def throughput_breach(value: float | None, baseline: float | None) -> bool:
    """A surge is a symptom, not a fault on its own.

    `traffic_flood` is only observable as load: the demo's catalogue declares
    the symptom as "throughput at least 2x its pre-injection value". A drop is
    not a breach (the fault catalog does not model starvation).
    """
    if value is None:
        return False
    threshold = throughput_threshold(baseline)
    return threshold is not None and value > threshold


def severity_for_error(value: float, baseline: float | None) -> str:
    if value >= 0.5:
        return "sev1"
    if value >= 0.2:
        return "sev2"
    if baseline is not None and value >= max(0.05, 5 * baseline):
        return "sev2"
    return "sev3"


def severity_for_latency(value: float, baseline: float) -> str:
    ratio = value / baseline if baseline > 0 else 1.0
    if ratio >= 5 or value >= 10_000:
        return "sev2"
    return "sev3"


def worst_severity(severities: list[str]) -> str:
    return min(severities, key=lambda item: SEVERITY_RANK.get(item, 9), default="sev3")


__all__ = [
    "SEVERITY_RANK",
    "THROUGHPUT_MULTIPLIER",
    "Thresholds",
    "container_breach",
    "error_breach",
    "error_threshold",
    "latency_breach",
    "latency_threshold",
    "severity_for_error",
    "severity_for_latency",
    "throughput_breach",
    "throughput_threshold",
    "worst_severity",
]
