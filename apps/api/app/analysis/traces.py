"""Trace analysis: error origin, onset ordering, topology, and samples.

The demo's spans are enough to separate a service that *fails locally* from one
whose error is a downstream call:

- a failing SERVER span with a failing child is **propagated**;
- a failing SERVER span with no failing child is the **origin**;
- a failing CLIENT span whose peer has no SERVER span at all (a partition, a
  stopped dependency, a bad address) blames the **peer** — the "silent peer"
  case that makes `network_partition` and `paymentUnreachable` attributable
  even though the callee emits nothing.

Onset is the earliest anomalous span *completion* per service: a nested callee
finishes its failing span before its caller, so the origin sorts first. A span is
anomalous if it errored or took longer than twice that service's pre-incident p95.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.telemetry.dto import SpanNode, TraceDetail
from app.telemetry.jaeger import JaegerAdapter

PEER_KEYS = ("peer.service", "rpc.service", "server.address", "net.peer.name", "http.host")
SERVER_KINDS = ("server", "internal")
# Envoy response flags for a fault its HTTP fault filter injected because the
# request asked for it (`x-envoy-fault-delay-request` / `-abort-request`):
# DI = delay injected, FI = abort injected. The proxy executed the fault; the
# caller that sent the header is where it comes from (`imageSlowLoad`).
INJECTED_FLAGS = frozenset({"DI", "FI"})
# The edge proxy's untraced caller is the browser running the frontend bundle:
# its request span carries a parent id the browser never exported (S4 on the
# live stack), so the header's sender is `frontend-web`, not the proxy.
EDGE_PROXY = "frontend-proxy"
EDGE_CLIENT = "frontend-web"
PRE_OPEN_S = 240
# A callee span that closes within this of its caller's timeout was cancelled
# with it (S7: the frontend span ends 4 ms before the proxy's 15 s timeout).
CANCEL_TOLERANCE_MS = 50.0
# frontend-proxy routes that reach the lab's own machinery (envoy.tmpl.yaml).
# The browser's flagd event stream is the main one: a long-lived POST whose
# duration is the stream's lifetime, so it looked like a 15 s "slow" span that
# completed before the real symptom and won onset for the proxy.
LAB_UPSTREAMS = frozenset(
    {
        "flagservice",
        "flagd-ui",
        "grafana",
        "jaeger",
        "loadgen",
        "opamp",
        "opentelemetry_collector_http",
        "profiles",
        "telemetry-docs",
    }
)


class OriginStats(BaseModel):
    service: str
    local_errors: int = 0
    propagated_errors: int = 0
    silent_peer_errors: int = 0
    origins: int = 0
    peers: dict[str, int] = Field(default_factory=dict)


class TraceSample(BaseModel):
    trace_id: str
    root_service: str
    root_name: str
    started_at: datetime
    duration_ms: float
    error_count: int
    services: list[str]
    origin: str | None = None


class Edge(BaseModel):
    source: str
    target: str
    calls: int = 1
    errors: int = 0


class TraceAnalysis(BaseModel):
    origin: dict[str, OriginStats] = Field(default_factory=dict)
    onset: dict[str, datetime] = Field(default_factory=dict)
    samples: list[TraceSample] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    healthy_control: TraceSample | None = None
    traces_scanned: int = 0
    span_count: int = 0


def peer_service(span: SpanNode, known: list[str]) -> str | None:
    haystacks = [span.attributes.get(key, "") for key in PEER_KEYS]
    haystacks.append(span.name)
    for raw in haystacks:
        if not raw:
            continue
        lowered = raw.lower()
        for service in sorted(known, key=len, reverse=True):
            if service in lowered:
                return service
    return None


def _error(span: SpanNode) -> bool:
    return span.status == "error"


def _stats(analysis: TraceAnalysis, service: str) -> OriginStats:
    stats = analysis.origin.get(service)
    if stats is None:
        stats = OriginStats(service=service)
        analysis.origin[service] = stats
    return stats


def _note_onset(
    analysis: TraceAnalysis,
    service: str,
    at: datetime,
) -> None:
    current = analysis.onset.get(service)
    if current is None or at < current:
        analysis.onset[service] = at


def _requested_by(span: SpanNode, by_id: dict[str, SpanNode]) -> str | None:
    """The caller that asked the proxy to inject this span's fault, if any."""
    flags = {flag.strip() for flag in span.attributes.get("response_flags", "").split(",")}
    if not flags & INJECTED_FLAGS:
        return None
    parent = by_id.get(span.parent_span_id or "")
    if parent is None:
        return EDGE_CLIENT if span.service == EDGE_PROXY else None
    if parent.service == span.service:
        return None
    return parent.service


