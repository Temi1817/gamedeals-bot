"""Курсы валют и конвертация."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.rates import RATES_URL, RatesClient

RATES = {
    "result": "success",
    "base_code": "USD",
    "time_last_update_utc": "Wed, 26 Aug 2026 00:02:31 +0000",
    "rates": {"USD": 1, "KZT": 457.551263, "EUR": 0.856908, "PLN": 3.687542},
}


@pytest.fixture
def rates(api: ApiClient, cache: TTLCache) -> RatesClient:
    return RatesClient(api, cache)


@respx.mock
async def test_usd_to_kzt(rates: RatesClient) -> None:
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    result = await rates.convert(Decimal("17.99"), "USD", "KZT")

    assert result == Decimal("8231.35")


@respx.mock
async def test_kzt_to_usd(rates: RatesClient) -> None:
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    result = await rates.convert(Decimal("5000"), "KZT", "USD")

    assert result == Decimal("10.93")


@respx.mock
async def test_cross_rate_without_usd(rates: RatesClient) -> None:
    """EUR→PLN считается через доллар, база в ответе именно USD."""
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    result = await rates.convert(Decimal("10"), "EUR", "PLN")

    assert result == Decimal("43.03")


async def test_same_currency_skips_request(rates: RatesClient) -> None:
    """Одинаковые валюты не должны дёргать сеть вообще."""
    with respx.mock:
        route = respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

        result = await rates.convert(Decimal("100"), "KZT", "KZT")

        assert result == Decimal("100")
        assert not route.called


@respx.mock
async def test_case_insensitive(rates: RatesClient) -> None:
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    assert await rates.convert(Decimal("1"), "usd", "kzt") == Decimal("457.55")


@respx.mock
async def test_unknown_currency_returns_none(rates: RatesClient) -> None:
    """Курса нет — карточка просто покажет исходную валюту без ≈."""
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    assert await rates.convert(Decimal("10"), "USD", "XYZ") is None


@respx.mock
async def test_failed_response_returns_none(rates: RatesClient) -> None:
    respx.get(RATES_URL).mock(return_value=httpx.Response(200, json={"result": "error"}))

    assert await rates.convert(Decimal("10"), "USD", "KZT") is None


@respx.mock
async def test_network_failure_does_not_raise(
    rates: RatesClient, no_sleep: list[float]
) -> None:
    """Недоступный курс не должен ронять выдачу карточки."""
    respx.get(RATES_URL).mock(return_value=httpx.Response(503))

    assert await rates.convert(Decimal("10"), "USD", "KZT") is None


@respx.mock
async def test_rates_are_cached(rates: RatesClient) -> None:
    route = respx.get(RATES_URL).mock(return_value=httpx.Response(200, json=RATES))

    await rates.convert(Decimal("1"), "USD", "KZT")
    await rates.convert(Decimal("2"), "USD", "EUR")

    assert route.call_count == 1


@respx.mock
async def test_broken_rate_values_are_skipped(rates: RatesClient) -> None:
    respx.get(RATES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "rates": {"KZT": "не число", "EUR": 0.85},
            },
        )
    )

    assert await rates.convert(Decimal("10"), "USD", "KZT") is None
    assert await rates.convert(Decimal("10"), "USD", "EUR") == Decimal("8.50")
