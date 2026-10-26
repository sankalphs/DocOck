from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.metrics import _anomaly
from app.analysis.signals import ContainerSignal, FaultSignal
from app.analysis.suspects import score_suspects, weights
from app.analysis.traces import OriginStats, TraceAnalysis, analyse_trace, peer_service
from app.evidence.store import evidence_hash, format_code, is_valid_code, parse_code
from app.telemetry.dto import SpanNode, TraceDetail

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _span(
    span_id: str,
    *,
    parent: str | None,
    name: str,
    service: str,
    kind: str,
    start_ms: float,
    duration_ms: float,
    status: str = "error",
    attributes: dict[str, str] | None = None,
) -> SpanNode:
    return SpanNode(
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        service=service,
        kind=kind,
        start_ms=start_ms,
        duration_ms=duration_ms,
        status=status,  # type: ignore[arg-type]
        depth=0,
        attributes=attributes or {},
    )


def _trace(spans: list[SpanNode]) -> TraceDetail:
    return TraceDetail(
        trace_id="a" * 32,
        root_name=spans[0].name,
        root_service=spans[0].service,
        started_at=NOW,
        duration_ms=max(span.duration_ms for span in spans),
        span_count=len(spans),
        services=sorted({span.service for span in spans}),
        error_count=sum(1 for span in spans if span.status == "error"),
        spans=spans,
    )


def test_evidence_codes_round_trip_and_validate() -> None:
    assert format_code(1) == "E0001"
    assert format_code(42) == "E0042"
    assert parse_code("E0042") == 42
    assert is_valid_code("E0001") is True
    assert is_valid_code("E1") is False
    assert is_valid_code("X0001") is False


def test_evidence_hash_is_stable_and_ignores_time() -> None:
    first = evidence_hash(
        type_="metric_anomaly", source="prometheus", service="payment", data={"value": 1}
    )
    second = evidence_hash(
        type_="metric_anomaly", source="prometheus", service="payment", data={"value": 1}
    )
    other = evidence_hash(
        type_="metric_anomaly", source="prometheus", service="payment", data={"value": 2}
    )
    assert first == second
    assert first != other


def test_log_templates_strip_ids_numbers_and_addresses() -> None:
    from app.analysis.logs import template_of

    text = (
        "GetCartAsync called with userId=8369ccca-cf7c-11f1-a2e2-869bd3417425 "
        "in 42ms from 172.18.0.1 trace 9876bad5c4e3f2a1"
    )
    normalized = template_of(text)
    assert "8369ccca" not in normalized
    assert "42ms" not in normalized
    assert "172.18.0.1" not in normalized
    assert "<uuid>" in normalized
    assert "<duration>" in normalized


def test_peer_service_matches_the_most_specific_name() -> None:
    span = _span(
        "c1",
        parent=None,
        name="oteldemo.PaymentService/Charge",
        service="checkout",
        kind="client",
        start_ms=1,
        duration_ms=2,
        attributes={"rpc.service": "oteldemo.PaymentService"},
    )
    assert peer_service(span, ["payment", "frontend", "frontend-proxy"]) == "payment"
    proxy = _span(
        "c2",
        parent=None,
        name="GET",
        service="frontend",
        kind="client",
        start_ms=1,
        duration_ms=2,
        attributes={"server.address": "frontend-proxy"},
    )
    assert peer_service(proxy, ["frontend", "frontend-proxy"]) == "frontend-proxy"