def _trace_slice(detail: TraceDetail) -> tuple[list[SpanNode], dict[str, list[SpanNode]]]:
    spans = list(detail.spans)
    children: dict[str, list[SpanNode]] = {}
    for span in spans:
        if span.parent_span_id:
            children.setdefault(span.parent_span_id, []).append(span)
    return spans, children


def _time_sink(span: SpanNode, children: dict[str, list[SpanNode]]) -> SpanNode:
    """The span reached by walking down the child that owns most of the time."""
    current = span
    seen = {span.span_id}
    while True:
        kids = [child for child in children.get(current.span_id, []) if child.duration_ms > 0]
        if not kids:
            break
        best = max(kids, key=lambda child: child.duration_ms)
        if best.duration_ms < 0.5 * current.duration_ms or best.span_id in seen:
            break
        seen.add(best.span_id)
        current = best
    return current


def _cancelled_with(span: SpanNode, failing: SpanNode, children: dict[str, list[SpanNode]]) -> bool:
    """`span` closed with `failing`'s timeout; the work it waited for is unexported."""
    return (
        span is not failing
        and span.kind in SERVER_KINDS
        and not _error(span)
        and not children.get(span.span_id)
        and abs((span.start_ms + span.duration_ms) - (failing.start_ms + failing.duration_ms))
        <= CANCEL_TOLERANCE_MS
    )


def _ancestors(span: SpanNode, by_id: dict[str, SpanNode]) -> list[SpanNode]:
    chain: list[SpanNode] = []
    seen = {span.span_id}
    current = by_id.get(span.parent_span_id or "")
    while current is not None and current.span_id not in seen:
        chain.append(current)
        seen.add(current.span_id)
        current = by_id.get(current.parent_span_id or "")
    return chain


def _downstream_origin(
    span: SpanNode,
    children: dict[str, list[SpanNode]],
    known: list[str],
    catalog_services: set[str],
    server_services: set[str],
) -> str | None:
    """Where the time in a timed-out span actually went.

    A proxy or a caller is often the only span marked ERROR: it gave up after a
    timeout while the callee kept working. Walking down the child that owns most
    of the parent's duration follows the delay to its source. A chain that ends
    at a client span with no SERVER span for its peer is the "silent peer" case
    (only catalog services qualify; a database that never emits a span is
    infrastructure, not a suspect).

    None when the trace is still in flight: the walk ends at a span without
    children that closed together with the timeout, so it was cancelled, and
    the work it waited for has not been exported yet (S7: frontend's outer
    span ends at the proxy's 15 s timeout; the 120 s checkout chain arrives
    minutes later).
    """
    current = _time_sink(span, children)
    if current is span:
        return span.service
    if _cancelled_with(current, span, children):
        return None
    if current.kind == "client":
        peer = peer_service(current, known)
        if peer in catalog_services and peer not in server_services:
            return peer
    return current.service


