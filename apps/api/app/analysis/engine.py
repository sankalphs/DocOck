"""The deterministic analysis engine.

Runs the six analyses from PLAN §10 in a fixed order, stores every observable as
incident-scoped evidence, writes the suspect ranking, and appends an `analysis`
event to the incident timeline. Re-running is safe: evidence is deduped by hash
and keeps its original `E####` code, and the suspect snapshot is replaced.

It never calls an LLM and never mutates infrastructure.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.logs import LogAnalysis, analyse_logs
from app.analysis.metrics import MetricAnomaly, scan_metrics
from app.analysis.signals import (
    ContainerSignal,
    FaultSignal,
    container_signals,
    fault_signals,
)
from app.analysis.suspects import SuspectScore, score_suspects, weights
from app.analysis.traces import TraceAnalysis, analyse_traces
from app.config import settings
from app.db.models import Fault, Suspect
from app.db.session import SessionFactory
from app.evidence.store import EvidenceStore
from app.evidence.store import store as evidence_store
from app.faults.catalog import ALLOWED_TARGETS
from app.incidents.service import IncidentService
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.telemetry.docker import DockerAdapter
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.logs import LogsAdapter
from app.telemetry.prometheus import PrometheusAdapter

log = get_logger(__name__)

MAX_WINDOW_S = 900


class AnalysisReport(BaseModel):
    incident_id: str
    window_start: datetime
    window_end: datetime
    pre_s: int
    duration_ms: int
    services_considered: int
    evidence_added: int
    evidence_total: int
    codes: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    suspects: list[SuspectScore] = Field(default_factory=list)
    top_suspect: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class Analyzer:
    def __init__(
        self,
        *,
        prometheus: PrometheusAdapter | None = None,
        jaeger: JaegerAdapter | None = None,
        logs: LogsAdapter | None = None,
        docker: DockerAdapter | None = None,
        evidence: EvidenceStore | None = None,
        incidents: IncidentService | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusAdapter(ttl_s=0)
        self.jaeger = jaeger or JaegerAdapter(ttl_s=0)
        self.logs = logs or LogsAdapter(ttl_s=0)
        self.docker = docker or DockerAdapter(ttl_s=0)
        self.evidence = evidence or evidence_store
        self.incidents = incidents or incident_service
        # Two concurrent writers on one incident would both compute the same next
        # E#### code and the second would fail on the unique constraint. The
        # store's lock is shared with the agent's tools; a run takes seconds and
        # the API is one process, so one lock is enough.
        self._lock = self.evidence.lock

    async def analyze(self, ref: str) -> AnalysisReport:
        async with self._lock:
            return await self._analyze(ref)

    async def _analyze(self, ref: str) -> AnalysisReport:
        started = time.perf_counter()
        async with SessionFactory() as session:
            incident = await self.incidents.get(session, ref)
            opened = _utc(incident.opened_at)
            end = min(
                _utc(incident.closed_at) if incident.closed_at else datetime.now(UTC),
                opened + timedelta(seconds=MAX_WINDOW_S),
            )
            pre_s = settings.analysis_pre_s

            faults = (
                (
                    await session.execute(
                        select(Fault).where(Fault.injected_at <= end).order_by(Fault.id)
                    )
                )
                .scalars()
                .all()
            )
            overlapping = [
                fault
                for fault in faults
                if (fault.reset_at is None or _utc(fault.reset_at) >= opened)
                and fault.status != "FAILED"
            ]

            services = sorted(
                {str(item) for item in ((incident.trigger or {}).get("services") or []) if item}
                | ({incident.primary_service} if incident.primary_service else set())
            )
            primary_signal = str((incident.trigger or {}).get("primary_signal") or "")
            load_incident = primary_signal == "throughput"
            latency_incident = primary_signal == "p95_latency"

            all_containers = await self.docker.containers(include_stats=True)
            allowed_containers = [
                item for item in all_containers if item.service in ALLOWED_TARGETS
            ]
            metric_scan = await scan_metrics(
                self.prometheus,
                opened_at=opened,
                end=end,
                pre_s=pre_s,
                containers=allowed_containers,
            )
            baseline_p95 = {
                service: values.get("p95_latency", 0.0)
                for service, values in metric_scan.baselines.items()
            }
            known = sorted(
                set(metric_scan.baselines)
                | set(metric_scan.current)
                | {item.service for item in allowed_containers}
                | set(services)
            )
            trace_analysis = await analyse_traces(
                self.jaeger,
                opened_at=opened,
                end=end,
                known_services=known,
                baseline_p95=baseline_p95,
                focus_services=services,
                catalog_services=set(ALLOWED_TARGETS),
                latency_incident=latency_incident,
                max_traces=settings.analysis_max_traces,
            )
            log_services = sorted(
                set(services)
                | {item.service for item in metric_scan.anomalies}
                | {service for service in trace_analysis.origin}
            )[:6]
            log_analysis = await analyse_logs(
                self.logs,
                opened_at=opened,
                end=end,
                services=log_services,
                per_service=settings.analysis_max_logs_per_service,
            )
            containers = await container_signals(
                self.docker, sorted(ALLOWED_TARGETS), allowed_containers
            )
            fault_sigs = fault_signals(overlapping)

            suspects = score_suspects(
                opened_at=opened,
                origins=trace_analysis.origin,
                onset=trace_analysis.onset,
                anomalies=metric_scan.anomalies,
                containers=containers,
                faults=fault_sigs,
                incident_services=services,
                primary_service=incident.primary_service,
                load_incident=load_incident,
                latency_incident=latency_incident,
            )

            added, codes, counts = await self._persist_evidence(
                session,
                incident,
                opened=opened,
                end=end,
                metrics=metric_scan.anomalies,
                traces=trace_analysis,
                logs=log_analysis,
                containers=containers,
                faults=fault_sigs,
                suspects=suspects,
                load_incident=load_incident,
                latency_incident=latency_incident,
            )
            await self._persist_suspects(session, incident, suspects)
            total = len(await self.evidence.list(session, incident))

            await self.incidents.append_event(
                session,
                incident,
                "analysis",
                {
                    "evidence_added": added,
                    "evidence_total": total,
                    "top_suspect": suspects[0].service if suspects else None,
                    "top_score": suspects[0].score if suspects else None,
                    "weights": weights(
                        load_incident=load_incident, latency_incident=latency_incident
                    ),
                },
                publish=False,
            )
            await session.commit()

        report = AnalysisReport(
            incident_id=incident.public_id,
            window_start=opened,
            window_end=end,
            pre_s=pre_s,
            duration_ms=int((time.perf_counter() - started) * 1000),
            services_considered=len(known),
            evidence_added=added,
            evidence_total=total,
            codes=codes,
            counts=counts,
            suspects=suspects,
            top_suspect=suspects[0].service if suspects else None,
            weights=weights(load_incident=load_incident, latency_incident=latency_incident),
        )
        from app.events.bus import bus

        bus.publish(
            "incident.analysis",
            {
                "incident_id": incident.public_id,
                "id": incident.id,
                "evidence_added": added,
                "evidence_total": total,
                "top_suspect": report.top_suspect,
                "top_score": suspects[0].score if suspects else None,
                "duration_ms": report.duration_ms,
            },
        )
        log.info(
            "incident_analysed",
            incident=incident.public_id,
            evidence=total,
            added=added,
            top=report.top_suspect,
            ms=report.duration_ms,
        )
        return report

    async def _persist_evidence(
        self,
        session: AsyncSession,
        incident: Any,
        *,
        opened: datetime,
        end: datetime,
        metrics: list[MetricAnomaly],
        traces: TraceAnalysis,
        logs: LogAnalysis,
        containers: dict[str, ContainerSignal],
        faults: list[FaultSignal],
        suspects: list[SuspectScore],
        load_incident: bool = False,
        latency_incident: bool = False,
    ) -> tuple[int, list[str], dict[str, int]]:
        added = 0
        codes: list[str] = []
        counts: dict[str, int] = {}

        async def add(
            type_: str,
            source: str,
            service: str | None,
            data: dict[str, Any],
            at: datetime,
        ) -> None:
            nonlocal added
            row, created = await self.evidence.add(
                session,
                incident,
                type_=type_,
                source=source,
                service=service,
                data=data,
                at=at,
            )
            counts[type_] = counts.get(type_, 0) + 1
            if created:
                added += 1
                codes.append(row.code)
            else:
                codes.append(row.code)

        for anomaly in sorted(metrics, key=lambda item: (item.metric, item.service)):
            await add(
                "metric_anomaly",
                "prometheus",
                anomaly.service,
                anomaly.model_dump(mode="json"),
                end,
            )

        for service, first in sorted(traces.onset.items(), key=lambda item: (item[1], item[0])):
            stats = traces.origin.get(service)
            await add(
                "onset",
                "jaeger",
                service,
                {
                    "first_anomalous_span_at": first.isoformat(),
                    "delta_s_from_earliest": round(
                        (first - min(traces.onset.values())).total_seconds(), 3
                    ),
                    "origin_spans": stats.origins if stats else 0,
                },
                first,
            )

        for service, stats in sorted(
            traces.origin.items(), key=lambda item: (-item[1].origins, item[0])
        ):
            if stats.origins == 0 and stats.local_errors == 0:
                continue
            await add(
                "error_origin",
                "jaeger",
                service,
                {
                    "origins": stats.origins,
                    "local_errors": stats.local_errors,
                    "propagated_errors": stats.propagated_errors,
                    "silent_peer_errors": stats.silent_peer_errors,
                    "peers": stats.peers,
                },
                end,
            )

        for sample in traces.samples:
            await add(
                "trace_sample",
                "jaeger",
                sample.origin or sample.root_service,
                sample.model_dump(mode="json"),
                sample.started_at,
            )
        if traces.healthy_control is not None:
            await add(
                "healthy_control",
                "jaeger",
                traces.healthy_control.root_service,
                traces.healthy_control.model_dump(mode="json"),
                traces.healthy_control.started_at,
            )

        for template in logs.templates:
            await add(
                "log_template",
                "opensearch",
                template.service,
                {
                    "template": template.template,
                    "count": template.count,
                    "sample": template.sample,
                },
                end,
            )
        if logs.totals:
            await add(
                "log_cluster",
                "opensearch",
                incident.primary_service,
                {
                    "totals": logs.totals,
                    "new_templates": logs.new_templates,
                    "scanned": logs.scanned,
                },
                end,
            )

        for service, signal in sorted(containers.items()):
            # State, not gauges: live CPU % and memory change every second, so
            # including them made every re-run append a new row per container.
            await add(
                "container_state",
                "docker",
                service,
                signal.model_dump(mode="json", exclude={"cpu_percent", "memory_mb", "detail"}),
                end,
            )

        for fault in faults:
            await add(
                "flag_state",
                "faults",
                fault.target,
                fault.model_dump(mode="json") | {"at": fault.injected_at.isoformat()},
                fault.injected_at,
            )

        if traces.edges:
            await add(
                "topology",
                "jaeger",
                incident.primary_service,
                {
                    "nodes": sorted(
                        {edge.source for edge in traces.edges}
                        | {edge.target for edge in traces.edges}
                    ),
                    "edges": [edge.model_dump(mode="json") for edge in traces.edges],
                    "traces": traces.traces_scanned,
                },
                end,
            )

        if suspects:
            await add(
                "suspect_ranking",
                "analysis",
                suspects[0].service,
                {
                    "ranking": [
                        {"service": item.service, "score": item.score, "rank": item.rank}
                        for item in suspects
                    ],
                    "weights": weights(
                        load_incident=load_incident, latency_incident=latency_incident
                    ),
                },
                end,
            )
        return added, codes, counts

    async def _persist_suspects(
        self, session: AsyncSession, incident: Any, suspects: list[SuspectScore]
    ) -> None:
        await session.execute(delete(Suspect).where(Suspect.incident_id == incident.id))
        for item in suspects:
            session.add(
                Suspect(
                    incident_id=incident.id,
                    service=item.service,
                    score=item.score,
                    rank=item.rank,
                    signals=item.signals,
                )
            )
        await session.flush()


analyzer = Analyzer()

__all__ = ["MAX_WINDOW_S", "AnalysisReport", "Analyzer", "analyzer"]
