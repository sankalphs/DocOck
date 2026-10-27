"""Record real telemetry responses into apps/api/tests/fixtures/.

The fixture tests parse these files; the live contract tests hit the same
endpoints. Run this when the demo is upgraded and a payload shape changes:

    scripts\\record-telemetry-fixtures.ps1      (or)
    uv run --project apps/api python scripts/record-telemetry-fixtures.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "apps" / "api" / "tests" / "fixtures"
PROMETHEUS = "http://127.0.0.1:9090"
JAEGER = "http://127.0.0.1:16686/jaeger/ui"
OPENSEARCH = "http://127.0.0.1:9200"
PROJECT = "docock-demo"


def write(name: str, payload: object) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  {name:34s} {path.stat().st_size:>8,d} bytes")


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    client = httpx.Client(timeout=30.0)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=10)

    print("prometheus")
    write(
        "prometheus_label_values.json",
        client.get(f"{PROMETHEUS}/api/v1/label/__name__/values").json(),
    )
    write(
        "prometheus_instant.json",
        client.get(
            f"{PROMETHEUS}/api/v1/query",
            params={
                "query": "sum by (service_name) (rate(traces_span_metrics_calls_total"
                '{status_code="STATUS_CODE_ERROR"}[5m]))'
            },
        ).json(),
    )
    write(
        "prometheus_range.json",
        client.get(
            f"{PROMETHEUS}/api/v1/query_range",
            params={
                "query": "sum by (service_name) (rate(traces_span_metrics_calls_total[5m]))",
                "start": f"{start.timestamp():.0f}",
                "end": f"{now.timestamp():.0f}",
                "step": "30",
            },
        ).json(),
    )
    write(
        "prometheus_histogram.json",
        client.get(
            f"{PROMETHEUS}/api/v1/query",
            params={
                "query": "histogram_quantile(0.95, sum by (le, service_name) "
                "(rate(traces_span_metrics_duration_milliseconds_bucket[5m])))"
            },
        ).json(),
    )

    print("jaeger")
    write("jaeger_services.json", client.get(f"{JAEGER}/api/v3/services").json())
    search = client.get(
        f"{JAEGER}/api/v3/traces",
        params={
            "query.service_name": "cart",
            "query.startTimeMin": rfc3339(start),
            "query.startTimeMax": rfc3339(now),
            "query.num_traces": "1",
        },
    )
    write("jaeger_traces.json", search.json())

    result = search.json().get("result", {})
    trace_id = None
    for resource in result.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                trace_id = span.get("traceId")
                break
            if trace_id:
                break
        if trace_id:
            break
    if trace_id:
        write("jaeger_trace.json", client.get(f"{JAEGER}/api/v3/traces/{trace_id}").json())
        write("jaeger_trace_id.json", {"trace_id": trace_id})

    print("opensearch")
    body = {
        "size": 3,
        "track_total_hits": True,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-15m"}}}]}},
    }
    write("opensearch_logs.json", client.post(f"{OPENSEARCH}/otel-logs*/_search", json=body).json())

    print("docker")
    ps = docker(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={PROJECT}",
        "--format",
        "{{json .}}",
    )
    write("docker_ps.json", [json.loads(line) for line in ps.splitlines() if line.strip()])
    inspect = docker("inspect", "payment")
    write("docker_inspect.json", json.loads(inspect))
    stats = docker("stats", "--no-stream", "--format", "{{json .}}", "payment")
    write("docker_stats.json", [json.loads(line) for line in stats.splitlines() if line.strip()])

    print(f"wrote fixtures to {FIXTURES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
