"""TTL cache with single-flight loading.

The dashboard polls a handful of endpoints every few seconds; all of them fan
out to the same three stores. Without a cache, one browser tab turns into ten
Prometheus queries per tick. Caching here (not in the UI) also means two clients
share one upstream read.

Single-flight matters more than TTL on Windows: Docker Desktop's loopback proxy
serialises new connections, so N concurrent misses would queue anyway. The lock
collapses them into one request.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, ttl_s: float = 10.0, max_entries: int = 256) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._entries: dict[Hashable, _Entry] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > now:
            self.hits += 1
            return entry.value

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                self.hits += 1
                return entry.value

            self.misses += 1
            value = await factory()
            self._store(key, value)
            return value

    def _store(self, key: Hashable, value: Any) -> None:
        if len(self._entries) >= self.max_entries:
            self._evict_expired()
        self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + self.ttl_s)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
            self._entries.pop(key, None)
        if len(self._entries) >= self.max_entries:
            # Still full: drop the oldest key (dicts preserve insertion order).
            oldest = next(iter(self._entries), None)
            if oldest is not None:
                self._entries.pop(oldest, None)

    def clear(self) -> None:
        self._entries.clear()

    @property
    def size(self) -> int:
        return len(self._entries)
