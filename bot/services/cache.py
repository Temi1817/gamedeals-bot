"""TTL-кэш в памяти.

У ITAD лимит 1000 запросов / 5 минут, у Steam и Epic лимитов формально нет,
но долбить их на каждое нажатие кнопки всё равно нельзя.

Кэш процессный: при перезапуске бота теряется, для пет-проекта это норма.
Понадобится несколько инстансов — здесь же меняется на Redis.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from bot.utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """Асинхронный кэш с временем жизни на ключ.

    Параллельные запросы одного ключа не размножают поход в API: второй
    ждёт на блокировке и забирает уже готовое значение.
    """

    def __init__(self, max_size: int = 2000) -> None:
        self._data: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at <= time.monotonic():
            del self._data[key]
            self.misses += 1
            return None
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._data) >= self.max_size:
            self._evict()
        self._data[key] = _Entry(value, time.monotonic() + ttl)

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def _evict(self) -> None:
        """Чистим протухшее; если всё живо — выкидываем самое старое."""
        now = time.monotonic()
        expired = [k for k, e in self._data.items() if e.expires_at <= now]
        for key in expired:
            del self._data[key]
        if not expired and self._data:
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            del self._data[oldest]

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = asyncio.Lock()
            return lock

    async def get_or_set(
        self, key: str, ttl: float, factory: Callable[[], Awaitable[T]]
    ) -> T:
        """Возвращает значение из кэша либо вычисляет его через `factory`."""
        cached = self.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        lock = await self._lock_for(key)
        async with lock:
            # пока ждали блокировку, значение мог положить кто-то другой
            cached = self.get(key)
            if cached is not None:
                return cached  # type: ignore[no-any-return]

            value = await factory()
            self.set(key, value, ttl)
            return value

    @property
    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "size": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


# Общий кэш процесса — клиенты берут его отсюда.
cache = TTLCache()
