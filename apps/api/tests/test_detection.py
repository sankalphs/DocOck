from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.detection.baselines import BaselineStore, Sample
from app.detection.detector import EXCLUDED_SERVICES, Detector
from app.detection.thresholds import (
    Thresholds,
    container_breach,
    error_breach,
    error_threshold,
    latency_breach,
    latency_threshold,
    severity_for_error,
    severity_for_latency,
    silence_breach,
    throughput_breach,
    throughput_threshold,
    window_seconds,
    worst_severity,
)

NOW = datetime.now(UTC)


def _rules(**overrides: float | int | str) -> Thresholds:
    base: dict[str, float | int | str] = {
        "error_floor": 0.02,
        "error_multiplier": 3.0,
        "latency_multiplier": 2.0,
        "latency_floor_ms": 500.0,
        "min_errors": 2.0,
        "consecutive": 3,
        "latency_breaches": 9,
        "recovery_samples": 4,
        "cooldown_s": 300,
        "interval_s": 10,
        "window": "2m",
    }
    base.update(overrides)
    return Thresholds(**base)  # type: ignore[arg-type]


def test_error_threshold_is_the_larger_of_floor_and_multiple() -> None:
    rules = _rules()
    assert error_threshold(0.0, rules) == 0.02
    assert error_threshold(0.05, rules) == pytest.approx(0.15)
    assert error_threshold(None, rules) is None


def test_error_breach_needs_both_rate_and_count() -> None:
    rules = _rules()
    # One failed order in a quiet service: 25 % rate, but a single error.
    assert error_breach(0.25, 0.0, 1, rules) is False
    assert error_breach(0.25, 0.0, 2, rules) is True
    # A service failing most of its requests is severe even with one error.
    assert error_breach(1.0, 0.0, 1, rules) is True
    assert error_breach(0.6, 0.0, 1, rules) is True
    # Below the floor even with many errors.
    assert error_breach(0.01, 0.0, 50, rules) is False
    # Above the floor but under 3x a high baseline.
    assert error_breach(0.7, 0.2, 50, rules) is True
    assert error_breach(0.5, 0.2, 50, rules) is False
    assert error_breach(None, 0.0, 5, rules) is False
    assert error_breach(0.5, None, 5, rules) is False


def test_latency_breach_uses_the_service_baseline_and_a_floor() -> None:
    rules = _rules()
    assert latency_threshold(100.0, rules) == 500.0
    assert latency_threshold(1000.0, rules) == 2000.0
    assert latency_breach(900.0, 1000.0, rules) is False
    assert latency_breach(2100.0, 1000.0, rules) is False  # slow service, delta < 1.5 s
    assert latency_breach(3000.0, 1000.0, rules) is True
    # A healthy checkout p95 of 15 s must not alert at 15 s.
    assert latency_breach(15000.0, 15000.0, rules) is False
    assert latency_breach(15000.0, None, rules) is False
    # A fast service (ad at 5 ms) crosses the 500 ms floor without needing a delta.
    assert latency_breach(600.0, 5.0, rules) is True


def test_container_breach_reads_state_paused_and_health() -> None:
    assert container_breach(state="running", paused=False, health="healthy") is False
    assert container_breach(state="exited", paused=False, health="none") is True
    assert container_breach(state="running", paused=True, health="healthy") is True
    assert container_breach(state="running", paused=False, health="unhealthy") is True


def test_throughput_breach_is_a_doubling_and_ignores_tiny_baselines() -> None:
    assert throughput_breach(60.0, 24.0) is True
    assert throughput_breach(40.0, 24.0) is False
    assert throughput_breach(60.0, None) is False
    # payment serves ~0.1 req/s; 0.2/s is not a flood.
    assert throughput_breach(0.4, 0.1) is False
    assert throughput_threshold(24.0) == 48.0


def test_severity_mapping() -> None:
    assert severity_for_error(0.9, 0.0) == "sev1"
    assert severity_for_error(0.3, 0.0) == "sev2"
    assert severity_for_error(0.06, 0.01) == "sev2"
    assert severity_for_error(0.035, 0.01) == "sev3"
    assert severity_for_latency(12000.0, 1000.0) == "sev2"
    assert severity_for_latency(1200.0, 1000.0) == "sev3"
    assert worst_severity(["sev3", "sev1", "sev2"]) == "sev1"


