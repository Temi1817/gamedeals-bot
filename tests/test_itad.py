"""Парсинг ответов IsThereAnyDeal."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
import respx

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.itad import (
    BASE_URL,
    MIN_REVIEWS,
    MIN_SCORE,
    ItadClient,
    _deal_filter,
)

SEARCH_URL = f"{BASE_URL}/games/search/v1"
LOOKUP_URL = f"{BASE_URL}/games/lookup/v1"
PRICES_URL = f"{BASE_URL}/games/prices/v3"
HISTORYLOW_URL = f"{BASE_URL}/games/historylow/v1"
DEALS_URL = f"{BASE_URL}/deals/v2"
SHOPS_URL = f"{BASE_URL}/service/shops/v1"

GAME_ID = "018d937f-2997-7131-b8b9-7c8af4825fa8"


@pytest.fixture
def itad(api: ApiClient, cache: TTLCache) -> ItadClient:
    return ItadClient(api, cache, "test-key")


def price(amount: float, currency: str = "USD") -> dict[str, object]:
    return {
        "amount": amount,
        "amountInt": round(amount * 100),
        "currency": currency,
    }


SEARCH_RESPONSE = [
    {
        "id": GAME_ID,
        "slug": "cyberpunk-2077",
        "title": "Cyberpunk 2077",
        "type": "game",
        "mature": False,
        "assets": {
            "banner145": "https://assets.example/b145.jpg",
            "banner600": "https://assets.example/b600.jpg",
            "boxart": "https://assets.example/box.jpg",
        },
    },
    {
        "id": "018d937f-6ed7-7164-8fc3-5390f976e531",
        "slug": "cyberpunk-2077-phantom-liberty",
        "title": "Cyberpunk 2077: Phantom Liberty",
        "type": "dlc",
        "assets": {},
    },
]

PRICES_RESPONSE = [
    {
        "id": GAME_ID,
        "historyLow": {"all": price(17.99)},
        "deals": [
            {
                "shop": {"id": 37, "name": "Humble Store"},
                "price": price(59.99),
                "regular": price(59.99),
                "cut": 0,
                "url": "https://itad.link/humble/",
            },
            {
                "shop": {"id": 35, "name": "GOG"},
                "price": price(17.99),
                "regular": price(59.99),
                "cut": 70,
                "url": "https://itad.link/gog/",
            },
        ],
    }
]


@respx.mock
async def test_search_parses_games(itad: ItadClient) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE)
    )

    games = await itad.search("Cyberpunk 2077")

    assert [g.title for g in games] == [
        "Cyberpunk 2077",
        "Cyberpunk 2077: Phantom Liberty",
    ]
    assert games[0].itad_id == GAME_ID
    assert games[0].slug == "cyberpunk-2077"
    # обложку берём самую крупную
    assert games[0].image_url == "https://assets.example/b600.jpg"


@respx.mock
async def test_search_sends_api_key(itad: ItadClient) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=[]))

    await itad.search("x")

    assert route.calls.last.request.url.params["key"] == "test-key"


@respx.mock
async def test_lookup_by_appid(itad: ItadClient) -> None:
    respx.get(LOOKUP_URL).mock(
        return_value=httpx.Response(
            200, json={"found": True, "game": SEARCH_RESPONSE[0]}
        )
    )

    game = await itad.lookup_by_appid(1091500)

    assert game is not None
    assert game.itad_id == GAME_ID


@respx.mock
async def test_lookup_not_found(itad: ItadClient) -> None:
    respx.get(LOOKUP_URL).mock(
        return_value=httpx.Response(200, json={"found": False, "game": None})
    )

    assert await itad.lookup_by_appid(1) is None


@respx.mock
async def test_prices_sorted_and_parsed(itad: ItadClient) -> None:
    respx.post(PRICES_URL).mock(
        return_value=httpx.Response(200, json=PRICES_RESPONSE)
    )

    offers = (await itad.prices([GAME_ID], country="KZ"))[GAME_ID]

    assert [o.shop.name for o in offers] == ["GOG", "Humble Store"]
    assert offers[0].price == Decimal("17.99")
    assert offers[0].regular_price == Decimal("59.99")
    assert offers[0].cut == 70
    assert offers[0].url == "https://itad.link/gog/"
    # без скидки старую цену не показываем
    assert offers[1].regular_price is None


@respx.mock
async def test_prices_send_bare_array_body(itad: ItadClient) -> None:
    """Тело — голый массив ID; {"ids": [...]} даёт 400."""
    route = respx.post(PRICES_URL).mock(return_value=httpx.Response(200, json=[]))

    await itad.prices([GAME_ID], country="KZ")

    request = route.calls.last.request
    assert json.loads(request.content) == [GAME_ID]
    assert request.url.params["country"] == "KZ"
    # deals=true оставил бы только магазины со скидкой — для карточки не годится
    assert request.url.params["deals"] == "false"


@respx.mock
async def test_prices_empty_input_skips_request(itad: ItadClient) -> None:
    route = respx.post(PRICES_URL).mock(return_value=httpx.Response(200, json=[]))

    assert await itad.prices([]) == {}
    assert not route.called


@respx.mock
async def test_price_falls_back_to_amount_int(itad: ItadClient) -> None:
    """Если amount не пришёл, считаем из amountInt (центы)."""
    respx.post(PRICES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": GAME_ID,
                    "deals": [
                        {
                            "shop": {"id": 35, "name": "GOG"},
                            "price": {"amountInt": 1799, "currency": "USD"},
                            "cut": 70,
                        }
                    ],
                }
            ],
        )
    )

    offers = (await itad.prices([GAME_ID]))[GAME_ID]

    assert offers[0].price == Decimal("17.99")


@respx.mock
async def test_history_low(itad: ItadClient) -> None:
    respx.post(HISTORYLOW_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": GAME_ID,
                    "low": {
                        "shop": {"id": 35, "name": "GOG"},
                        "price": price(17.99),
                        "regular": price(59.99),
                        "cut": 70,
                        "timestamp": "2026-06-17T14:44:42+02:00",
                    },
                }
            ],
        )
    )

    lows = await itad.history_lows([GAME_ID], country="KZ")

    low = lows[GAME_ID]
    assert low.price == Decimal("17.99")
    assert low.currency == "USD"
    assert low.shop == "GOG"
    assert low.at is not None
    assert low.at.date() == datetime(2026, 6, 17).date()


@respx.mock
async def test_history_low_missing_for_new_game(itad: ItadClient) -> None:
    respx.post(HISTORYLOW_URL).mock(
        return_value=httpx.Response(200, json=[{"id": GAME_ID, "low": None}])
    )

    assert await itad.history_lows([GAME_ID]) == {}


@respx.mock
async def test_deals_pagination(itad: ItadClient) -> None:
    respx.get(DEALS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "nextOffset": 5,
                "hasMore": True,
                "list": [
                    {
                        "id": GAME_ID,
                        "slug": "cyberpunk-2077",
                        "title": "Cyberpunk 2077",
                        "assets": {},
                        "deal": {
                            "shop": {"id": 35, "name": "GOG"},
                            "price": price(17.99),
                            "regular": price(59.99),
                            "cut": 70,
                            "url": "https://itad.link/gog/",
                        },
                    }
                ],
            },
        )
    )

    deals, next_offset = await itad.deals("KZ", limit=5)

    assert len(deals) == 1
    assert deals[0].game.title == "Cyberpunk 2077"
    assert deals[0].offer.cut == 70
    assert next_offset == 5


@respx.mock
async def test_deals_last_page_has_no_next(itad: ItadClient) -> None:
    respx.get(DEALS_URL).mock(
        return_value=httpx.Response(
            200, json={"nextOffset": 40, "hasMore": False, "list": []}
        )
    )

    _, next_offset = await itad.deals("KZ")

    assert next_offset is None


@respx.mock
async def test_deals_send_filter_json(itad: ItadClient) -> None:
    """У /deals/v2 нет minCut/maxPrice — фильтры идут одним полем filter."""
    route = respx.get(DEALS_URL).mock(
        return_value=httpx.Response(200, json={"list": [], "hasMore": False})
    )

    await itad.deals("KZ", min_cut=75, max_price=Decimal("10"))

    sent = json.loads(route.calls.last.request.url.params["filter"])
    assert sent["cut"] == {"min": 75, "max": None}
    assert sent["price"] == {"min": None, "max": 10}


@respx.mock
async def test_deals_sort_defaults_to_trending(itad: ItadClient) -> None:
    """При sort=-cut наверху всегда ~100%, и кнопки порога скидки
    визуально ничего не меняли."""
    route = respx.get(DEALS_URL).mock(
        return_value=httpx.Response(200, json={"list": [], "hasMore": False})
    )

    await itad.deals("KZ")

    assert route.calls.last.request.url.params["sort"] == "-trending"


@respx.mock
async def test_deals_send_shop_ids(itad: ItadClient) -> None:
    route = respx.get(DEALS_URL).mock(
        return_value=httpx.Response(200, json={"list": [], "hasMore": False})
    )

    await itad.deals("KZ", shops=[61, 35])

    assert route.calls.last.request.url.params["shops"] == "61,35"


@respx.mock
async def test_shops_directory(itad: ItadClient) -> None:
    respx.get(SHOPS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 61, "title": "Steam", "deals": 100, "games": 1000},
                {"id": 35, "title": "GOG", "deals": 50, "games": 500},
            ],
        )
    )

    shops = await itad.shops("KZ")

    assert shops["61"].name == "Steam"
    assert shops["35"].name == "GOG"


class TestDealFilter:
    """Диапазоны у ITAD требуют обоих концов — отсутствующий край это null."""

    def test_only_cut(self) -> None:
        assert _deal_filter(75, None, games_only=False, quality=False) == {
            "cut": {"min": 75, "max": None}
        }

    def test_only_price(self) -> None:
        assert _deal_filter(0, Decimal("10.50"), games_only=False, quality=False) == {
            "price": {"min": None, "max": 10.5}
        }

    def test_games_only_excludes_dlc(self) -> None:
        assert _deal_filter(0, None, games_only=True, quality=False) == {"type": [1]}

    def test_empty(self) -> None:
        assert _deal_filter(0, None, games_only=False, quality=False) == {}

    def test_quality_floor_filters_shovelware(self) -> None:
        """Без порога выдача забита ассет-флипами с одинаковым ценником."""
        filters = _deal_filter(0, None, games_only=False, quality=True)

        assert filters["steamCount"] == {"min": MIN_REVIEWS, "max": None}
        assert filters["steamPerc"] == {"min": MIN_SCORE, "max": 100}

    def test_quality_is_on_by_default(self) -> None:
        assert "steamCount" in _deal_filter(0, None, games_only=True)
