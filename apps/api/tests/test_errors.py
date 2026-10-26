from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import ApiError, register_error_handlers
from app.main import app


def test_unknown_route_uses_error_envelope() -> None:
    response = TestClient(app).get("/api/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]
    assert body["error"]["request_id"]
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}


def test_validation_error_is_enveloped() -> None:
    # `refresh` is a bool query parameter; a non-bool triggers RequestValidationError.
    response = TestClient(app).get("/api/health", params={"refresh": "maybe"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"]
    assert body["details"][0]["loc"][-1] == "refresh"


def test_api_error_carries_details_and_status() -> None:
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.get("/boom")
    async def boom() -> None:
        raise ApiError(409, "conflict", "already done", details={"id": 3})

    response = TestClient(test_app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "conflict"
    assert body["message"] == "already done"
    assert body["details"] == {"id": 3}


def test_request_id_is_echoed_and_generated() -> None:
    client = TestClient(app)

    with_header = client.get("/api/version", headers={"X-Request-ID": "abc123"})
    assert with_header.headers["X-Request-ID"] == "abc123"

    generated = client.get("/api/version")
    assert len(generated.headers["X-Request-ID"]) == 12
    assert float(generated.headers["X-Response-Time-ms"]) >= 0
