"""Курсы валют — чтобы показывать «≈ в тенге» рядом с ценой в долларах.

Зачем вообще: у ITAD нет региональных цен для Казахстана, для `country=KZ`
он отдаёт USD (проверено: DE→EUR, PL→PLN, TR→TRY, а KZ, UA и RU→USD).
Настоящие тенге дают только Steam и Epic. Чтобы карточка сортировалась
сквозь весь список, доллары приводим к валюте региона и помечаем знаком ≈.

Источник — open.er-api.com: без ключа, 166 валют одним запросом, обновление
раз в сутки. Альтернатива на будущее — Нацбанк РК
(https://nationalbank.kz/rss/rates_all.xml), но он даёт только пары к тенге.

Пересчёт всегда приблизительный: магазин спишет доллары, а не тенге.
Если курс недоступен — возвращаем None, и карточка просто покажет цену
в исходной валюте без «≈». Ронять выдачу из-за курса нельзя.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.utils.logging import get_logger

log = get_logger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/USD"
BASE = "USD"
CACHE_KEY = "rates:usd"

# Курс меняется раз в сутки — чаще ходить незачем
RATES_TTL = 24 * 60 * 60.0


class RatesClient:
    """Конвертер валют с базой в USD."""

    def __init__(
        self, api: ApiClient, cache: TTLCache, ttl: float = RATES_TTL
    ) -> None:
        self.api = api
        self.cache = cache
        self.ttl = ttl

    async def rates(self) -> dict[str, Decimal]:
        """Курсы к доллару: `{"KZT": Decimal("457.55"), ...}`."""

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(RATES_URL)
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(CACHE_KEY, self.ttl, fetch)
        if raw.get("result") != "success":
            return {}

        rates: dict[str, Decimal] = {}
        for code, value in (raw.get("rates") or {}).items():
            try:
                rates[str(code).upper()] = Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
        return rates

    async def convert(
        self, amount: Decimal, source: str, target: str
    ) -> Decimal | None:
        """Переводит сумму между валютами. `None`, если курса нет."""
        source, target = source.upper(), target.upper()
        if source == target:
            return amount

        try:
            rates = await self.rates()
        except Exception as exc:
            # курс — украшение, а не обязательная часть ответа
            log.warning("rates_unavailable", error=str(exc))
            return None

        # база USD: сначала приводим к доллару, потом к целевой валюте
        rate_from = Decimal(1) if source == BASE else rates.get(source)
        rate_to = Decimal(1) if target == BASE else rates.get(target)
        if not rate_from or not rate_to or rate_from <= 0:
            return None

        return (amount / rate_from * rate_to).quantize(Decimal("0.01"))
