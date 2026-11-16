"""Incident notes, search and CSV export."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.incidents import CSV_COLUMNS, csv_cell, incidents_csv
from app.domain.state import IncidentStatus
from app.incidents.service import IncidentSummary, escape_like
from app.main import app
from app.reports.builder import event_detail


def _summary(**overrides: object) -> IncidentSummary:
    base: dict[str, object] = {
        "id": 7,
        "public_id": "INC-00007",
        "status": IncidentStatus.RESOLVED,
        "severity": "sev2",
        "title": "checkout error rate 12%",
        "primary_service": "checkout",
        "signature": "error_rate:checkout",
        "opened_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "closed_at": None,
        "duration_s": 90,
        "services": ["checkout", "frontend"],
        "signals": [],
        "event_count": 4,
    }
    base.update(overrides)
    return IncidentSummary.model_validate(base)


def test_escape_like_makes_wildcards_literal() -> None:
    assert escape_like("50%_off\\") == "50\\%\\_off\\\\"
    assert escape_like("checkout") == "checkout"


def test_csv_has_header_and_one_row_per_incident() -> None:
    text = incidents_csv([_summary(), _summary(public_id="INC-00008", id=8)])
    rows = list(csv.reader(io.StringIO(text)))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert len(rows) == 3
    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["public_id"] == "INC-00007"
    assert row["status"] == "RESOLVED"
    assert row["services"] == "checkout;frontend"
    assert row["opened_at"].startswith("2026-01-02T03:04:05")
    assert row["closed_at"] == ""


def test_csv_neutralises_spreadsheet_formulas() -> None:
    assert csv_cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert csv_cell("+1") == "'+1"
    assert csv_cell("@cmd") == "'@cmd"
    assert csv_cell("plain") == "plain"
    assert csv_cell(None) == ""
    assert csv_cell(42) == "42"


def test_report_timeline_renders_notes() -> None:
    assert event_detail("note", {"author": "sam", "text": "rolled back by hand"}) == (
        "sam: rolled back by hand"
    )
    assert event_detail("note", {"text": "x"}) == "operator: x"


def test_note_body_is_validated_before_the_database() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    empty = client.post("/api/incidents/INC-00001/notes", json={"text": ""})
    assert empty.status_code == 422
    too_long = client.post("/api/incidents/INC-00001/notes", json={"text": "x" * 2001})
    assert too_long.status_code == 422
    long_author = client.post(
        "/api/incidents/INC-00001/notes", json={"text": "ok", "author": "a" * 65}
    )
    assert long_author.status_code == 422


def test_export_route_is_not_shadowed_by_ref() -> None:
    # Routes are matched in declaration order, which OpenAPI preserves.
    paths = list(app.openapi()["paths"])
    export = paths.index("/api/incidents/export.csv")
    detail = paths.index("/api/incidents/{ref}")
    assert export < detail
