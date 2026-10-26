from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, create_app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "DocOck"
    assert body["version"]
    assert body["environment"] == "local"


def test_version_is_exposed() -> None:
    client = TestClient(create_app())
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json()["name"] == "DocOck"


def test_unknown_route_is_404() -> None:
    client = TestClient(app)
    assert client.get("/api/does-not-exist").status_code == 404