def test_origin_is_the_leaf_that_fails_and_onset_is_completion_order() -> None:
    spans = [
        _span(
            "f",
            parent=None,
            name="GET /cart",
            service="frontend",
            kind="server",
            start_ms=0,
            duration_ms=100,
        ),
        _span(
            "c",
            parent="f",
            name="PlaceOrder",
            service="checkout",
            kind="server",
            start_ms=10,
            duration_ms=80,
        ),
        _span(
            "p",
            parent="c",
            name="Charge",
            service="payment",
            kind="server",
            start_ms=20,
            duration_ms=40,
        ),
    ]
    analysis = TraceAnalysis()
    origin = analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend", "checkout", "payment"],
        baseline_p95={},
    )
    assert origin == "payment"
    assert analysis.origin["payment"].local_errors == 1
    assert analysis.origin["checkout"].propagated_errors == 1
    assert analysis.origin["frontend"].propagated_errors == 1
    # Completion order: payment (60ms) < checkout (90ms) < frontend (100ms).
    assert analysis.onset["payment"] < analysis.onset["checkout"] < analysis.onset["frontend"]
    assert analysis.edges and analysis.edges[0].source in ("frontend", "checkout")


def test_silent_peer_is_blamed_when_no_server_span_exists() -> None:
    spans = [
        _span(
            "f",
            parent=None,
            name="POST /checkout",
            service="frontend",
            kind="server",
            start_ms=0,
            duration_ms=50,
        ),
        _span(
            "c",
            parent="f",
            name="PlaceOrder",
            service="checkout",
            kind="server",
            start_ms=5,
            duration_ms=40,
        ),
        _span(
            "p",
            parent="c",
            name="oteldemo.PaymentService/Charge",
            service="checkout",
            kind="client",
            start_ms=10,
            duration_ms=20,
            attributes={"rpc.service": "oteldemo.PaymentService"},
        ),
    ]
    analysis = TraceAnalysis()
    origin = analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend", "checkout", "payment"],
        baseline_p95={},
    )
    assert origin == "payment"
    assert analysis.origin["payment"].silent_peer_errors == 1
    assert analysis.origin["payment"].peers == {"checkout": 1}


def test_weights_are_normalized_and_sum_to_one() -> None:
    resolved = weights()
    assert resolved.keys() == {
        "error_origin",
        "earliest_onset",
        "local_error_ratio",
        "latency_anomaly",
        "throughput_anomaly",
        "container_unhealthy",
    }
    assert abs(sum(resolved.values()) - 1.0) < 1e-9
    assert resolved["error_origin"] > resolved["latency_anomaly"]


def test_scoring_prefers_the_error_origin_over_the_caller() -> None:
    spans = [
        _span(
            "f",
            parent=None,
            name="GET /cart",
            service="frontend",
            kind="server",
            start_ms=0,
            duration_ms=100,
        ),
        _span(
            "c",
            parent="f",
            name="PlaceOrder",
            service="checkout",
            kind="server",
            start_ms=10,
            duration_ms=80,
        ),
        _span(
            "p",
            parent="c",
            name="Charge",
            service="payment",
            kind="server",
            start_ms=20,
            duration_ms=40,
        ),
    ]
    analysis = TraceAnalysis()
    analyse_trace(
        _trace(spans), analysis=analysis, known=["frontend", "checkout", "payment"], baseline_p95={}
    )
    suspects = score_suspects(
        opened_at=NOW,
        origins=analysis.origin,
        onset=analysis.onset,
        anomalies=[],
        containers={},
        faults=[],
        incident_services=["frontend", "checkout", "payment"],
        primary_service="checkout",
    )
    assert suspects[0].service == "payment"
    assert abs(sum(item.score for item in suspects) - 1.0) < 0.01
    contribution = suspects[0].signals["error_origin"]["contribution"]
    assert isinstance(contribution, int | float) and contribution > 0


def test_scoring_can_use_a_container_signal_alone() -> None:
    suspects = score_suspects(
        opened_at=NOW,
        origins={},
        onset={},
        anomalies=[],
        containers={
            "email": ContainerSignal(
                service="email",
                container="email",
                state="exited",
                health="none",
                paused=False,
                restarts=1,
                oom_killed=False,
                detail="email exited",
            )
        },
        faults=[
            FaultSignal(
                id=1,
                fault_id="service_down",
                target="email",
                mechanism="container_stop",
                status="ACTIVE",
                injected_at=NOW,
            )
        ],
        incident_services=["email"],
        primary_service="email",
    )
    assert suspects[0].service == "email"
    assert suspects[0].score == 1.0
    assert suspects[0].signals["container_unhealthy"]["value"] == 1.0


