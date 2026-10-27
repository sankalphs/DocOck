"""P16 hardening: rate limits, statistics helpers."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handlers
from app.ratelimit import RULES, RateLimiter, _rule, rate_limit
from app.stats import distribution, percentile


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_every_api_route_passes_the_limiter() -> None:
    from app.api import api_router

    assert any(dep.dependency is rate_limit for dep in api_router.dependencies)


def test_reads_are_never_limited() -> None:
    limiter = RateLimiter(rules=(_rule("all", r"^/api/", 1),))
    for _ in range(5):
        assert limiter.check("GET", "/api/faults", "c") is None
    assert limiter.rejected == 0


def test_sliding_window_rejects_then_frees_a_slot() -> None:
    clock = Clock()
    limiter = RateLimiter(rules=(_rule("inject", r"^/api/faults$", 2, 60),), clock=clock)
    assert limiter.check("POST", "/api/faults", "c") is None
    clock.now += 10
    assert limiter.check("POST", "/api/faults", "c") is None
    wait = limiter.check("POST", "/api/faults", "c")
    assert wait == 50.0
    assert limiter.rejected == 1
    # Another client has its own window.
    assert limiter.check("POST", "/api/faults", "other") is None
    clock.now += 50
    assert limiter.check("POST", "/api/faults", "c") is None


def test_first_matching_rule_applies() -> None:
    limiter = RateLimiter()
    assert limiter.match("POST", "/api/faults").name == "fault_inject"  # type: ignore[union-attr]
    assert limiter.match("DELETE", "/api/faults/12").name == "faults"  # type: ignore[union-attr]
    assert limiter.match("POST", "/api/incidents/INC-00001/investigate").name == "investigate"  # type: ignore[union-attr]
    assert limiter.match("POST", "/api/approvals/4/approve").name == "approvals"  # type: ignore[union-attr]
    assert limiter.match("POST", "/api/incidents/INC-00001/approvals").name == "approvals"  # type: ignore[union-attr]
    assert limiter.match("PUT", "/api/settings/llm").name == "settings"  # type: ignore[union-attr]
    assert limiter.match("POST", "/api/incidents/INC-00001/ack").name == "writes"  # type: ignore[union-attr]
    assert limiter.match("GET", "/api/events") is None
    assert RULES[-1].name == "writes"


def test_dependency_returns_429_with_retry_after(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import ratelimit

    monkeypatch.setattr(
        ratelimit, "limiter", RateLimiter(rules=(_rule("inject", r"^/api/faults$", 1, 60),))
    )
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.post("/api/faults", dependencies=[Depends(rate_limit)])
    async def inject() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(test_app)
    assert client.post("/api/faults").status_code == 200
    limited = client.post("/api/faults")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.json()["error"]["details"] == {"rule": "inject", "limit": 1}
    assert 1 <= int(limited.headers["Retry-After"]) <= 60


def test_dependency_can_be_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import ratelimit

    monkeypatch.setattr(
        ratelimit, "limiter", RateLimiter(rules=(_rule("inject", r"^/api/faults$", 1, 60),))
    )
    monkeypatch.setattr(ratelimit.settings, "rate_limit_enabled", False)
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.post("/api/faults", dependencies=[Depends(rate_limit)])
    async def inject() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(test_app)
    assert [client.post("/api/faults").status_code for _ in range(3)] == [200, 200, 200]


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([5.0], 0.95) == 5.0
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 0.5) == 10.0
    assert percentile(values, 0.95) == 19.0
    assert percentile(values, 1.0) == 20.0


def test_distribution_summary() -> None:
    summary = distribution([100.0, 300.0, 200.0])
    assert summary.count == 3
    assert summary.p50 == 200.0
    assert summary.max == 300.0
    assert summary.mean == 200.0
    assert distribution([]).mean is None
