"""Общие фикстуры тестов."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest

from bot.services.cache import TTLCache
from bot.services.cheapshark import CheapSharkClient
from bot.services.epic import EpicClient
from bot.services.http import ApiClient
from bot.services.steam import SteamClient


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        yield client


@pytest.fixture
def cache() -> TTLCache:
    return TTLCache()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Убирает реальные паузы между повторами, но записывает их длительность."""
    import asyncio

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    yield delays


@pytest.fixture
def api(http_client: httpx.AsyncClient) -> ApiClient:
    return ApiClient(http_client, source="test", max_retries=2)


@pytest.fixture
def steam(api: ApiClient, cache: TTLCache) -> SteamClient:
    return SteamClient(api, cache)


@pytest.fixture
def epic(api: ApiClient, cache: TTLCache) -> EpicClient:
    return EpicClient(api, cache)


@pytest.fixture
def cheapshark(api: ApiClient, cache: TTLCache) -> CheapSharkClient:
    return CheapSharkClient(api, cache)


# --------------------------------------------------------------------------- #
# Слепки реальных ответов, снятые scripts/probe_apis.py
# --------------------------------------------------------------------------- #
STEAM_SEARCH: list[dict[str, Any]] = [
    {
        "appid": "1091500",
        "name": "Cyberpunk 2077",
        "icon": "https://cdn.example/icon.jpg",
        "logo": "https://cdn.example/capsule_184x69.jpg",
    }
]

STEAM_PRICES: dict[str, Any] = {
    "1091500": {
        "success": True,
        "data": {
            "price_overview": {
                "currency": "KZT",
                "initial": 1799900,
                "final": 1799900,
                "discount_percent": 0,
                "initial_formatted": "",
                "final_formatted": "17 999₸",
            }
        },
    },
    "377160": {
        "success": True,
        "data": {
            "price_overview": {
                "currency": "KZT",
                "initial": 700000,
                "final": 350000,
                "discount_percent": 50,
                "initial_formatted": "7 000₸",
                "final_formatted": "3 500₸",
            }
        },
    },
}

CHEAPSHARK_SEARCH: list[dict[str, Any]] = [
    {
        "gameID": "202350",
        "steamAppID": "1091500",
        "cheapest": "59.99",
        "cheapestDealID": "ezTZN4Wxuyjk",
        "external": "Cyberpunk 2077",
        "internalName": "CYBERPUNK2077",
        "thumb": "https://cdn.example/capsule_231x87.jpg",
    },
    {
        "gameID": "264414",
        "steamAppID": None,
        "cheapest": "29.99",
        "cheapestDealID": "uram8xpV1u3H",
        "external": "Cyberpunk 2077: Phantom Liberty",
        "internalName": "CYBERPUNK2077PHANTOMLIBERTY",
        "thumb": "https://cdn.example/pl.webp",
    },
]

CHEAPSHARK_GAME: dict[str, Any] = {
    "info": {
        "title": "Cyberpunk 2077",
        "steamAppID": "1091500",
        "thumb": "https://cdn.example/capsule_231x87.jpg",
    },
    "cheapestPriceEver": {"price": "17.99", "date": 1781701951},
    "deals": [
        {
            "storeID": "11",
            "dealID": "nL0Ha1MA6fsR",
            "price": "29.99",
            "retailPrice": "59.99",
            "savings": "50.008335",
        },
        {
            "storeID": "1",
            "dealID": "ezTZN4Wxuyjk",
            "price": "59.99",
            "retailPrice": "59.99",
            "savings": "0.000000",
        },
    ],
}

CHEAPSHARK_STORES: list[dict[str, Any]] = [
    {"storeID": "1", "storeName": "Steam", "isActive": 1},
    {"storeID": "7", "storeName": "GOG", "isActive": 1},
    {"storeID": "11", "storeName": "Humble Store", "isActive": 1},
]


def epic_element(
    title: str,
    *,
    discount_price: int,
    original_price: int = 754000,
    current: list[dict[str, Any]] | None = None,
    upcoming: list[dict[str, Any]] | None = None,
    product_slug: str | None = None,
    page_slug: str | None = None,
) -> dict[str, Any]:
    """Собирает элемент каталога Epic в том виде, в каком его отдаёт API."""
    return {
        "title": title,
        "description": f"Описание {title}",
        "productSlug": product_slug,
        "urlSlug": "some-url-slug",
        "offerMappings": (
            [{"pageSlug": page_slug, "pageType": "productHome"}] if page_slug else []
        ),
        "keyImages": [
            {"type": "OfferImageTall", "url": "https://cdn.example/tall.png"},
            {"type": "OfferImageWide", "url": "https://cdn.example/wide.png"},
        ],
        "price": {
            "totalPrice": {
                "discountPrice": discount_price,
                "originalPrice": original_price,
                "currencyCode": "KZT",
            }
        },
        "promotions": {
            "promotionalOffers": (
                [{"promotionalOffers": current}] if current else []
            ),
            "upcomingPromotionalOffers": (
                [{"promotionalOffers": upcoming}] if upcoming else []
            ),
        },
    }


def window(start: str, end: str, percent: int) -> dict[str, Any]:
    return {
        "startDate": start,
        "endDate": end,
        "discountSetting": {
            "discountType": "PERCENTAGE",
            "discountPercentage": percent,
        },
    }


def epic_payload(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"Catalog": {"searchStore": {"elements": elements}}}}
