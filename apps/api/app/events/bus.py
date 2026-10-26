"""In-process event bus with replay.

Every state change in the lab (fault, incident, agent, approval, action,
verification) is published here. The SSE endpoint replays history for a client
that reconnects with `Last-Event-ID`, which is what makes the dashboard
recoverable after a reload mid-incident.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_HISTORY = 1000
MAX_QUEUE = 256


@dataclass(slots=True, frozen=True)
class BusEvent:
    id: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_sse(self) -> str:
        import json

        payload = json.dumps(self.data, separators=(",", ":"), default=str)
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


class EventBus:
    def __init__(self, history: int = MAX_HISTORY) -> None:
        self._history: deque[BusEvent] = deque(maxlen=history)
        self._subscribers: set[asyncio.Queue[BusEvent]] = set()
        self._counter = 0

    @property
    def last_id(self) -> int:
        return self._counter

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> BusEvent:
        self._counter += 1
        event = BusEvent(id=self._counter, type=event_type, data=data or {})
        self._history.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client must not block the bus. It will replay from
                # Last-Event-ID when it reconnects.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(event)
        return event

    def replay(self, after_id: int | None) -> list[BusEvent]:
        if after_id is None:
            return list(self._history)
        return [event for event in self._history if event.id > after_id]

    def subscribe(self) -> asyncio.Queue[BusEvent]:
        queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BusEvent]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
