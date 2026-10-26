from __future__ import annotations

import asyncio

import pytest

from app.telemetry.cache import TTLCache


async def test_single_flight_collapses_concurrent_misses() -> None:
    cache = TTLCache(ttl_s=10)
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return calls

    results = await asyncio.gather(*[cache.get("key", factory) for _ in range(5)])

    assert calls == 1
    assert results == [1, 1, 1, 1, 1]
    assert cache.hits == 4


async def test_entries_expire() -> None:
    cache = TTLCache(ttl_s=0.01)
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get("key", factory) == 1
    await asyncio.sleep(0.02)
    assert await cache.get("key", factory) == 2


async def test_factory_errors_are_not_cached() -> None:
    cache = TTLCache(ttl_s=10)
    attempts = 0

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get("key", factory)

    assert await cache.get("key", factory) == "ok"
    assert attempts == 2


async def test_cache_evicts_when_full() -> None:
    cache = TTLCache(ttl_s=10, max_entries=3)

    async def value(number: int):  # type: ignore[no-untyped-def]
        async def factory() -> int:
            return number

        return await cache.get(("k", number), factory)

    for index in range(5):
        await value(index)

    assert cache.size <= 3
