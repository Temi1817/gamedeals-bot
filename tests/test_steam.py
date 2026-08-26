"""Парсинг ответов Steam Store."""

from __future__ import annotations

from decimal import Decimal

import httpx
import respx

from bot.services.steam import SteamClient
from tests.conftest import STEAM_PRICES, STEAM_SEARCH

SEARCH_HOST = "https://steamcommunity.com/actions/SearchApps/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"


@respx.mock
async def test_search_parses_games(steam: SteamClient) -> None:
    respx.get(url__startswith=SEARCH_HOST).mock(
        return_value=httpx.Response(200, json=STEAM_SEARCH)
    )

    games = await steam.search("Cyberpunk 2077")

    assert len(games) == 1
    assert games[0].title == "Cyberpunk 2077"
    assert games[0].steam_appid == 1091500
    assert games[0].image_url == "https://cdn.example/capsule_184x69.jpg"


@respx.mock
async def test_search_skips_broken_items(steam: SteamClient) -> None:
    respx.get(url__startswith=SEARCH_HOST).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"appid": "не число", "name": "Битая"},
                {"appid": "1", "name": None},
                {"name": "Без appid"},
                {"appid": "42", "name": "Нормальная"},
            ],
        )
    )

    games = await steam.search("x")

    assert [g.title for g in games] == ["Нормальная"]


@respx.mock
async def test_search_respects_limit(steam: SteamClient) -> None:
    respx.get(url__startswith=SEARCH_HOST).mock(
        return_value=httpx.Response(
            200,
            json=[{"appid": str(i), "name": f"Игра {i}"} for i in range(10)],
        )
    )

    assert len(await steam.search("x", limit=3)) == 3


@respx.mock
async def test_prices_converts_minor_units(steam: SteamClient) -> None:
    respx.get(DETAILS_URL).mock(return_value=httpx.Response(200, json=STEAM_PRICES))

    offers = await steam.prices([1091500, 377160], country="KZ")

    # 1799900 минорных единиц == 17 999,00 ₸
    assert offers[1091500].price == Decimal("17999.00")
    assert offers[1091500].currency == "KZT"
    assert offers[1091500].url == "https://store.steampowered.com/app/1091500/"


@respx.mock
async def test_prices_hides_regular_price_without_discount(steam: SteamClient) -> None:
    """При нулевой скидке initial == final — старую цену показывать незачем."""
    respx.get(DETAILS_URL).mock(return_value=httpx.Response(200, json=STEAM_PRICES))

    offers = await steam.prices([1091500, 377160])

    assert offers[1091500].cut == 0
    assert offers[1091500].regular_price is None
    assert offers[1091500].savings is None


@respx.mock
async def test_prices_keeps_regular_price_on_discount(steam: SteamClient) -> None:
    respx.get(DETAILS_URL).mock(return_value=httpx.Response(200, json=STEAM_PRICES))

    offer = (await steam.prices([1091500, 377160]))[377160]

    assert offer.price == Decimal("3500.00")
    assert offer.regular_price == Decimal("7000.00")
    assert offer.cut == 50
    assert offer.savings == Decimal("3500.00")


@respx.mock
async def test_prices_skips_unsuccessful_and_free(steam: SteamClient) -> None:
    respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "1": {"success": False},
                # у части игр data приходит пустым списком
                "2": {"success": True, "data": []},
                # free-to-play: блока price_overview нет
                "3": {"success": True, "data": {"name": "Dota 2", "is_free": True}},
                "4": {
                    "success": True,
                    "data": {
                        "price_overview": {
                            "currency": "KZT",
                            "initial": 100000,
                            "final": 100000,
                            "discount_percent": 0,
                        }
                    },
                },
            },
        )
    )

    offers = await steam.prices([1, 2, 3, 4])

    assert set(offers) == {4}


@respx.mock
async def test_prices_empty_input_skips_request(steam: SteamClient) -> None:
    route = respx.get(DETAILS_URL).mock(return_value=httpx.Response(200, json={}))

    assert await steam.prices([]) == {}
    assert not route.called


