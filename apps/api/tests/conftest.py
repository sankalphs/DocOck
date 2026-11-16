from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

FixtureFactory = Callable[[dict[str, Any]], "FixtureTransport"]


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureTransport(httpx.AsyncBaseTransport):
    """Serves recorded payloads by URL path prefix.

    Longest prefix wins, so `/api/v3/traces/{id}` is not shadowed by
    `/api/v3/traces`.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = sorted(routes.items(), key=lambda item: len(item[0]), reverse=True)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, payload in self.routes:
            if prefix.lstrip("/") in path:
                return httpx.Response(200, json=payload, request=request)
        return httpx.Response(
            404,
            json={"error": {"message": f"no fixture for {path}"}},
            request=request,
        )


@pytest.fixture
def fixture_transport() -> FixtureFactory:
    def _build(routes: dict[str, Any]) -> FixtureTransport:
        return FixtureTransport(routes)

    return _build


@pytest.fixture(autouse=True)
def _isolated_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Tests never read the live lab through the drift scanner.

    By default the lab has no drift; a test sets `drift.items` to simulate some.
    The desired-state file lives in the test's tmp dir.
    """
    from datetime import UTC, datetime

    from app.remediation import drift as drift_module

    state: dict[str, Any] = {"items": []}
    store = drift_module.DesiredStateStore(tmp_path / "desired-state.json")
    monkeypatch.setattr(drift_module.scanner, "store", store)

    async def fake_scan(
        *, services: list[str] | None = None, learn: bool = False
    ) -> drift_module.DriftReport:
        items = [
            item
            for item in state["items"]
            if services is None
            or item.service in services
            or (item.fix is not None and item.fix.target in services)
        ]
        return drift_module.DriftReport(at=datetime.now(UTC), items=list(items))

    monkeypatch.setattr(drift_module.scanner, "scan", fake_scan)
    return state
