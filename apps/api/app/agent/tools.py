"""The agent's tool registry (PLAN §11). Every tool is read-only.

Each tool has a JSON schema (sent to the model), a Pydantic argument model
(`extra=forbid`, bounded values; a service name must be one this incident knows,
because service names end up inside PromQL/Jaeger/OpenSearch queries), and a
handler.

**Tool results are evidence.** Whatever a tool observes live is stored as an
incident-scoped evidence row (deduped by hash, so asking twice returns the same
`E####`), and the code is handed back with the result. The agent can therefore
cite what it looked at, and the validator's rule "every cited code exists in this
incident and was shown to the model" covers tool output too. Tools that only
re-read stored evidence return the existing codes.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.db.models import Baseline, Evidence, Fault, Incident, Suspect
from app.db.session import SessionFactory
from app.evidence.store import CODE_RE, EvidenceStore
from app.evidence.store import store as evidence_store
from app.models.llm.base import ToolSpec
from app.telemetry.docker import DockerAdapter
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.logs import LogsAdapter
from app.telemetry.prometheus import PrometheusAdapter

SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{16,32}$")
MAX_OUTPUT_CHARS = 6000

MetricName = Literal[
    "error_rate", "p95_latency", "throughput", "error_count", "container_cpu", "container_memory"
]
RateWindow = Literal["1m", "2m", "5m"]


class ToolError(Exception):
    """A refused call (bad arguments, unknown service). Reported to the model."""


@dataclass
class ToolContext:
    incident_id: int
    incident_ref: str
    opened_at: datetime
    window_start: datetime
    window_end: datetime
    known_services: set[str]
    prometheus: PrometheusAdapter
    jaeger: JaegerAdapter
    logs: LogsAdapter
    docker: DockerAdapter
    evidence: EvidenceStore = field(default_factory=lambda: evidence_store)


@dataclass
class ToolResult:
    output: dict[str, Any]
    codes: list[str] = field(default_factory=list)


# --- argument models ------------------------------------------------------------
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ServiceArgs(_Args):
    service: str = Field(description="Service name as it appears in the incident")

    @field_validator("service")
    @classmethod
    def _shape(cls, value: str) -> str:
        if not SERVICE_RE.match(value):
            raise ValueError("service must be lower-case letters, digits and dashes")
        return value


class NoArgs(_Args):
    pass


class MetricsArgs(_ServiceArgs):
    metric: MetricName = Field(description="Which metric to read")
    window: RateWindow = Field(default="1m", description="Rate window for each point")


class TracesArgs(_ServiceArgs):
    errors_only: bool = Field(default=True)
    min_duration_ms: float | None = Field(default=None, ge=0, le=120000)
    limit: int = Field(default=5, ge=1, le=10)


class TraceArgs(_Args):
    trace_id: str = Field(description="32-hex trace id from a trace search or evidence row")

    @field_validator("trace_id")
    @classmethod
    def _shape(cls, value: str) -> str:
        if not TRACE_ID_RE.match(value):
            raise ValueError("trace_id must be 16-32 lower-case hex characters")
        return value


class LogsArgs(_ServiceArgs):
    level: Literal["error", "warn", "info"] | None = Field(default="error")
    query: str | None = Field(default=None, max_length=80, description="Plain-text filter")
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[\w .:/\-]*", value):
            raise ValueError("query must be plain text (letters, digits, space, . : / -)")
        return value


class EvidenceArgs(_Args):
    codes: list[str] = Field(min_length=1, max_length=10)

    @field_validator("codes")
    @classmethod
    def _codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not CODE_RE.match(code):
                raise ValueError(f"{code!r} is not an E#### code")
        return value


# --- helpers ----------------------------------------------------------------------
def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return float(f"{value:.{digits}g}")


def _check_service(ctx: ToolContext, service: str) -> None:
    if service not in ctx.known_services:
        raise ToolError(
            f"unknown service {service!r}; known: {', '.join(sorted(ctx.known_services))}"
        )


async def _record(
    ctx: ToolContext, *, type_: str, source: str, service: str | None, data: dict[str, Any]
) -> str:
    async with ctx.evidence.lock, SessionFactory() as session:
        incident = await session.get(Incident, ctx.incident_id)
        assert incident is not None
        row, _created = await ctx.evidence.add(
            session, incident, type_=type_, source=source, service=service, data=data
        )
        code = row.code
        await session.commit()
    return code


async def _stored(
    incident_id: int, *, types: tuple[str, ...], service: str | None = None
) -> list[Evidence]:
    async with SessionFactory() as session:
        query = select(Evidence).where(
            Evidence.incident_id == incident_id, Evidence.type.in_(types)
        )
        if service is not None:
            query = query.where(Evidence.service == service)
        return list((await session.execute(query.order_by(Evidence.code))).scalars().all())


# --- handlers -----------------------------------------------------------------------
async def get_incident_summary(ctx: ToolContext, _args: NoArgs) -> ToolResult:
    async with SessionFactory() as session:
        incident = await session.get(Incident, ctx.incident_id)
        assert incident is not None
        suspects = (
            (
                await session.execute(
                    select(Suspect)
                    .where(Suspect.incident_id == ctx.incident_id)
                    .order_by(Suspect.rank)
                )
            )
            .scalars()
            .all()
        )
        evidence = (
            (
                await session.execute(
                    select(Evidence)
                    .where(Evidence.incident_id == ctx.incident_id)
                    .order_by(Evidence.code)
                )
            )
            .scalars()
            .all()
        )
    trigger = incident.trigger or {}
    return ToolResult(
        output={
            "incident": incident.public_id,
            "status": incident.status,
            "title": incident.title,
            "primary_service": incident.primary_service,
            "primary_signal": trigger.get("primary_signal"),
            "signals": trigger.get("signals") or [],
            "suspects": [
                {"rank": item.rank, "service": item.service, "score": item.score}
                for item in suspects
            ],
            "evidence": [
                {"code": row.code, "type": row.type, "service": row.service} for row in evidence
            ],
        },
        codes=[row.code for row in evidence],
    )


async def get_evidence(ctx: ToolContext, args: EvidenceArgs) -> ToolResult:
    async with SessionFactory() as session:
        rows = (
            (
                await session.execute(
                    select(Evidence).where(
                        Evidence.incident_id == ctx.incident_id, Evidence.code.in_(args.codes)
                    )
                )
            )
            .scalars()
            .all()
        )
    found = {row.code: row for row in rows}
    missing = [code for code in args.codes if code not in found]
    return ToolResult(
        output={
            "evidence": [
                {
                    "code": row.code,
                    "type": row.type,
                    "source": row.source,
                    "service": row.service,
                    "data": row.data,
                }
                for row in sorted(found.values(), key=lambda item: item.code)
            ],
            "not_in_this_incident": missing,
        },
        codes=sorted(found),
    )


async def get_metrics(ctx: ToolContext, args: MetricsArgs) -> ToolResult:
    _check_service(ctx, args.service)
    result = await ctx.prometheus.metric(
        args.metric,
        service=args.service,
        window=args.window,
        start=ctx.window_start,
        end=ctx.window_end,
        step_s=15,
    )
    opened = ctx.opened_at
    series_out: list[dict[str, Any]] = []
    for series in result.series[:4]:
        values = [point.v for point in series.points]
        before = [p.v for p in series.points if p.t < opened]
        after = [p.v for p in series.points if p.t >= opened]
        series_out.append(
            {
                "labels": {k: v for k, v in series.labels.items() if k in ("service_name",)},
                "points": len(values),
                "first": _round(values[0]) if values else None,
                "last": _round(values[-1]) if values else None,
                "min": _round(min(values)) if values else None,
                "max": _round(max(values)) if values else None,
                "mean_before": _round(statistics.fmean(before)) if before else None,
                "mean_after": _round(statistics.fmean(after)) if after else None,
            }
        )
    data = {
        "metric": args.metric,
        "unit": result.unit,
        "window": args.window,
        "from": ctx.window_start.isoformat(),
        "to": ctx.window_end.isoformat(),
        "series": series_out,
    }
    code = await _record(
        ctx, type_="metric_query", source="prometheus", service=args.service, data=data
    )
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


async def get_traces(ctx: ToolContext, args: TracesArgs) -> ToolResult:
    _check_service(ctx, args.service)
    found = await ctx.jaeger.search(
        service=args.service,
        errors_only=args.errors_only,
        min_duration_ms=args.min_duration_ms,
        limit=args.limit,
        start=ctx.window_start,
        end=ctx.window_end,
    )
    traces = [
        {
            "trace_id": item.trace_id,
            "root": f"{item.root_service} {item.root_name}",
            "started_at": item.started_at.isoformat(),
            "duration_ms": _round(item.duration_ms),
            "spans": item.span_count,
            "errors": item.error_count,
            "services": item.services[:8],
        }
        for item in found.traces[: args.limit]
    ]
    data = {
        "service": args.service,
        "errors_only": args.errors_only,
        "min_duration_ms": args.min_duration_ms,
        "traces": traces,
    }
    code = await _record(
        ctx, type_="trace_search", source="jaeger", service=args.service, data=data
    )
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


async def get_trace(ctx: ToolContext, args: TraceArgs) -> ToolResult:
    detail = await ctx.jaeger.trace(args.trace_id)
    spans = sorted(detail.spans, key=lambda span: (span.status != "error", -span.duration_ms))
    data = {
        "trace_id": detail.trace_id,
        "root": f"{detail.root_service} {detail.root_name}",
        "duration_ms": _round(detail.duration_ms),
        "span_count": detail.span_count,
        "error_count": detail.error_count,
        "spans": [
            {
                "service": span.service,
                "name": span.name[:80],
                "kind": span.kind,
                "status": span.status,
                "duration_ms": _round(span.duration_ms),
                "depth": span.depth,
                **(
                    {
                        "peer": span.attributes.get("rpc.service")
                        or span.attributes.get("server.address")
                    }
                    if span.kind.lower().endswith("client")
                    else {}
                ),
            }
            for span in spans[:20]
        ],
    }
    code = await _record(
        ctx, type_="trace_detail", source="jaeger", service=detail.root_service, data=data
    )
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


async def get_logs(ctx: ToolContext, args: LogsArgs) -> ToolResult:
    _check_service(ctx, args.service)
    found = await ctx.logs.search(
        service=args.service,
        level=args.level,
        text=args.query,
        limit=args.limit,
        start=ctx.window_start,
        end=ctx.window_end,
    )
    data = {
        "service": args.service,
        "level": args.level,
        "query": args.query,
        "total": found.total,
        "lines": [
            {
                "at": record.timestamp.isoformat(),
                "severity": record.severity,
                "body": record.body[:200],
                "trace_id": record.trace_id,
            }
            for record in found.records[: args.limit]
        ],
    }
    code = await _record(
        ctx, type_="log_search", source="opensearch", service=args.service, data=data
    )
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


async def get_log_templates(ctx: ToolContext, args: _ServiceArgs) -> ToolResult:
    _check_service(ctx, args.service)
    rows = await _stored(ctx.incident_id, types=("log_template",), service=args.service)
    return ToolResult(
        output={
            "service": args.service,
            "new_templates": [{"code": row.code, **row.data} for row in rows],
            "note": "templates absent from the 5 minutes before the incident",
        },
        codes=[row.code for row in rows],
    )


async def get_container_health(ctx: ToolContext, args: _ServiceArgs) -> ToolResult:
    _check_service(ctx, args.service)
    info = await ctx.docker.container(args.service)
    data = {
        "container": info.name,
        "state": info.state,
        "health": info.health,
        "paused": info.paused,
        "oom_killed": info.oom_killed,
        "restarts": info.restart_count,
        "networks": info.networks,
        "cpu_limit_cores": info.cpu_limit_cores,
        "memory_limit_mb": info.memory_limit_mb,
        "memory_used_mb": _round(info.memory_used_mb, 3),
        "cpu_percent": _round(info.cpu_percent, 3),
        "status": info.status_text,
    }
    code = await _record(
        ctx, type_="container_health", source="docker", service=args.service, data=data
    )
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


async def get_topology(ctx: ToolContext, _args: NoArgs) -> ToolResult:
    rows = await _stored(ctx.incident_id, types=("topology",))
    if not rows:
        return ToolResult(output={"topology": None, "note": "no traced edges for this incident"})
    row = rows[-1]
    return ToolResult(output={"evidence_code": row.code, **row.data}, codes=[row.code])


async def get_active_flags(ctx: ToolContext, _args: NoArgs) -> ToolResult:
    stored = await _stored(ctx.incident_id, types=("flag_state",))
    async with SessionFactory() as session:
        live = (
            (
                await session.execute(
                    select(Fault).where(Fault.status.in_(["PROPAGATING", "ACTIVE", "REVERTING"]))
                )
            )
            .scalars()
            .all()
        )
    output: dict[str, Any] = {
        "during_incident": [{"code": row.code, **row.data} for row in stored],
    }
    codes = [row.code for row in stored]
    data = {
        "active_now": sorted(
            ({"fault": item.type, "target": item.target, "status": item.status} for item in live),
            key=lambda entry: (entry["target"], entry["fault"]),
        )
    }
    code = await _record(ctx, type_="active_faults", source="faults", service=None, data=data)
    output.update({"evidence_code": code, **data})
    codes.append(code)
    return ToolResult(output=output, codes=codes)


async def get_service_baseline(ctx: ToolContext, args: _ServiceArgs) -> ToolResult:
    _check_service(ctx, args.service)
    async with SessionFactory() as session:
        rows = (
            (
                await session.execute(
                    select(Baseline)
                    .where(Baseline.service == args.service)
                    .order_by(Baseline.computed_at.desc())
                )
            )
            .scalars()
            .all()
        )
    latest: dict[str, Baseline] = {}
    for row in rows:
        latest.setdefault(row.metric, row)
    data = {
        "service": args.service,
        "baselines": {
            metric: {"value": _round(row.value), "window_s": row.window_s}
            for metric, row in sorted(latest.items())
        },
    }
    if not latest:
        return ToolResult(output={**data, "note": "no persisted baseline for this service"})
    code = await _record(ctx, type_="baseline", source="detector", service=args.service, data=data)
    return ToolResult(output={"evidence_code": code, **data}, codes=[code])


# --- registry ------------------------------------------------------------------------
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args: type[BaseModel]
    handler: Handler

    def spec(self) -> ToolSpec:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


TOOLS: dict[str, ToolDef] = {
    tool.name: tool
    for tool in (
        ToolDef(
            "get_incident_summary",
            "Incident trigger, detector signals, suspect ranking and the evidence index.",
            NoArgs,
            get_incident_summary,
        ),
        ToolDef(
            "get_evidence",
            "Full data of up to 10 evidence rows of THIS incident by code.",
            EvidenceArgs,
            get_evidence,
        ),
        ToolDef(
            "get_metrics",
            "One metric for one service over the incident window (15 s points); "
            "returns before/after means and a citable evidence code.",
            MetricsArgs,
            get_metrics,
        ),
        ToolDef(
            "get_traces",
            "Search traces of a service inside the incident window.",
            TracesArgs,
            get_traces,
        ),
        ToolDef(
            "get_trace",
            "One trace: failing and slowest spans first, with client peers.",
            TraceArgs,
            get_trace,
        ),
        ToolDef(
            "get_logs",
            "Log lines of a service inside the incident window.",
            LogsArgs,
            get_logs,
        ),
        ToolDef(
            "get_log_templates",
            "Log templates that are new in the incident window for a service.",
            _ServiceArgs,
            get_log_templates,
        ),
        ToolDef(
            "get_container_health",
            "Live Docker state of a service's container: state, health, networks, limits.",
            _ServiceArgs,
            get_container_health,
        ),
        ToolDef(
            "get_topology",
            "Service call graph observed in the incident's traces, with error counts.",
            NoArgs,
            get_topology,
        ),
        ToolDef(
            "get_active_flags",
            "Injected faults/flags during the incident and those active now.",
            NoArgs,
            get_active_flags,
        ),
        ToolDef(
            "get_service_baseline",
            "The detector's healthy baseline (error rate, p95, throughput) for a service.",
            _ServiceArgs,
            get_service_baseline,
        ),
    )
}


def register(tool: ToolDef) -> None:
    """Later phases add tools (P12: retrieve_similar_incidents)."""
    TOOLS[tool.name] = tool


def tool_specs() -> list[ToolSpec]:
    return [tool.spec() for tool in TOOLS.values()]


async def call_tool(ctx: ToolContext, name: str, arguments: dict[str, Any] | None) -> ToolResult:
    """Validate and run one call. Raises ToolError for anything the model must fix."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name!r}; available: {', '.join(TOOLS)}")
    if arguments is None:
        raise ToolError("arguments are not valid JSON")
    try:
        args = tool.args.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ToolError(f"invalid arguments: {problems}") from exc
    return await tool.handler(ctx, args)


def incident_window(
    incident: Incident, *, pre_s: int, max_s: int = 900
) -> tuple[datetime, datetime]:
    opened = (
        incident.opened_at if incident.opened_at.tzinfo else incident.opened_at.replace(tzinfo=UTC)
    )
    closed = incident.closed_at
    if closed is not None and closed.tzinfo is None:
        closed = closed.replace(tzinfo=UTC)
    end = min(closed or datetime.now(UTC), opened + timedelta(seconds=max_s))
    return opened - timedelta(seconds=pre_s), end


__all__ = [
    "MAX_OUTPUT_CHARS",
    "TOOLS",
    "ToolContext",
    "ToolDef",
    "ToolError",
    "ToolResult",
    "call_tool",
    "incident_window",
    "register",
    "tool_specs",
]
