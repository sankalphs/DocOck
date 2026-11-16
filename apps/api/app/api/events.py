"""Server-sent events.

One stream for the whole dashboard: the client subscribes once, filters by event
prefix locally, and resumes with `Last-Event-ID` after a reload. `: ping`
comments every 15 s keep intermediaries from closing an idle connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.events.bus import BusEvent, bus

router = APIRouter(tags=["events"])

HEARTBEAT_S = 15.0
MAX_REPLAY = 500


def _resume_point(
    last_event_id: str | None, query_id: int | None, *, last_id: int | None = None
) -> int | None:
    """Where to resume, or None to replay the whole history.

    Event ids restart at 1 with the process. A browser that reconnects after an
    API restart still sends the id it saw from the old process; taken at face
    value, every new event would be skipped until the counter caught up. An id
    ahead of the bus therefore means "another epoch": replay everything.
    """
    resume: int | None = query_id
    if resume is None and last_event_id:
        try:
            resume = int(last_event_id.strip())
        except ValueError:
            return None
    if resume is None or resume < 0:
        return None
    current = bus.last_id if last_id is None else last_id
    if resume > current:
        return None
    return resume


async def _stream(
    request: Request,
    after_id: int | None,
) -> AsyncIterator[str]:
    queue = bus.subscribe()
    try:
        yield "retry: 3000\n\n"
        replay = bus.replay(after_id)
        if len(replay) > MAX_REPLAY:
            replay = replay[-MAX_REPLAY:]
        for event in replay:
            yield event.to_sse()

        last_sent = replay[-1].id if replay else (after_id or 0)
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event.id <= last_sent:
                continue
            last_sent = event.id
            yield event.to_sse()
    finally:
        bus.unsubscribe(queue)


@router.get(
    "/events",
    summary="SSE stream of lab events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int | None = Query(default=None, description="Replay events after this id"),
) -> StreamingResponse:
    after_id = _resume_point(last_event_id, since)
    return StreamingResponse(
        _stream(request, after_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["BusEvent", "router"]