def test_baseline_is_unavailable_until_six_samples() -> None:
    store = BaselineStore(window_s=900)
    for index in range(5):
        store.observe(
            "payment",
            Sample(at=NOW + timedelta(seconds=index * 10), error_rate=0.0, throughput=0.1),
        )
    assert store.baseline("payment", "error_rate", now=NOW + timedelta(seconds=50)) is None
    store.observe("payment", Sample(at=NOW + timedelta(seconds=60), error_rate=0.0, throughput=0.1))
    assert store.baseline("payment", "error_rate", now=NOW + timedelta(seconds=60)) == 0.0


def test_baseline_is_a_median_that_ignores_a_short_spike() -> None:
    store = BaselineStore(window_s=900)
    values = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]
    for index, value in enumerate(values):
        store.observe(
            "payment",
            Sample(at=NOW + timedelta(seconds=index * 10), error_rate=value),
        )
    now = NOW + timedelta(seconds=100)
    assert store.baseline("payment", "error_rate", now=now) == 0.0


def test_baseline_window_prunes_old_samples() -> None:
    store = BaselineStore(window_s=60)
    for index in range(6):
        store.observe(
            "payment",
            Sample(at=NOW + timedelta(seconds=index * 10), error_rate=0.0),
        )
    later = NOW + timedelta(minutes=10)
    store.observe("payment", Sample(at=later, error_rate=0.5))
    assert store.baseline("payment", "error_rate", now=later) is None


def test_seeded_baseline_expires() -> None:
    store = BaselineStore(window_s=900)
    store.seed("payment", "error_rate", 0.01, NOW)
    assert store.baseline("payment", "error_rate", now=NOW + timedelta(minutes=5)) == 0.01
    assert store.baseline("payment", "error_rate", now=NOW + timedelta(minutes=30)) is None


def test_backfill_uses_the_low_percentile_of_the_recorded_points() -> None:
    store = BaselineStore(window_s=900)
    points = [(NOW + timedelta(minutes=i), value) for i, value in enumerate([0.2, 0.1, 0.3])]
    store.backfill("cart", "error_rate", points)
    assert store.baseline("cart", "error_rate", now=NOW + timedelta(minutes=5)) == 0.1


def test_debounce_needs_three_consecutive_samples() -> None:
    detector = Detector()
    assert detector._count("payment", "error_rate", True, NOW) == 1
    assert detector._count("payment", "error_rate", True, NOW) == 2
    assert detector._count("payment", "error_rate", False, NOW) == 0
    assert detector._count("payment", "error_rate", True, NOW) == 1
    assert detector._count("payment", "error_rate", True, NOW) == 2
    assert detector._count("payment", "error_rate", True, NOW) == 3
    assert detector._first_seen("payment", "error_rate", NOW) == NOW


def test_infrastructure_pseudo_services_are_excluded() -> None:
    assert {"flagd", "flagd-ui", "load-generator"} <= EXCLUDED_SERVICES
    assert "payment" not in EXCLUDED_SERVICES


def test_silence_needs_a_baseline_that_expects_traffic() -> None:
    window = window_seconds("2m")
    assert window == 120
    # cart: ~5 spans/s -> ~600 expected in 2 minutes, none arrived.
    assert silence_breach(0.0, 5.0, window)
    # One span in the window is not silence.
    assert not silence_breach(1 / 120, 5.0, window)
    # payment/email (~0.1-0.2/s) see healthy 90 s gaps between order bursts.
    assert not silence_breach(0.0, 0.18, window)
    # No reading or no baseline: nothing to compare.
    assert not silence_breach(None, 5.0, window)
    assert not silence_breach(0.0, None, window)


def test_a_running_container_with_no_network_is_a_breach() -> None:
    assert container_breach(state="running", paused=False, health="healthy", networks=[])
    assert not container_breach(
        state="running", paused=False, health="healthy", networks=["docock-demo_default"]
    )
    # Not inspected is not the same as detached.
    assert not container_breach(state="running", paused=False, health="healthy", networks=None)
