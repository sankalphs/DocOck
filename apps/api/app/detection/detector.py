"""Deterministic incident detector.

One cycle:

1. read error rate, error count, p95, and throughput for every service from
   Prometheus (one 2-minute rate window),
2. read container state from Docker,
3. update the rolling baselines,
4. debounce three consecutive breaches per (service, signal),
5. correlate: one incident per fault — the earliest onset is the primary
   service and every other breaching service is attached as a signal event,
6. close an incident that is still `OPEN` once the symptom has been gone for
   four samples.

No LLM, no absolute latency threshold, no second incident for the same fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db.models import Fault
from app.db.session import SessionFactory
from app.detection.baselines import (
    MAX_SEED_ERROR_RATE,
    MAX_SEED_P95_MS,
    BaselineStore,
    Sample,
)
from app.detection.thresholds import (
    CONTAINER_HEALTH_BREACHES,
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
from app.domain.state import IncidentStatus, is_active
from app.faults.catalog import ALLOWED_TARGETS
from app.incidents.service import IncidentService
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.telemetry.docker import DockerAdapter, host_cores
from app.telemetry.prometheus import PrometheusAdapter

log = get_logger(__name__)

EXCLUDED_SERVICES = frozenset(
    {"flagd", "flagd-ui", "load-generator", "telemetry-docs", "opamp-server"}
)

# Which breach best explains the others. A resource change or a container death
# is a *cause*; a throughput surge is the next most causal (the load is why
# everything else got slow); an error rate outranks a latency.
SIGNAL_PRIORITY = {
    "container": 3,
    "container_resource": 3,
    "throughput": 2,
    "silence": 1,
    "error_rate": 1,
    "p95_latency": 0,
}


@dataclass(slots=True)
class Breach:
    service: str
    signal: str
    signature: str
    value: float | None
    baseline: float | None
    threshold: float | None
    first_seen: datetime
    consecutive: int
    detail: str
    severity: str
    throughput: float | None = None

    @property
    def deviation(self) -> float:
        """How far the reading is from the service's own norm.

        Used only to pick the incident's primary service when several services
        breach in the same cycle: the propagated caller inherits the callee's
        error rate but usually has the larger baseline (it was already noisy),
        so the origin wins. P9 computes the real suspect ranking.
        """
        if self.signal == "container":
            return float("inf")
        if self.value is None:
            return 0.0
        denominator = max(self.baseline or 0.0, self.threshold or 0.0, 1e-6)
        return self.value / denominator


@dataclass(slots=True)
class DetectionReport:
    at: datetime
    services: int = 0
    baselines: dict[str, dict[str, float | None]] = field(default_factory=dict)
    breaches: list[Breach] = field(default_factory=list)
    opened: str | None = None
    attached: int = 0
    closed: str | None = None
    skipped: str | None = None
    persisted: int = 0


class Detector:
    def __init__(
        self,
        *,
        prometheus: PrometheusAdapter | None = None,
        docker: DockerAdapter | None = None,
        store: BaselineStore | None = None,
        incidents: IncidentService | None = None,
        rules: Thresholds | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusAdapter(ttl_s=0)
        self.docker = docker or DockerAdapter(ttl_s=0)
        self.store = store or BaselineStore()
        self.incidents = incidents or incident_service
        self.rules = rules or Thresholds.from_settings()
        self._counters: dict[tuple[str, str], tuple[int, datetime]] = {}
        # Container resource limits are *state*, not a metric: record the limit
        # observed at startup and treat a reduction as a breach. This is the
        # only observable symptom of a CPU/memory pressure fault on a service
        # that is too cheap to slow down (ad at 0.25 cores uses 1.3 % CPU).
        self._limits: dict[str, tuple[float | None, int | None]] = {}
        self._healthy_cycles = 0
        self._cycles = 0
        self.last_report: DetectionReport | None = None
        self.last_error: str | None = None

    async def backfill(self) -> int:
        """Seed baselines from the last window of Prometheus data.

        This removes the need for a five-minute warm-up timer after a restart:
        if the service has history, its own median is available immediately.
        """
        now = datetime.now(UTC)
        start = now - timedelta(seconds=self.store.window_s)
        seeded = 0
        # Throughput is the one metric whose "normal" level changes with the
        # deployment itself: after a restart the generator ramps from 1 req/s to
        # 25 req/s, and a backfilled baseline from before the restart would call
        # that ramp a flood. It is learned from live samples only (one minute).
        for metric in ("error_rate", "p95_latency"):
            result = await self.prometheus.metric(
                metric, window=self.rules.window, start=start, end=now, step_s=60
            )
            for series in result.series:
                service = series.labels.get("service_name")
                if not service or service in EXCLUDED_SERVICES:
                    continue
                self.store.backfill(
                    service, metric, [(point.t, point.v) for point in series.points]
                )
                seeded += 1
        log.info("baselines_backfilled", series=seeded)
        return seeded

    async def run_once(self, *, persist: bool = False) -> DetectionReport:
        now = datetime.now(UTC)
        report = DetectionReport(at=now)
        # PLAN §8 calls the first minutes after bring-up "warm-up". Breach
        # counters are cleared each cycle so that the moment the gate lifts the
        # detector needs a fresh consecutive run instead of opening an incident
        # on a spike that was already counted while the stack was still ramping.
        warm = self._cycles * self.rules.interval_s < settings.detector_warmup_s
        if warm:
            self._counters.clear()

        error_rate = await self._instant("error_rate")
        error_count = await self._instant("error_count")
        p95 = await self._instant("p95_latency")
        throughput = await self._instant("throughput")
        containers = {
            info.service: info
            for info in await self.docker.containers(include_stats=False)
            if info.service in ALLOWED_TARGETS
        }

        services = (
            set(error_rate) | set(p95) | set(throughput) | set(containers)
        ) - EXCLUDED_SERVICES
        report.services = len(services)

        active: list[Breach] = []
        host = 0
        try:
            host = await host_cores()
        except Exception:  # Docker hiccup: limits are simply not compared
            host = 0
        # While an incident is open for a service, that service's readings are
        # symptoms of a known problem, not health. Feeding them back into its
        # baseline is how a 15-minute p20 learned that a 3-minute 100 % error
        # window was normal and stopped detecting the next fault.
        window_s = window_seconds(self.rules.window)
        suppressed: set[str] = set()
        try:
            async with SessionFactory() as session:
                current = await self.incidents.latest_open(session)
                if current is not None:
                    suppressed = {
                        str(item) for item in ((current.trigger or {}).get("services") or [])
                    }
        except Exception:
            suppressed = set()
        for service in sorted(services):
            rate = error_rate.get(service)
            errors = error_count.get(service)
            latency = p95.get(service)
            rps = throughput.get(service)
            baseline_error = self.store.baseline(service, "error_rate", now=now)
            baseline_latency = self.store.baseline(service, "p95_latency", now=now)

            error_hit = error_breach(rate, baseline_error, errors, self.rules)
            latency_hit = latency_breach(latency, baseline_latency, self.rules)
            info = containers.get(service)
            container_hit = bool(
                info
                and container_breach(
                    state=info.state,
                    paused=info.paused,
                    health=info.health,
                    networks=list(info.networks),
                )
            )
            resource_hit, resource_detail = self._resource_change(service, info, host)

            baseline_rps = self.store.baseline(service, "throughput", now=now)
            throughput_hit = throughput_breach(rps, baseline_rps)
            silent_hit = silence_breach(rps, baseline_rps, window_s)
            # A sample taken while the service is breaching must not feed its
            # own baseline, or a long fault teaches the detector that the fault
            # is normal. Bootstrap (no baseline yet) still observes so a
            # baseline can form on a healthy service — but only when the
            # reading itself is plausible: a fresh process that starts while a
            # fault is still draining must not learn that 100 % errors are the
            # new normal.
            plausible = (rate is None or rate <= MAX_SEED_ERROR_RATE) and (
                latency is None or latency <= MAX_SEED_P95_MS
            )
            bootstrap = baseline_error is None and baseline_latency is None
            if (
                service not in suppressed
                and plausible
                and (
                    bootstrap
                    or not (
                        error_hit
                        or latency_hit
                        or container_hit
                        or resource_hit
                        or throughput_hit
                        or silent_hit
                    )
                )
            ):
                self.store.observe(
                    service,
                    Sample(
                        at=now,
                        error_rate=rate,
                        errors=errors,
                        p95_ms=latency,
                        throughput=rps,
                    ),
                )

            count = self._count(service, "error_rate", error_hit, now)
            if error_hit and count >= self.rules.consecutive and rate is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="error_rate",
                        signature=f"error_rate:{service}",
                        value=rate,
                        baseline=baseline_error,
                        threshold=error_threshold(baseline_error, self.rules),
                        first_seen=self._first_seen(service, "error_rate", now),
                        consecutive=count,
                        detail=(
                            f"inbound error rate {rate:.1%} vs baseline "
                            f"{(baseline_error or 0):.1%} ({errors or 0:.0f} errors)"
                        ),
                        severity=severity_for_error(rate, baseline_error),
                        throughput=rps,
                    )
                )

            count = self._count(service, "p95_latency", latency_hit, now)
            if latency_hit and count >= self.rules.latency_breaches and latency is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="p95_latency",
                        signature=f"p95_latency:{service}",
                        value=latency,
                        baseline=baseline_latency,
                        threshold=latency_threshold(baseline_latency, self.rules),
                        first_seen=self._first_seen(service, "p95_latency", now),
                        consecutive=count,
                        detail=(
                            f"p95 {latency:.0f} ms vs baseline {(baseline_latency or 0):.0f} ms"
                        ),
                        severity=severity_for_latency(latency, baseline_latency or 1.0),
                        throughput=rps,
                    )
                )

            info = containers.get(service)
            count = self._count(service, "container", container_hit, now)
            health_only = bool(
                info and info.health == "unhealthy" and info.state == "running" and not info.paused
            )
            required = CONTAINER_HEALTH_BREACHES if health_only else self.rules.consecutive
            if container_hit and count >= required and info is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="container",
                        signature=f"container:{service}",
                        value=None,
                        baseline=None,
                        threshold=None,
                        first_seen=self._first_seen(service, "container", now),
                        consecutive=count,
                        detail=(
                            f"container {info.name} is {info.status_text}"
                            + (" (paused)" if info.paused else "")
                            + (
                                " (no network attached)"
                                if info.state == "running" and not info.networks
                                else ""
                            )
                        ),
                        severity="sev2",
                        throughput=rps,
                    )
                )

            count = self._count(service, "container_resource", resource_hit, now)
            if resource_hit and count >= self.rules.consecutive and info is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="container_resource",
                        signature=f"container_resource:{service}",
                        value=info.cpu_limit_cores,
                        baseline=self._limits.get(service, (None, None))[0],
                        threshold=None,
                        first_seen=self._first_seen(service, "container_resource", now),
                        consecutive=count,
                        detail=resource_detail,
                        severity="sev2",
                        throughput=rps,
                    )
                )

            count = self._count(service, "throughput", throughput_hit, now)
            if throughput_hit and count >= self.rules.consecutive and rps is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="throughput",
                        signature=f"throughput:{service}",
                        value=rps,
                        baseline=baseline_rps,
                        threshold=throughput_threshold(baseline_rps),
                        first_seen=self._first_seen(service, "throughput", now),
                        consecutive=count,
                        detail=(
                            f"throughput {rps:.2f}/s vs baseline "
                            f"{(baseline_rps or 0):.2f}/s "
                            f"({(rps / baseline_rps) if baseline_rps else 1:.1f}x)"
                        ),
                        severity="sev3",
                        throughput=rps,
                    )
                )

            count = self._count(service, "silence", silent_hit, now)
            if silent_hit and count >= self.rules.consecutive and rps is not None:
                active.append(
                    Breach(
                        service=service,
                        signal="silence",
                        signature=f"silence:{service}",
                        value=rps,
                        baseline=baseline_rps,
                        threshold=0.0,
                        first_seen=self._first_seen(service, "silence", now),
                        consecutive=count,
                        detail=(
                            f"no requests in {self.rules.window} vs baseline "
                            f"{(baseline_rps or 0):.2f}/s "
                            f"(~{(baseline_rps or 0) * window_s:.0f} expected)"
                        ),
                        severity="sev2",
                        throughput=rps,
                    )
                )

        report.breaches = active
        report.baselines = self.store.snapshot(now=now)

        async with SessionFactory() as session:
            open_incident = await self.incidents.latest_open(session)
            if open_incident is not None and is_active(IncidentStatus(open_incident.status)):
                attached = await self._attach(session, open_incident, active)
                report.attached = attached
                if active:
                    self._healthy_cycles = 0
                else:
                    self._healthy_cycles += 1
                if not active and self._healthy_cycles >= self.rules.recovery_samples:
                    reason = "symptom cleared for " + (
                        f"{self._healthy_cycles * self.rules.interval_s}s"
                    )
                    recovered = await self.incidents.recover(
                        session,
                        open_incident,
                        reason="symptom_cleared",
                        detail=reason,
                    )
                    if recovered is not None:
                        report.closed = recovered.public_id
                        self._healthy_cycles = 0
            else:
                self._healthy_cycles = 0 if active else self._healthy_cycles + 1
                # PLAN §8 calls the first minutes after bring-up "warm-up": the
                # generator ramps from ~1 req/s to 25 req/s and checkout's p95
                # reaches its 15 s top bucket while pools and JIT fill. Every one
                # of those breaches a baseline that does not exist yet, so no
                # incident may open until the detector has watched for
                # DETECTOR_WARMUP_S. Samples are still recorded, so the baselines
                # are ready the moment the gate lifts; an incident that was
                # already open when the process restarted still attaches and
                # recovers normally.
                if active and warm:
                    report.skipped = f"warmup ({settings.detector_warmup_s}s)"
                    active = []
                if active:
                    # A deliberate new injection supersedes the alert cooldown:
                    # without this, re-injecting the same fault within five
                    # minutes of closing its incident is silently suppressed.
                    active_faults = (
                        (
                            await session.execute(
                                select(Fault).where(Fault.status.in_(["PROPAGATING", "ACTIVE"]))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    injection_active = bool(active_faults)
                    eligible = []
                    for breach in active:
                        if injection_active:
                            eligible.append(breach)
                            continue
                        recent = await self.incidents.recently_closed(
                            session,
                            signature=breach.signature,
                            within_s=self.rules.cooldown_s,
                        )
                        if recent is None:
                            eligible.append(breach)
                    if not eligible:
                        report.skipped = "all_breaches_in_cooldown"
                    else:
                        primary = min(
                            eligible,
                            key=lambda item: (
                                -SIGNAL_PRIORITY.get(item.signal, 0),
                                item.first_seen,
                                -item.deviation,
                                item.throughput if item.throughput is not None else 1e9,
                                item.service,
                            ),
                        )
                        incident = await self._open(session, primary, eligible)
                        report.opened = incident.public_id
                        report.attached = max(0, len(eligible) - 1)
            if persist:
                report.persisted = await self.store.persist(
                    session, now=now, skip={item.service for item in active}
                )
            await session.commit()

        self._cycles += 1
        self.last_report = report
        self.last_error = None
        if report.opened or report.closed:
            log.info(
                "detection_cycle",
                opened=report.opened,
                closed=report.closed,
                breaches=len(report.breaches),
            )
        return report

    async def _open(self, session: Any, primary: Breach, eligible: list[Breach]) -> Any:
        detector_context = {
            "window": self.rules.window,
            "interval_s": self.rules.interval_s,
            "consecutive": self.rules.consecutive,
            "min_errors": self.rules.min_errors,
        }
        signal = {
            "service": primary.service,
            "signal": primary.signal,
            "signature": primary.signature,
            "value": primary.value,
            "baseline": primary.baseline,
            "threshold": primary.threshold,
            "detail": primary.detail,
        }
        trigger: dict[str, Any] = {
            "services": [primary.service],
            "signals": [signal],
            "detector": detector_context,
            "primary_signal": primary.signal,
        }
        title = f"{primary.service}: {primary.signal.replace('_', ' ')} — {primary.detail}"
        incident = await self.incidents.create(
            session,
            service=primary.service,
            signature=primary.signature,
            severity=worst_severity([breach.severity for breach in eligible]),
            title=title,
            trigger=trigger,
        )
        for breach in eligible:
            if breach.service == primary.service and breach.signal == primary.signal:
                continue
            await self.incidents.attach_signal(
                session,
                incident,
                service=breach.service,
                signal=breach.signal,
                value=breach.value,
                baseline=breach.baseline,
                threshold=breach.threshold,
                detail=breach.detail,
            )
        return incident

    async def _attach(self, session: Any, incident: Any, active: list[Breach]) -> int:
        trigger = dict(incident.trigger or {})
        known = {str(item) for item in (trigger.get("services") or [])}
        signals = {
            str(item.get("signature"))
            for item in (trigger.get("signals") or [])
            if isinstance(item, dict)
        }
        attached = 0
        for breach in active:
            if breach.service in known and breach.signature in signals:
                continue
            await self.incidents.attach_signal(
                session,
                incident,
                service=breach.service,
                signal=breach.signal,
                value=breach.value,
                baseline=breach.baseline,
                threshold=breach.threshold,
                detail=breach.detail,
            )
            attached += 1
        return attached

    def _resource_change(self, service: str, info: Any, host_cores_value: int) -> tuple[bool, str]:
        """True when a container's CPU or memory limit is below its startup value.

        The first observation is the baseline. A cap equal to the host core
        count is the demo's "unlimited" (Docker cannot clear a CPU limit), so it
        normalizes to `None`. A reduction is the only observable symptom of a
        resource-pressure fault on a service that is too cheap to slow down.
        """
        if info is None:
            return False, ""
        current = (
            None
            if info.cpu_limit_cores is None
            or (host_cores_value and info.cpu_limit_cores >= host_cores_value)
            else float(info.cpu_limit_cores),
            int(info.memory_limit_mb) if info.memory_limit_mb is not None else None,
        )
        baseline = self._limits.get(service)
        if baseline is None:
            self._limits[service] = current
            return False, ""
        details: list[str] = []
        base_cpu, base_mem = baseline
        if current[0] is not None and (base_cpu is None or current[0] < base_cpu):
            details.append(
                f"CPU limit {current[0]:g} cores"
                + (" (was unlimited)" if base_cpu is None else f" (was {base_cpu:g})")
            )
        if current[1] is not None and (base_mem is None or current[1] < base_mem):
            details.append(
                f"memory limit {current[1]} MB"
                + (" (was unlimited)" if base_mem is None else f" (was {base_mem})")
            )
        return (bool(details), "; ".join(details))

    async def _instant(self, metric: str) -> dict[str, float]:
        result = await self.prometheus.metric(metric, window=self.rules.window)
        values: dict[str, float] = {}
        for series in result.series:
            service = series.labels.get("service_name")
            if not service:
                continue
            point = series.points[-1] if series.points else None
            if point is None:
                continue
            values[service] = max(values.get(service, point.v), point.v)
        return values

    def _count(self, service: str, signal: str, breached: bool, at: datetime) -> int:
        key = (service, signal)
        if not breached:
            self._counters.pop(key, None)
            return 0
        count, first = self._counters.get(key, (0, at))
        count += 1
        self._counters[key] = (count, first)
        return count

    def _first_seen(self, service: str, signal: str, fallback: datetime) -> datetime:
        return self._counters.get((service, signal), (0, fallback))[1]

    def status(self) -> dict[str, Any]:
        return {
            "enabled": settings.detector_enabled,
            "interval_s": self.rules.interval_s,
            "window": self.rules.window,
            "consecutive": self.rules.consecutive,
            "recovery_samples": self.rules.recovery_samples,
            "cycles": self._cycles,
            "last_error": self.last_error,
            "last_cycle_at": self.last_report.at if self.last_report else None,
            "last_opened": self.last_report.opened if self.last_report else None,
            "last_closed": self.last_report.closed if self.last_report else None,
            "breaches": [
                {
                    "service": item.service,
                    "signal": item.signal,
                    "value": item.value,
                    "baseline": item.baseline,
                    "consecutive": item.consecutive,
                    "severity": item.severity,
                }
                for item in (self.last_report.breaches if self.last_report else [])
            ],
            "baselines": self.store.snapshot(),
            "cooldown_s": self.rules.cooldown_s,
            "min_errors": self.rules.min_errors,
            "error_floor": self.rules.error_floor,
            "excluded_services": sorted(EXCLUDED_SERVICES),
        }


__all__ = ["EXCLUDED_SERVICES", "Breach", "DetectionReport", "Detector"]
