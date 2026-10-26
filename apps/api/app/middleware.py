"""Request context: correlation id, access log, timing headers.

Pure ASGI rather than `BaseHTTPMiddleware` so SSE responses are not buffered or
delayed by an extra task group.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1").strip()
        request_id = incoming or uuid.uuid4().hex[:12]

        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - started) * 1000
                status_holder["status"] = message["status"]
                out = MutableHeaders(scope=message)
                out[REQUEST_ID_HEADER] = request_id
                out["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
                # SSE must not be cached or buffered by an intermediary.
                if out.get("content-type", "").startswith("text/event-stream"):
                    out["Cache-Control"] = "no-cache, no-transform"
                    out["X-Accel-Buffering"] = "no"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info(
                "http_request",
                status=status_holder["status"],
                duration_ms=round(elapsed_ms, 2),
            )
            structlog.contextvars.clear_contextvars()