def analyse_trace(
    detail: TraceDetail,
    *,
    analysis: TraceAnalysis,
    known: list[str],
    baseline_p95: dict[str, float],
    catalog_services: set[str] | None = None,
    latency_incident: bool = False,
) -> str | None:
    spans, children = _trace_slice(detail)
    by_id = {span.span_id: span for span in spans}
    server_services = {span.service for span in spans if span.kind in SERVER_KINDS}
    catalog = catalog_services if catalog_services is not None else set(known)
    local_origins: list[tuple[int, datetime, str]] = []

    def start_of(span: SpanNode) -> datetime:
        return detail.started_at + timedelta(milliseconds=span.start_ms)

    def end_of(span: SpanNode) -> datetime:
        return start_of(span) + timedelta(milliseconds=span.duration_ms)

    for span in spans:
        if span.attributes.get("upstream_cluster") in LAB_UPSTREAMS:
            continue
        span_start = start_of(span)
        span_end = end_of(span)
        if _error(span):
            # Completion order, not start order: a nested callee finishes its
            # failing span before its caller does, so the origin is earliest.
            # A latency incident is not explained by timeouts at its edge, so
            # only the slow-span branch below may move its onset. A timeout
            # whose work is still in flight completed before its cause did, so
            # its completion says nothing about the order (S7).
            sink = _time_sink(span, children)
            if not latency_incident and not _cancelled_with(sink, span, children):
                _note_onset(analysis, span.service, span_end)
            if span.kind in SERVER_KINDS:
                failing_children = [
                    child for child in children.get(span.span_id, []) if _error(child)
                ]
                stats = _stats(analysis, span.service)
                requested = _requested_by(span, by_id)
                if failing_children:
                    stats.propagated_errors += 1
                elif (
                    requested is None
                    and sink is not span
                    and sink.kind in SERVER_KINDS
                    and _error(sink)
                ):
                    # The time went into a span that failed itself, so its own
                    # branch counts that failure. Envoy's egress span between
                    # frontend-proxy and the frontend is never marked ERROR, so
                    # without this the proxy credits a second origin (S3: the
                    # walk stops at checkout because the failing Charge call to
                    # the unreachable payment takes 0 ms).
                    stats.propagated_errors += 1
                else:
                    origin = requested or _downstream_origin(
                        span, children, known, catalog, server_services
                    )
                    if origin is None:
                        stats.propagated_errors += 1
                    elif origin == span.service:
                        stats.local_errors += 1
                        stats.origins += 1
                        local_origins.append((span.depth, span_end, span.service))
                    else:
                        # The failing span is a timeout, not a local defect.
                        stats.propagated_errors += 1
                        target = _stats(analysis, origin)
                        target.local_errors += 1
                        target.origins += 1
                        target.peers[span.service] = target.peers.get(span.service, 0) + 1
                        local_origins.append((span.depth + 1, span_end, origin))
            elif span.kind == "client":
                peer = peer_service(span, known)
                if peer and peer not in server_services:
                    stats = _stats(analysis, peer)
                    stats.silent_peer_errors += 1
                    stats.origins += 1
                    stats.peers[span.service] = stats.peers.get(span.service, 0) + 1
                    local_origins.append((span.depth + 1, span_start, peer))
                    _note_onset(analysis, peer, span_start)
        elif span.kind in SERVER_KINDS:
            baseline = baseline_p95.get(span.service)
            if baseline and span.duration_ms > max(2 * baseline, 500):
                if any(
                    _cancelled_with(span, ancestor, children)
                    for ancestor in _ancestors(span, by_id)
                    if _error(ancestor)
                ):
                    # Slow only because its caller's timeout closed it; its
                    # children are not exported yet.
                    continue
                # The slow span may be a caller waiting on a callee; attribute
                # the latency to where the time is actually spent.
                origin = _requested_by(span, by_id) or _downstream_origin(
                    span, children, known, catalog, server_services
                )
                if origin is None:
                    continue
                # A call into a silent peer (partition, bad address) hangs until
                # the peer comes back, so it *completes* last; its onset is when
                # the call went out, as in the error branch above.
                silent = origin != span.service and origin not in server_services
                _note_onset(analysis, origin, span_start if silent else span_end)

    # Topology: parent -> child across the span tree (self-edges excluded).
    edges: dict[tuple[str, str], Edge] = {}
    for span in spans:
        parent = by_id.get(span.parent_span_id or "")
        if parent is None or parent.service == span.service:
            continue
        key = (parent.service, span.service)
        edge = edges.get(key)
        if edge is None:
            edge = Edge(source=parent.service, target=span.service)
            edges[key] = edge
            analysis.edges.append(edge)
        else:
            edge.calls += 1
        if _error(span):
            edge.errors += 1

    origin_service = max(local_origins, key=lambda item: item[0])[2] if local_origins else None
    analysis.samples.append(
        TraceSample(
            trace_id=detail.trace_id,
            root_service=detail.root_service,
            root_name=detail.root_name,
            started_at=detail.started_at,
            duration_ms=detail.duration_ms,
            error_count=detail.error_count,
            services=list(detail.services),
            origin=origin_service,
        )
    )
    return origin_service


