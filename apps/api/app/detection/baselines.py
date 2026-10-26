"""Per-service rolling baselines.

PLAN §10 calls for a 15-minute rolling median per service. The median is what
makes the demo usable: the checkout path is genuinely slow when healthy (p95 at
the 15 s top bucket), so an absolute threshold would either never fire or fire
forever. Only a service's own recent behaviour is a meaningful comparison.

A baseline is *unavailable* until six live samples exist (one minute at the 10 s
cadence) or a fresh persisted row / Prometheus backfill exists. No baseline means
no alert for that service — that is the warm-up, expressed as data availability
instead of a timer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Baseline
from app.logging import get_logger

log = get_logger(__name__)

METRICS = ("error_rate", "p95_latency", "throughput")
MIN_SAMPLES = 6
SEED_FRESHNESS = timedelta(minutes=10)
# A "healthy" baseline above these values is not plausible for the demo; it
# means the recorded window was faulted, so it is ignored instead of teaching
# the detector that 80 % errors are normal.
MAX_SEED_ERROR_RATE = 0.25
MAX_SEED_P95_MS = 60_000.0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _baseline_value(values: list[float], metric: str) -> float | None:
    """A high percentile for latency, a low one for errors, median for throughput.

    Errors only deviate upward when something is wrong, so the 20th percentile
    is the healthy level even when a fault occupies most of the window.
    Latency is noisy even when healthy (shipping's p95 ranges 48-1925 ms at the
    demo's normal load), so its baseline is the 80th percentile of the normal
    range and an alert needs to exceed *twice* that. Throughput is two-sided,
    so its median is the neutral statistic.
    """
    if metric == "throughput":
        return _percentile(values, 0.5)
    if metric == "p95_latency":
        return _percentile(values, 0.8)
    return _percentile(values, 0.2)


@dataclass(frozen=True, slots=True)
class Sample:
    at: datetime
    error_rate: float | None = None
    errors: float | None = None
    p95_ms: float | None = None
    throughput: float | None = None


class BaselineStore:
    def __init__(self, window_s: int | None = None) -> None:
        self.window_s = window_s or settings.detector_baseline_window_s
        self._samples: dict[str, deque[Sample]] = {}
        self._seed: dict[tuple[str, str], tuple[float, datetime]] = {}

    def observe(self, service: str, sample: Sample) -> None:
        queue = self._samples.setdefault(service, deque(maxlen=600))
        queue.append(sample)
        cutoff = sample.at - timedelta(seconds=self.window_s)
        while queue and queue[0].at < cutoff:
            queue.popleft()

    def backfill(self, service: str, metric: str, points: list[tuple[datetime, float]]) -> None:
        values = [value for _, value in points if value is not None]
        value = _baseline_value(values, metric)
        if value is None:
            return
        if metric == "error_rate" and value > MAX_SEED_ERROR_RATE:
            return
        if metric == "p95_latency" and value > MAX_SEED_P95_MS:
            return
        self._seed[(service, metric)] = (value, datetime.now(UTC))

    def seed(self, service: str, metric: str, value: float, at: datetime) -> None:
        self._seed[(service, metric)] = (value, at)

    def baseline(self, service: str, metric: str, *, now: datetime | None = None) -> float | None:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.window_s)
        queue = self._samples.get(service)
        live: list[float] = []
        if queue:
            for sample in queue:
                if sample.at < cutoff:
                    continue
                value = {
                    "error_rate": sample.error_rate,
                    "p95_latency": sample.p95_ms,
                    "throughput": sample.throughput,
                }.get(metric)
                if value is not None:
                    live.append(value)
        if len(live) >= MIN_SAMPLES:
            return _baseline_value(live, metric)
        seeded = self._seed.get((service, metric))
        if seeded and now - seeded[1] <= SEED_FRESHNESS:
            return seeded[0]
        return None

    def latest(self, service: str) -> Sample | None:
        queue = self._samples.get(service)
        return queue[-1] if queue else None

    def services(self) -> list[str]:
        return sorted(self._samples)

    def snapshot(self, now: datetime | None = None) -> dict[str, dict[str, float | None]]:
        now = now or datetime.now(UTC)
        return {
            service: {metric: self.baseline(service, metric, now=now) for metric in METRICS}
            for service in self.services()
        }

    async def load(self, session: AsyncSession) -> int:
        cutoff = datetime.now(UTC) - SEED_FRESHNESS
        rows = (
            (
                await session.execute(
                    select(Baseline).where(
                        Baseline.window_s == self.window_s,
                        Baseline.computed_at >= cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            computed = row.computed_at
            if computed.tzinfo is None:
                computed = computed.replace(tzinfo=UTC)
            if row.metric == "error_rate" and row.value > MAX_SEED_ERROR_RATE:
                continue
            if row.metric == "p95_latency" and row.value > MAX_SEED_P95_MS:
                continue
            self._seed[(row.service, row.metric)] = (row.value, computed)
        if rows:
            log.info("baselines_loaded", rows=len(rows))
        return len(rows)

    async def persist(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        skip: set[str] | None = None,
    ) -> int:
        now = now or datetime.now(UTC)
        written = 0
        # A baseline computed while the service is breaching is not a healthy
        # baseline. Persisting it would survive a restart and suppress the very
        # alert it came from, so breaching services are skipped for this cycle.
        blocked = skip or set()
        for service, metrics in self.snapshot(now=now).items():
            if service in blocked:
                continue
            for metric, value in metrics.items():
                if value is None:
                    continue
                statement = (
                    insert(Baseline)
                    .values(
                        service=service,
                        metric=metric,
                        value=float(value),
                        window_s=self.window_s,
                        computed_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=["service", "metric", "window_s"],
                        set_={"value": float(value), "computed_at": now},
                    )
                )
                await session.execute(statement)
                written += 1
        return written


__all__ = ["METRICS", "MIN_SAMPLES", "BaselineStore", "Sample"]
