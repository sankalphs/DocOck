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
