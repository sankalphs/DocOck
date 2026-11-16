"""Operator console helpers: attention counts, notes, search, shared audit lock."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.attention import worst
from app.api.incidents import router as incidents_router
from app.errors import ApiError, register_error_handlers
from app.incidents.service import IncidentService
from app.remediation.audit import ActionAuditEntry, ActionAuditLog
from app.reports.builder import event_detail

APP_DIR = Path(__file__).resolve().parents[1] / "app"
EVENTS_TS = Path(__file__).resolve().parents[2] / "dashboard" / "lib" / "events.ts"


def test_worst_severity_ranks_sev1_first() -> None:
    assert worst(["sev3", "sev1", "sev2"]) == "sev1"
    assert worst(["sev4"]) == "sev4"
    assert worst([]) is None
    # Unknown labels never outrank a real severity.
    assert worst(["weird", "sev3"]) == "sev3"


async def test_note_without_text_is_refused_before_any_write() -> None:
    class NoSession:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"session.{name} must not be touched")

    with pytest.raises(ApiError) as caught:
        await IncidentService().add_note(
            NoSession(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            actor="op",
            text="   \n ",
        )
    assert caught.value.code == "note_empty"


def test_note_endpoint_validates_its_body() -> None:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(incidents_router, prefix="/api")
    client = TestClient(app)
    assert client.post("/api/incidents/INC-00001/notes", json={"text": ""}).status_code == 422
    too_long = {"text": "x" * 2001}
    assert client.post("/api/incidents/INC-00001/notes", json=too_long).status_code == 422
    search = client.get("/api/incidents", params={"q": "x" * 121})
    assert search.status_code == 422


def test_note_reads_as_one_report_line() -> None:
    assert event_detail("note", {"actor": "ana", "text": "rolled back the flag"}) == (
        "ana: rolled back the flag"
    )
    assert event_detail("note", {"text": "no actor"}) == "operator: no actor"


async def test_separate_audit_logs_on_one_file_never_interleave(tmp_path: Path) -> None:
    """Approvals, the executor and the verifier each own an ActionAuditLog.

    Each object's asyncio.Lock only serialises its own writes. Linux appends a
    large line atomically, so this contract test passes there either way; on
    Windows (the lab's main host) appends from several threads are not atomic,
    which is why the lock is shared per file.
    """
    path = tmp_path / "actions.jsonl"
    logs = [ActionAuditLog(path) for _ in range(3)]
    blob = {"blob": "y" * 64_000}

    async def write(log: ActionAuditLog, index: int) -> None:
        for n in range(15):
            await log.append(
                ActionAuditEntry(actor=f"writer-{index}", event="e", outcome=str(n), before=blob)
            )

    await asyncio.gather(*(write(log, i) for i, log in enumerate(logs)))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 45
    for line in lines:
        json.loads(line)  # raises on a spliced line
    assert len(await logs[0].tail(1000)) == 45


def _published_event_types() -> set[str]:
    found: set[str] = set()
    pattern = re.compile(r"(?:bus\.publish\(\s*|event_type=)\"([a-z_]+\.[a-z_.]+)\"", re.MULTILINE)
    ternary = re.compile(r"\"([a-z_]+\.[a-z_.]+)\" if [^\n]+ else \"([a-z_]+\.[a-z_.]+)\"")
    for source in APP_DIR.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        found.update(pattern.findall(text))
        for first, second in ternary.findall(text):
            found.update({first, second})
    return found


@pytest.mark.skipif(not EVENTS_TS.exists(), reason="dashboard checkout not present")
def test_dashboard_subscribes_to_every_published_event() -> None:
    """EventSource has no wildcard: an unlisted type never reaches the UI."""
    published = _published_event_types()
    assert {"incident.note", "fault.reverted", "incident.opened"} <= published
    listed = set(re.findall(r"\"([a-z_]+\.[a-z_.]+)\"", EVENTS_TS.read_text(encoding="utf-8")))
    assert published - listed == set()
