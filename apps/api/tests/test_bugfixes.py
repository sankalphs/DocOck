"""Regression tests for bugs found in the post-P16 audit."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import _resume_point
from app.domain.state import IncidentStatus, InvalidTransition
from app.errors import register_error_handlers
from app.incidents.service import normalize_ref, parse_numeric_ref
from app.main import app
from app.middleware import MAX_REQUEST_ID, sanitize_request_id


# --- oversized ids used to reach asyncpg and fail as a 500 ----------------------
def test_ids_beyond_bigint_are_rejected_before_the_database() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    huge = "9" * 20
    for path in (
        f"/api/approvals/{huge}",
        f"/api/agent/runs/{huge}",
        f"/api/faults/{huge}",
    ):
        response = client.get(path)
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == "validation_error"


def test_numeric_incident_ref_only_accepts_ascii_bigint() -> None:
    assert parse_numeric_ref("42") == 42
    assert parse_numeric_ref("9" * 20) is None
    assert parse_numeric_ref("0") is None
    assert parse_numeric_ref("٤٢") is None  # Arabic-Indic digits pass str.isdigit()
    assert parse_numeric_ref("INC-00042") is None


def test_incident_refs_are_case_insensitive() -> None:
    assert normalize_ref("inc-00042") == "INC-00042"
    assert normalize_ref("  INC-00042 ") == "INC-00042"
    assert normalize_ref("42") == "42"


# --- SSE resume across an API restart --------------------------------------------
def test_resume_point_within_the_current_epoch() -> None:
    assert _resume_point("5", None, last_id=10) == 5
    assert _resume_point(None, 7, last_id=10) == 7
    assert _resume_point(None, None, last_id=10) is None
    assert _resume_point("garbage", None, last_id=10) is None


def test_resume_point_from_a_previous_process_replays_everything() -> None:
    # The browser saw id 523 before the restart; this process is at 4.
    assert _resume_point("523", None, last_id=4) is None
    assert _resume_point(None, 99, last_id=4) is None
    assert _resume_point("-3", None, last_id=4) is None


# --- a lost transition race is a conflict, not a crash ---------------------------
def test_invalid_transition_is_a_409() -> None:
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.get("/race")
    async def race() -> None:
        raise InvalidTransition(IncidentStatus.RESOLVED, IncidentStatus.ESCALATED)

    response = TestClient(test_app, raise_server_exceptions=False).get("/race")
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "invalid_transition"
    assert body["details"] == {"current": "RESOLVED", "target": "ESCALATED"}


# --- request ids are echoed into headers and logs --------------------------------
def test_request_id_is_sanitized_and_capped() -> None:
    assert sanitize_request_id(" abc-123.x:y_z ") == "abc-123.x:y_z"
    assert sanitize_request_id("a b<script>") == "abscript"
    assert len(sanitize_request_id("x" * 500)) == MAX_REQUEST_ID
    assert sanitize_request_id("<<>>") == ""

    client = TestClient(app)
    response = client.get("/api/version", headers={"X-Request-ID": "y" * 300})
    assert response.headers["X-Request-ID"] == "y" * MAX_REQUEST_ID
    minted = client.get("/api/version", headers={"X-Request-ID": "!!!"})
    assert len(minted.headers["X-Request-ID"]) == 12