def test_a_stopped_container_outranks_a_caller_with_more_signals() -> None:
    caller = OriginStats(service="checkout", origins=5, local_errors=5)
    suspects = score_suspects(
        opened_at=NOW,
        origins={"checkout": caller},
        onset={"checkout": NOW},
        anomalies=[],
        containers={
            "email": ContainerSignal(
                service="email",
                container="email",
                state="exited",
                health="none",
                paused=False,
                restarts=0,
                oom_killed=False,
                detail="email exited",
            )
        },
        faults=[],
        incident_services=["checkout", "email"],
        primary_service="checkout",
    )
    assert suspects[0].service == "email"
    assert suspects[1].service == "checkout"
    assert suspects[1].score > suspects[0].score


def test_metric_anomaly_detects_an_error_spike() -> None:
    anomaly = _anomaly("error_rate", "ratio", "up", "payment", [0.0, 0.0, 0.0, 0.0], [1.0, 1.0])
    assert anomaly is not None
    assert anomaly.service == "payment"
    assert anomaly.delta == 1.0
    assert "error rate" in anomaly.detail


def test_metric_anomaly_ignores_noise_below_the_floor() -> None:
    assert _anomaly("error_rate", "ratio", "up", "cart", [0.0, 0.0, 0.0], [0.01, 0.015]) is None
    assert _anomaly("p95_latency", "ms", "up", "ad", [100.0, 110.0, 100.0], [120.0, 130.0]) is None


def test_metric_anomaly_detects_a_latency_spike() -> None:
    anomaly = _anomaly("p95_latency", "ms", "up", "ad", [100.0, 100.0, 100.0], [500.0, 900.0])
    assert anomaly is not None
    assert anomaly.ratio is not None and anomaly.ratio > 5


def test_onset_metric_of_a_slow_span_is_used_for_latency_faults() -> None:
    spans = [
        _span(
            "a",
            parent=None,
            name="GetAds",
            service="ad",
            kind="server",
            start_ms=0,
            duration_ms=900,
            status="unset",
        )
    ]
    analysis = TraceAnalysis()
    analyse_trace(_trace(spans), analysis=analysis, known=["ad"], baseline_p95={"ad": 50.0})
    assert "ad" in analysis.onset
    assert analysis.samples[0].origin is None


def test_edge_timeout_blames_the_slow_callee_not_the_proxy() -> None:
    """The only ERROR is the proxy's 15 s timeout; the delay is product-catalog."""
    spans = [
        _span(
            "p",
            parent=None,
            name="GET",
            service="frontend-proxy",
            kind="server",
            start_ms=0,
            duration_ms=15006,
        ),
        _span(
            "e",
            parent="p",
            name="router frontend egress",
            service="frontend-proxy",
            kind="client",
            start_ms=0,
            duration_ms=15005,
            status="unset",
        ),
        _span(
            "f",
            parent="e",
            name="GET",
            service="frontend",
            kind="server",
            start_ms=1,
            duration_ms=15005,
            status="unset",
        ),
        _span(
            "r",
            parent="f",
            name="GET /api/products",
            service="frontend",
            kind="server",
            start_ms=1,
            duration_ms=25448,
            status="unset",
        ),
        _span(
            "c",
            parent="r",
            name="ListProducts",
            service="frontend",
            kind="client",
            start_ms=3,
            duration_ms=25444,
            status="unset",
        ),
        _span(
            "s",
            parent="c",
            name="ListProducts",
            service="product-catalog",
            kind="server",
            start_ms=97,
            duration_ms=25338,
            status="unset",
        ),
        _span(
            "d",
            parent="s",
            name="astronomy-db",
            service="product-catalog",
            kind="client",
            start_ms=413,
            duration_ms=24980,
            status="unset",
        ),
    ]
    analysis = TraceAnalysis()
    origin = analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend", "frontend-proxy", "product-catalog", "astronomy-db"],
        baseline_p95={},
        catalog_services={"frontend", "frontend-proxy", "product-catalog"},
    )
    assert origin == "product-catalog"
    assert analysis.origin["product-catalog"].origins == 1
    assert analysis.origin["frontend-proxy"].propagated_errors == 1
    assert analysis.origin["frontend-proxy"].origins == 0


