"""TTL-кэш."""

from __future__ import annotations

import asyncio

import pytest

from bot.services.cache import TTLCache


def test_set_and_get(cache: TTLCache) -> None:
    cache.set("k", 42, ttl=60)

    assert cache.get("k") == 42


def test_missing_key(cache: TTLCache) -> None:
    assert cache.get("нет такого") is None


def test_expired_value_is_dropped(cache: TTLCache) -> None:
    cache.set("k", 42, ttl=-1)  # уже протухло

    assert cache.get("k") is None


def test_invalidate(cache: TTLCache) -> None:
    cache.set("k", 42, ttl=60)
    cache.invalidate("k")

    assert cache.get("k") is None


def test_stats_count_hits_and_misses(cache: TTLCache) -> None:
    cache.set("k", 1, ttl=60)
    cache.get("k")
    cache.get("k")
    cache.get("нет")

    stats = cache.stats
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.667, abs=0.001)


def test_eviction_prefers_expired(cache: TTLCache) -> None:
    cache.max_size = 2
    cache.set("протухший", 1, ttl=-1)
    cache.set("живой", 2, ttl=60)

    cache.set("новый", 3, ttl=60)

    assert cache.get("живой") == 2
    assert cache.get("новый") == 3


def test_eviction_when_nothing_expired(cache: TTLCache) -> None:
    cache.max_size = 2
    cache.set("старый", 1, ttl=10)
    cache.set("средний", 2, ttl=60)

    cache.set("новый", 3, ttl=60)

    assert cache.get("старый") is None
    assert cache.get("новый") == 3


async def test_get_or_set_calls_factory_once(cache: TTLCache) -> None:
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "значение"

    assert await cache.get_or_set("k", 60, factory) == "значение"
    assert await cache.get_or_set("k", 60, factory) == "значение"
    assert calls == 1


async def test_parallel_requests_share_one_fetch(cache: TTLCache) -> None:
    """Пять одновременных запросов одного ключа — один поход в API."""
    calls = 0

    async def slow_factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "значение"

    results = await asyncio.gather(
        *(cache.get_or_set("k", 60, slow_factory) for _ in range(5))
    )

    assert results == ["значение"] * 5
    assert calls == 1


async def test_different_keys_do_not_block_each_other(cache: TTLCache) -> None:
    async def factory(value: str) -> str:
        await asyncio.sleep(0.01)
        return value

    results = await asyncio.gather(
        cache.get_or_set("a", 60, lambda: factory("a")),
        cache.get_or_set("b", 60, lambda: factory("b")),
    )

    assert results == ["a", "b"]


def test_clear(cache: TTLCache) -> None:
    cache.set("a", 1, ttl=60)
    cache.set("b", 2, ttl=60)

    cache.clear()

    assert cache.stats["size"] == 0
