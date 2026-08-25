"""Парсинг ответов CheapShark."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import respx

from bot.services.cheapshark import BASE_URL, CheapSharkClient
from tests.conftest import CHEAPSHARK_GAME, CHEAPSHARK_SEARCH, CHEAPSHARK_STORES

GAMES_URL = f"{BASE_URL}/games"
STORES_URL = f"{BASE_URL}/stores"
DEALS_URL = f"{BASE_URL}/deals"


def mock_stores() -> respx.Route:
    return respx.get(STORES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_STORES)
    )


@respx.mock
async def test_search_parses_games(cheapshark: CheapSharkClient) -> None:
    respx.get(GAMES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_SEARCH)
    )

    games = await cheapshark.search("Cyberpunk")

    assert [g.title for g in games] == [
        "Cyberpunk 2077",
        "Cyberpunk 2077: Phantom Liberty",
    ]
    assert games[0].cheapshark_id == "202350"
    assert games[0].steam_appid == 1091500
    # у дополнения steamAppID приходит null
    assert games[1].steam_appid is None


@respx.mock
async def test_offers_are_marked_as_reseller_usd(
    cheapshark: CheapSharkClient,
) -> None:
    """Цены CheapShark — доллары и ключи, их нельзя сравнивать с тенге."""
    mock_stores()
    respx.get(GAMES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_GAME)
    )

    _, offers, _ = await cheapshark.offers("202350")

    assert len(offers) == 2
    assert all(o.currency == "USD" for o in offers)
    assert all(o.is_reseller for o in offers)
    assert all(o.approximate for o in offers)


@respx.mock
async def test_offers_sorted_by_price(cheapshark: CheapSharkClient) -> None:
    mock_stores()
    respx.get(GAMES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_GAME)
    )

    _, offers, _ = await cheapshark.offers("202350")

    assert [o.price for o in offers] == [Decimal("29.99"), Decimal("59.99")]
    assert offers[0].shop.name == "Humble Store"
    assert offers[0].cut == 50
    assert offers[0].regular_price == Decimal("59.99")
    assert offers[0].url == "https://www.cheapshark.com/redirect?dealID=nL0Ha1MA6fsR"
    # без скидки старую цену не показываем
    assert offers[1].regular_price is None
    assert offers[1].cut == 0


@respx.mock
async def test_offers_parse_historical_low(cheapshark: CheapSharkClient) -> None:
    mock_stores()
    respx.get(GAMES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_GAME)
    )

    game, _, low = await cheapshark.offers("202350")

    assert game is not None
    assert game.title == "Cyberpunk 2077"
    assert low is not None
    assert low.price == Decimal("17.99")
    assert low.currency == "USD"
    assert low.at == datetime.fromtimestamp(1781701951, tz=UTC)


@respx.mock
async def test_offers_of_unknown_game(cheapshark: CheapSharkClient) -> None:
    respx.get(GAMES_URL).mock(return_value=httpx.Response(200, json={}))

    game, offers, low = await cheapshark.offers("нет такой")

    assert (game, offers, low) == (None, [], None)


@respx.mock
async def test_unknown_store_gets_placeholder(
    cheapshark: CheapSharkClient, no_sleep: list[float]
) -> None:
    """Справочник магазинов не должен ронять разбор предложений."""
    stores = respx.get(STORES_URL).mock(return_value=httpx.Response(500))
    respx.get(GAMES_URL).mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_GAME)
    )

    _, offers, _ = await cheapshark.offers("202350")

    assert len(offers) == 2
    assert offers[0].shop.name == "Магазин 11"
    # справочник тянем один раз на вызов, а не на каждое предложение:
    # 1 попытка + 2 повтора, независимо от числа сделок в ответе
    assert stores.call_count == 3


@respx.mock
async def test_stores_are_cached_between_calls(cheapshark: CheapSharkClient) -> None:
    route = mock_stores()

    await cheapshark.shops()
    await cheapshark.shops()

    assert route.call_count == 1


@respx.mock
async def test_deals_filtered_by_savings(cheapshark: CheapSharkClient) -> None:
    mock_stores()
    respx.get(DEALS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "title": "Дорогая скидка",
                    "gameID": "1",
                    "dealID": "a",
                    "storeID": "1",
                    "salePrice": "9.99",
                    "normalPrice": "19.99",
                    "savings": "50.02",
                    "steamAppID": "111",
                    "thumb": "https://cdn.example/1.jpg",
                },
                {
                    "title": "Слабая скидка",
                    "gameID": "2",
                    "dealID": "b",
                    "storeID": "7",
                    "salePrice": "18.99",
                    "normalPrice": "19.99",
                    "savings": "5.00",
                    "steamAppID": None,
                    "thumb": "https://cdn.example/2.jpg",
                },
            ],
        )
    )

    deals = await cheapshark.deals(upper_price=15, min_savings=50)

    assert [d.game.title for d in deals] == ["Дорогая скидка"]
    assert deals[0].offer.price == Decimal("9.99")
    assert deals[0].offer.cut == 50
    assert deals[0].game.steam_appid == 111


@respx.mock
async def test_deals_skip_items_without_title(cheapshark: CheapSharkClient) -> None:
    mock_stores()
    respx.get(DEALS_URL).mock(
        return_value=httpx.Response(
            200, json=[{"gameID": "1", "salePrice": "1.00", "storeID": "1"}]
        )
    )

    assert await cheapshark.deals() == []