def test_edge_timeout_with_a_dead_catalog_dependency_blames_that_dependency() -> None:
    spans = [
        _span(
            "p",
            parent=None,
            name="GET",
            service="frontend-proxy",
            kind="server",
            start_ms=0,
            duration_ms=15006,
        ),
        _span(
            "f",
            parent="p",
            name="GET",
            service="frontend",
            kind="server",
            start_ms=1,
            duration_ms=15005,
            status="unset",
        ),
        _span(
            "c",
            parent="f",
            name="oteldemo.PaymentService/Charge",
            service="frontend",
            kind="client",
            start_ms=3,
            duration_ms=15000,
            status="unset",
        ),
    ]
    analysis = TraceAnalysis()
    origin = analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend", "frontend-proxy", "payment"],
        baseline_p95={},
        catalog_services={"frontend", "frontend-proxy", "payment"},
    )
    assert origin == "payment"
    assert analysis.origin["payment"].origins == 1
    assert analysis.origin["payment"].peers == {"frontend-proxy": 1}


def test_load_incident_scores_on_load_signals_only() -> None:
    from app.analysis.suspects import weights

    normal = weights()
    load = weights(load_incident=True)
    assert load["error_origin"] == 0.0
    assert load["earliest_onset"] == 0.0
    assert load["local_error_ratio"] == 0.0
    assert abs(sum(load.values()) - 1.0) < 1e-9
    assert load["throughput_anomaly"] > normal["throughput_anomaly"]


def test_a_flood_blames_the_entry_point_not_a_small_service_that_also_doubled() -> None:
    entry = _anomaly("throughput", "rps", "change", "frontend", [25.0, 26.0, 25.5], [58.0, 57.0])
    small = _anomaly("throughput", "rps", "change", "email", [0.15, 0.14, 0.15], [0.36, 0.37])
    assert entry is not None and small is not None
    suspects = score_suspects(
        opened_at=NOW,
        origins={},
        onset={},
        anomalies=[small, entry],
        containers={},
        faults=[],
        incident_services=["frontend"],
        primary_service="frontend",
        load_incident=True,
    )
    assert suspects[0].service == "frontend"
    assert suspects[0].score > 0.9


def test_a_hung_call_into_a_partitioned_peer_has_its_onset_at_the_call() -> None:
    spans = [
        _span(
            "c",
            parent=None,
            name="oteldemo.CheckoutService/PlaceOrder",
            service="checkout",
            kind="server",
            start_ms=0,
            duration_ms=73_000,
            status="ok",
        ),
        _span(
            "x",
            parent="c",
            name="oteldemo.PaymentService/Charge",
            service="checkout",
            kind="client",
            start_ms=1_000,
            duration_ms=71_000,
            status="ok",
            attributes={"rpc.service": "oteldemo.PaymentService"},
        ),
    ]
    analysis = TraceAnalysis()
    analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["checkout", "payment"],
        baseline_p95={"checkout": 400.0},
        latency_incident=True,
    )
    assert set(analysis.onset) == {"payment"}
    assert analysis.onset["payment"] == NOW


