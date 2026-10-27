"""Rate limits for mutating endpoints (P16).

A sliding-window counter per (rule, client address), in memory: the API is a
single local process, so there is no shared store to coordinate. Reads are
never limited; the SSE stream and health probes stay free. The first rule that
matches a request applies, and the catch-all bounds every other write.

A limited request gets the normal error envelope with code `rate_limited`, HTTP
429 and a `Retry-After` header.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from app.config import settings
from app.errors import ApiError

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    limit: int
    window_s: float


def _rule(name: str, pattern: str, limit: int, window_s: float = 60.0) -> Rule:
    return Rule(name, re.compile(pattern), limit, window_s)


# Sized for a human at the console and for the scenario runner, which injects
# one fault and runs one investigation every few minutes.
RULES: tuple[Rule, ...] = (
    _rule("fault_inject", r"^/api/faults$", 10),
    _rule("faults", r"^/api/faults/", 30),
    _rule("investigate", r"^/api/incidents/[^/]+/(investigate|analyze)$", 10),
    _rule("approvals", r"^/api/(approvals/\d+/|incidents/[^/]+/approvals$)", 30),
    _rule("settings", r"^/api/settings", 20),
    _rule("memory", r"^/api/memory/", 20),
    _rule("writes", r"^/api/", 120),
)


class RateLimiter:
    def __init__(
        self, rules: tuple[Rule, ...] = RULES, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rules = rules
        self.clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self.rejected = 0

    def match(self, method: str, path: str) -> Rule | None:
        if method.upper() not in WRITE_METHODS:
            return None
        return next((rule for rule in self.rules if rule.pattern.search(path)), None)

    def check(self, method: str, path: str, client: str) -> float | None:
        """None when allowed (and counted), otherwise seconds until a slot frees."""
        rule = self.match(method, path)
        if rule is None:
            return None
        now = self.clock()
        hits = self._hits.setdefault((rule.name, client), deque())
        while hits and now - hits[0] >= rule.window_s:
            hits.popleft()
        if len(hits) >= rule.limit:
            self.rejected += 1
            return max(0.0, rule.window_s - (now - hits[0]))
        hits.append(now)
        return None

    def status(self) -> dict[str, object]:
        return {
            "enabled": settings.rate_limit_enabled,
            "rejected": self.rejected,
            "rules": [
                {
                    "name": r.name,
                    "pattern": r.pattern.pattern,
                    "limit": r.limit,
                    "window_s": r.window_s,
                }
                for r in self.rules
            ],
        }

    def reset(self) -> None:
        self._hits.clear()
        self.rejected = 0


limiter = RateLimiter()


async def rate_limit(request: Request) -> None:
    """Router dependency: raise 429 when the caller exceeds its rule."""
    if not settings.rate_limit_enabled:
        return
    client = request.client.host if request.client else "unknown"
    wait = limiter.check(request.method, request.url.path, client)
    if wait is not None:
        rule = limiter.match(request.method, request.url.path)
        retry = max(1, int(wait + 0.999))
        raise ApiError(
            429,
            "rate_limited",
            f"too many requests for {rule.name if rule else 'this endpoint'}; retry in {retry} s",
            details={"rule": rule.name if rule else None, "limit": rule.limit if rule else None},
            headers={"Retry-After": str(retry)},
        )


__all__ = ["RULES", "RateLimiter", "Rule", "limiter", "rate_limit"]
