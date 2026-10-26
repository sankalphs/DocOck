from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_route
from app.health import DependencyProbe, HealthReport, overall_status
from app.main import app, create_app


def _probe(name: str, status: str, *, critical: bool = False) -> DependencyProbe:
    return DependencyProbe(
        name=name,
        status=status,  # type: ignore[arg-type]
        critical=critical,
        detail=f"{name} {status}",
        latency_ms=1.0,
    )


def _report(*probes: DependencyProbe) -> HealthReport:
    return HealthReport(
        status=overall_status(list(probes)),
        app="DocOck",
        version="0.1.0",
        environment="local",
        checked_at=datetime.now(UTC),
        uptime_s=12.5,
        dependencies=list(probes),
    )


def _provider(report: HealthReport) -> Callable[[], Awaitable[HealthReport]]:
    async def _inner() -> HealthReport:
        return report

    return _inner


def test_health_returns_every_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(
        _probe("postgres", "ok", critical=True),
        _probe("docker", "ok"),
        _probe("demo", "ok"),
        _probe("prometheus", "ok"),
        _probe("jaeger", "ok"),
        _probe("opensearch", "ok"),
        _probe("llm", "unconfigured"),
    )
    monkeypatch.setattr(health_route, "get_health_report", _provider(report))

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "DocOck"
    assert [dep["name"] for dep in body["dependencies"]] == [
        "postgres",
        "docker",
        "demo",
        "prometheus",
        "jaeger",
        "opensearch",
        "llm",
    ]
    assert body["dependencies"][6]["status"] == "unconfigured"


def test_critical_dependency_down_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(_probe("postgres", "down", critical=True), _probe("docker", "ok"))
    monkeypatch.setattr(health_route, "get_health_report", _provider(report))

    response = TestClient(app).get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_non_critical_dependency_down_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(_probe("postgres", "ok", critical=True), _probe("jaeger", "down"))
    monkeypatch.setattr(health_route, "get_health_report", _provider(report))

    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_overall_status_matrix() -> None:
    assert overall_status([_probe("postgres", "ok", critical=True)]) == "ok"
    assert overall_status([_probe("postgres", "unconfigured", critical=True)]) == "ok"
    assert overall_status([_probe("postgres", "down", critical=True)]) == "down"
    assert overall_status([_probe("docker", "down")]) == "degraded"


def test_version_is_exposed() -> None:
    response = TestClient(create_app()).get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "DocOck"
    assert body["environment"] == "local"