def test_an_injected_proxy_delay_blames_the_caller_that_requested_it() -> None:
    spans = [
        _span(
            "w",
            parent=None,
            name="GET",
            service="frontend-web",
            kind="client",
            start_ms=0,
            duration_ms=5068,
            status="ok",
        ),
        _span(
            "p",
            parent="w",
            name="GET",
            service="frontend-proxy",
            kind="server",
            start_ms=10,
            duration_ms=5001,
            status="ok",
            attributes={"response_flags": "DI", "upstream_cluster": "image-provider"},
        ),
        _span(
            "e",
            parent="p",
            name="router image-provider egress",
            service="frontend-proxy",
            kind="client",
            start_ms=5005,
            duration_ms=2,
            status="ok",
        ),
    ]
    analysis = TraceAnalysis()
    known = ["frontend-web", "frontend-proxy", "frontend", "image-provider"]
    analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=known,
        baseline_p95={"frontend-proxy": 113.0},
        latency_incident=True,
    )
    assert set(analysis.onset) == {"frontend-web"}
    suspects = score_suspects(
        opened_at=NOW,
        origins=analysis.origin,
        onset=analysis.onset,
        anomalies=[],
        containers={},
        faults=[],
        incident_services=["frontend-proxy"],
        primary_service="frontend-proxy",
        latency_incident=True,
    )
    # frontend-web is the frontend's browser bundle: the deployable is frontend.
    assert suspects[0].service == "frontend"


def test_an_injected_delay_from_an_untraced_browser_blames_the_frontend() -> None:
    """INC-00063: the browser never exported the parent of the proxy's span."""
    spans = [
        _span(
            "p",
            parent="browser-span-not-exported",
            name="GET",
            service="frontend-proxy",
            kind="server",
            start_ms=0,
            duration_ms=2880,
            status="ok",
            attributes={"response_flags": "DI,DC", "upstream_cluster": "image-provider"},
        ),
    ]
    analysis = TraceAnalysis()
    analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend-proxy", "frontend", "image-provider"],
        baseline_p95={"frontend-proxy": 48.0},
        latency_incident=True,
    )
    assert set(analysis.onset) == {"frontend-web"}
    proxy = _anomaly("p95_latency", "ms", "up", "frontend-proxy", [48.0, 49.0, 48.5], [6101.0])
    frontend = _anomaly("p95_latency", "ms", "up", "frontend", [377.0, 378.0, 379.0], [5125.0])
    assert proxy is not None and frontend is not None
    suspects = score_suspects(
        opened_at=NOW,
        origins=analysis.origin,
        onset=analysis.onset,
        anomalies=[proxy, frontend],
        containers={},
        faults=[],
        incident_services=["frontend-proxy"],
        primary_service="frontend-proxy",
        latency_incident=True,
    )
    assert suspects[0].service == "frontend"


def test_a_slow_proxy_without_injection_flags_keeps_the_blame() -> None:
    spans = [
        _span(
            "p",
            parent=None,
            name="GET",
            service="frontend-proxy",
            kind="server",
            start_ms=0,
            duration_ms=5001,
            status="ok",
            attributes={"response_flags": "-"},
        ),
    ]
    analysis = TraceAnalysis()
    analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend-proxy"],
        baseline_p95={"frontend-proxy": 113.0},
        latency_incident=True,
    )
    assert set(analysis.onset) == {"frontend-proxy"}


def test_a_flagd_event_stream_through_the_proxy_is_not_a_slow_span() -> None:
    spans = [
        _span(
            "s",
            parent=None,
            name="POST",
            service="frontend-proxy",
            kind="server",
            start_ms=0,
            duration_ms=15_000,
            status="ok",
            attributes={"response_flags": "DC", "upstream_cluster": "flagservice"},
        ),
    ]
    analysis = TraceAnalysis()
    analyse_trace(
        _trace(spans),
        analysis=analysis,
        known=["frontend-proxy", "flagd"],
        baseline_p95={"frontend-proxy": 113.0},
        latency_incident=True,
    )
    assert analysis.onset == {}


def test_a_detached_container_is_an_observed_cause() -> None:
    def signal(networks: list[str] | None) -> ContainerSignal:
        return ContainerSignal(
            service="payment",
            container="payment",
            state="running",
            health="healthy",
            paused=False,
            restarts=0,
            oom_killed=False,
            networks=networks,
        )

    assert signal([]).unhealthy
    assert not signal(["docock-demo_default"]).unhealthy
    assert not signal(None).unhealthy
