"""Общий HTTP-слой: таймауты, retry с экспоненциальным backoff, Retry-After.

Один `httpx.AsyncClient` на процесс — переиспользование соединений важнее
изоляции, а лимиты у источников разные и держатся на стороне клиентов.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from bot.utils.logging import get_logger

log = get_logger(__name__)

USER_AGENT = "gamedeals-bot/0.1 (+https://github.com/; telegram price tracker)"

# Коды, при которых повтор осмыслен: лимит и временные сбои на той стороне.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_BACKOFF = 30.0


class ApiError(Exception):
    """Источник не смог ответить. Агрегатор ловит и идёт к следующему."""

    def __init__(self, source: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.status = status


def create_client(timeout: float = 10.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


def _retry_after(response: httpx.Response) -> float | None:
    """Пауза из заголовка `Retry-After` (поддерживаем только формат в секундах)."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    """Экспонента с джиттером: 1, 2, 4... плюс случайная добавка.

    Джиттер нужен, чтобы параллельные запросы не били в лимит синхронно.
    """
    return min(MAX_BACKOFF, 2.0**attempt) * (0.5 + random.random() / 2)


class ApiClient:
    """Обёртка над `httpx.AsyncClient` с повторами и разбором JSON."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        source: str,
        max_retries: int = 3,
    ) -> None:
        self._client = client
        self.source = source
        self.max_retries = max_retries

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        last_error: str = "неизвестная ошибка"
        last_status: int | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.TimeoutException:
                last_error = "таймаут"
            except httpx.HTTPError as exc:
                last_error = f"сетевая ошибка: {exc}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return self._parse(response)

                last_status = response.status_code
                last_error = f"HTTP {response.status_code}"
                # сервер сам сказал, сколько ждать — уважаем
                pause = _retry_after(response)
                if attempt < self.max_retries:
                    delay = pause if pause is not None else _backoff(attempt)
                    log.warning(
                        "api_retry",
                        source=self.source,
                        url=url,
                        status=response.status_code,
                        attempt=attempt + 1,
                        delay=round(delay, 2),
                    )
                    await asyncio.sleep(delay)
                continue

            # сюда попадаем только после сетевой ошибки или таймаута
            if attempt < self.max_retries:
                delay = _backoff(attempt)
                log.warning(
                    "api_retry",
                    source=self.source,
                    url=url,
                    error=last_error,
                    attempt=attempt + 1,
                    delay=round(delay, 2),
                )
                await asyncio.sleep(delay)

        raise ApiError(
            self.source, f"{last_error} после {self.max_retries} повторов", last_status
        )

    def _parse(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise ApiError(
                self.source, f"HTTP {response.status_code}", response.status_code
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(self.source, f"ответ не JSON: {exc}") from exc

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request_json("GET", url, params=params, headers=headers)

    async def post_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request_json(
            "POST", url, params=params, json=json, headers=headers
        )