def _sample_of(detail: TraceDetail, origin: str | None = None) -> TraceSample:
    return TraceSample(
        trace_id=detail.trace_id,
        root_service=detail.root_service,
        root_name=detail.root_name,
        started_at=detail.started_at,
        duration_ms=detail.duration_ms,
        error_count=detail.error_count,
        services=list(detail.services),
        origin=origin,
    )


async def analyse_traces(
    jaeger: JaegerAdapter,
    *,
    opened_at: datetime,
    end: datetime,
    known_services: list[str],
    baseline_p95: dict[str, float],
    focus_services: list[str] | None = None,
    catalog_services: set[str] | None = None,
    latency_incident: bool = False,
    max_traces: int = 25,
) -> TraceAnalysis:
    analysis = TraceAnalysis()
    # Jaeger filters on trace *start*. A hung call (a partitioned payment) can
    # start minutes before the incident opens, and the detector itself needs up
    # to 90 s of debounce on top of a 2-minute rate window, so the window opens
    # PRE_OPEN_S before the incident. It is fixed, not relative to now, so
    # re-analysing a closed incident sees the same traces.
    window_start = opened_at - timedelta(seconds=PRE_OPEN_S)
    summaries: dict[str, Any] = {}
    # The busiest service dominates a global "latest N" search, and the demo's
    # storefront serves far more traces than checkout does. Search the affected
    # services explicitly so a low-traffic payment error is not drowned out.
    for service in (focus_services or [])[:6]:
        try:
            found = await jaeger.search(
                service=service, errors_only=True, start=window_start, end=end, limit=max_traces
            )
            for summary in found.traces:
                summaries[summary.trace_id] = summary
        except Exception:
            continue
        if latency_incident:
            # A slowdown does not have to fail: the demo's image delay produces
            # no ERROR at all, and a catalogue lock times out at the edge while
            # the callee's own span is slow-but-successful. Search slow traces
            # too, so the descent has something to walk.
            threshold = max(2 * baseline_p95.get(service, 0.0), 500.0)
            try:
                found = await jaeger.search(
                    service=service,
                    start=window_start,
                    end=end,
                    limit=max_traces,
                    min_duration_ms=threshold,
                )
                for summary in found.traces:
                    summaries.setdefault(summary.trace_id, summary)
            except Exception:
                continue
    try:
        found = await jaeger.search(errors_only=True, start=window_start, end=end, limit=max_traces)
        for summary in found.traces:
            summaries.setdefault(summary.trace_id, summary)
    except Exception:
        pass
    if latency_incident and focus_services:
        slowest = max(max(2 * baseline_p95.get(s, 0.0), 500.0) for s in focus_services)
        try:
            found = await jaeger.search(
                start=window_start, end=end, limit=max_traces, min_duration_ms=slowest
            )
            for summary in found.traces:
                summaries.setdefault(summary.trace_id, summary)
        except Exception:
            pass

    details: list[TraceDetail] = []
    for summary in list(summaries.values())[:max_traces]:
        try:
            details.append(await jaeger.trace(summary.trace_id))
        except Exception:
            continue
    analysis.traces_scanned = len(details)
    analysis.span_count = sum(len(detail.spans) for detail in details)
    # An error incident is ranked by error count; a latency incident has no
    # error to sort by, so the longest traces are the informative ones.
    if latency_incident:
        details.sort(key=lambda item: item.duration_ms, reverse=True)
    else:
        details.sort(key=lambda item: item.error_count, reverse=True)
    for detail in details[:max_traces]:
        analyse_trace(
            detail,
            analysis=analysis,
            known=known_services,
            baseline_p95=baseline_p95,
            catalog_services=catalog_services,
            latency_incident=latency_incident,
        )
    analysis.samples.sort(key=lambda item: item.started_at)
    analysis.samples = analysis.samples[:6]

    # One healthy control from the same window, when available.
    try:
        healthy = await jaeger.search(limit=10, start=window_start, end=end)
        for summary in healthy.traces:
            if summary.error_count == 0:
                detail = await jaeger.trace(summary.trace_id)
                analysis.healthy_control = _sample_of(detail)
                break
    except Exception:
        pass
    return analysis


__all__ = [
    "Edge",
    "OriginStats",
    "TraceAnalysis",
    "TraceSample",
    "analyse_trace",
    "analyse_traces",
    "peer_service",
]