@respx.mock
async def test_prices_survive_failed_batch(
    steam: SteamClient, no_sleep: list[float]
) -> None:
    """Упавшая порция не должна обнулять уже полученные цены."""
    from bot.services import steam as steam_module

    original_batch = steam_module.BATCH_SIZE
    steam_module.BATCH_SIZE = 1
    try:
        route = respx.get(DETAILS_URL)
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(
                200,
                json={
                    "377160": STEAM_PRICES["377160"],
                },
            ),
        ]

        offers = await steam.prices([1091500, 377160])
    finally:
        steam_module.BATCH_SIZE = original_batch

    assert set(offers) == {377160}


@respx.mock
async def test_details_returns_title_and_image(steam: SteamClient) -> None:
    respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "1091500": {
                    "success": True,
                    "data": {
                        "name": "Cyberpunk 2077",
                        "header_image": "https://cdn.example/header.jpg",
                        "capsule_image": "https://cdn.example/capsule.jpg",
                    },
                }
            },
        )
    )

    game = await steam.details(1091500)

    assert game is not None
    assert game.title == "Cyberpunk 2077"
    assert game.image_url == "https://cdn.example/header.jpg"


@respx.mock
async def test_details_missing_game(steam: SteamClient) -> None:
    respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(200, json={"999": {"success": False}})
    )

    assert await steam.details(999) is None


@respx.mock
async def test_search_result_is_cached(steam: SteamClient) -> None:
    route = respx.get(url__startswith=SEARCH_HOST).mock(
        return_value=httpx.Response(200, json=STEAM_SEARCH)
    )

    await steam.search("Cyberpunk 2077")
    await steam.search("cyberpunk 2077")  # регистр не должен рождать новый запрос

    assert route.call_count == 1


# --------------------------------------------------------------------------- #
# топ продаж
# --------------------------------------------------------------------------- #
FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"


def featured_item(
    appid: int,
    name: str,
    final: int,
    original: int | None,
    cut: int = 0,
) -> dict[str, object]:
    return {
        "id": appid,
        "name": name,
        "discount_percent": cut,
        "original_price": original,
        "final_price": final,
        "currency": "KZT",
        "large_capsule_image": "https://cdn.example/capsule.jpg",
    }


def mock_featured(items: list[dict[str, object]]) -> respx.Route:
    return respx.get(FEATURED_URL).mock(
        return_value=httpx.Response(
            200, json={"top_sellers": {"id": "cat", "name": "Top", "items": items}}
        )
    )


@respx.mock
async def test_top_sellers_parsed(steam: SteamClient) -> None:
    mock_featured(
        [featured_item(3321460, "Crimson Desert", 2239900, 2799900, cut=20)]
    )

    top = await steam.top_sellers(country="KZ")

    assert len(top) == 1
    game, offer = top[0]
    assert game.title == "Crimson Desert"
    assert game.steam_appid == 3321460
    assert offer.price == Decimal("22399.00")
    assert offer.regular_price == Decimal("27999.00")
    assert offer.cut == 20
    assert offer.currency == "KZT"


@respx.mock
async def test_top_sellers_drops_hardware(steam: SteamClient) -> None:
    """В топе попадаются железки и промо без цены вовсе."""
    mock_featured(
        [
            featured_item(4165910, "Steam Machine", 0, None),
            featured_item(1091500, "Cyberpunk 2077", 1799900, None),
        ]
    )

    top = await steam.top_sellers(country="KZ")

    assert [g.title for g, _ in top] == ["Cyberpunk 2077"]


@respx.mock
async def test_top_sellers_dedupes(steam: SteamClient) -> None:
    """Одна и та же позиция приходит по нескольку раз."""
    mock_featured(
        [
            featured_item(4165910, "Игра", 100000, None),
            featured_item(4165910, "Игра", 100000, None),
            featured_item(1091500, "Другая", 200000, None),
        ]
    )

    top = await steam.top_sellers(country="KZ")

    assert [g.steam_appid for g, _ in top] == [4165910, 1091500]


@respx.mock
async def test_top_sellers_respects_limit(steam: SteamClient) -> None:
    mock_featured(
        [featured_item(i, f"Игра {i}", 100000, None) for i in range(1, 9)]
    )

    assert len(await steam.top_sellers(country="KZ", limit=3)) == 3


@respx.mock
async def test_top_sellers_empty_block(steam: SteamClient) -> None:
    respx.get(FEATURED_URL).mock(return_value=httpx.Response(200, json={}))

    assert await steam.top_sellers(country="KZ") == []
