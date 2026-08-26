"""Разбор раздач Epic Games."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import respx

from bot.services.epic import PROMOTIONS_URL, STOREFRONT_URL, EpicClient
from tests.conftest import epic_element, epic_payload, window

ACTIVE = window("2026-08-20T15:00:00.000Z", "2026-08-27T15:00:00.000Z", 0)
NEXT_WEEK = window("2026-08-27T15:00:00.000Z", "2026-09-03T15:00:00.000Z", 0)
SALE = window("2026-09-03T15:00:00.000Z", "2026-09-17T15:00:00.000Z", 25)


def mock_epic(elements: list[dict[str, object]]) -> respx.Route:
    return respx.get(PROMOTIONS_URL).mock(
        return_value=httpx.Response(200, json=epic_payload(elements))
    )


@respx.mock
async def test_active_giveaway_is_parsed(epic: EpicClient) -> None:
    mock_epic(
        [
            epic_element(
                "Cardpocalypse",
                discount_price=0,
                original_price=754000,
                current=[ACTIVE],
                page_slug="cardpocalypse",
            )
        ]
    )

    games = await epic.free_games()

    assert len(games) == 1
    game = games[0]
    assert game.title == "Cardpocalypse"
    assert game.upcoming is False
    assert game.currency == "KZT"
    # 754000 минорных единиц == 7 540 ₸
    assert game.original_price == Decimal("7540.00")
    assert game.url == "https://store.epicgames.com/ru/p/cardpocalypse"
    assert game.image_url == "https://cdn.example/wide.png"
    assert game.ends_at == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


@respx.mock
async def test_upcoming_giveaway_is_marked(epic: EpicClient) -> None:
    mock_epic(
        [
            epic_element(
                "Breathedge",
                discount_price=690000,
                current=None,
                upcoming=[NEXT_WEEK, SALE],
                page_slug="breathedge",
            )
        ]
    )

    games = await epic.free_games()

    assert len(games) == 1
    assert games[0].upcoming is True
    assert games[0].starts_at == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


@respx.mock
async def test_plain_sale_is_not_a_giveaway(epic: EpicClient) -> None:
    """discountPercentage=25 — это распродажа, а не раздача."""
    mock_epic(
        [
            epic_element(
                "Ghostrunner 2",
                discount_price=1599900,
                current=None,
                upcoming=[SALE],
                page_slug="ghostrunner-2",
            )
        ]
    )

    assert await epic.free_games() == []


@respx.mock
async def test_game_without_promotions_is_skipped(epic: EpicClient) -> None:
    mock_epic(
        [
            epic_element(
                "Eternal Threads",
                discount_price=370000,
                page_slug="eternal-threads",
            )
        ]
    )

    assert await epic.free_games() == []


@respx.mock
async def test_upcoming_can_be_excluded(epic: EpicClient) -> None:
    mock_epic(
        [
            epic_element("Сейчас", discount_price=0, current=[ACTIVE], page_slug="now"),
            epic_element(
                "Потом", discount_price=690000, upcoming=[NEXT_WEEK], page_slug="later"
            ),
        ]
    )

    active_only = await epic.free_games(include_upcoming=False)

    assert [g.title for g in active_only] == ["Сейчас"]


@respx.mock
async def test_active_giveaways_come_first(epic: EpicClient) -> None:
    mock_epic(
        [
            epic_element(
                "Потом", discount_price=690000, upcoming=[NEXT_WEEK], page_slug="later"
            ),
            epic_element("Сейчас", discount_price=0, current=[ACTIVE], page_slug="now"),
        ]
    )

    games = await epic.free_games()

    assert [g.title for g in games] == ["Сейчас", "Потом"]


@respx.mock
async def test_slug_falls_back_to_product_slug(epic: EpicClient) -> None:
    """productSlug приходит с хвостом /home — его нужно срезать."""
    mock_epic(
        [
            epic_element(
                "Them's Fightin' Herds",
                discount_price=0,
                current=[ACTIVE],
                product_slug="thems-fightin-herds/home",
                page_slug=None,
            )
        ]
    )

    games = await epic.free_games()

    assert games[0].url == (
        "https://store.epicgames.com/ru/p/thems-fightin-herds"
    )


@respx.mock
async def test_element_without_slug_has_no_url(epic: EpicClient) -> None:
    element = epic_element("Безымянная", discount_price=0, current=[ACTIVE])
    element["urlSlug"] = None
    mock_epic([element])

    games = await epic.free_games()

    assert games[0].url is None


@respx.mock
async def test_broken_payload_gives_empty_list(epic: EpicClient) -> None:
    respx.get(PROMOTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": {"Catalog": None}})
    )

    assert await epic.free_games() == []


# --------------------------------------------------------------------------- #
# региональные цены витрины
# --------------------------------------------------------------------------- #
def storefront_node(
    title: str,
    original: int,
    discount: int,
    *,
    offer_type: str = "BASE_GAME",
    slug: str = "some-game",
    currency: str = "KZT",
) -> dict[str, object]:
    """Узел витрины в том виде, в каком его отдаёт storefrontLayout."""
    return {
        "title": title,
        "offerType": offer_type,
        "urlSlug": slug,
        "offerMappings": [{"pageSlug": slug, "pageType": "productHome"}],
        "price": {
            "totalPrice": {
                "discountPrice": discount,
                "originalPrice": original,
                "currencyCode": currency,
            }
        },
    }


def storefront(*nodes: dict[str, object]) -> dict[str, object]:
    return {"data": {"Storefront": {"modules": [{"offers": list(nodes)}]}}}


def mock_storefront(nodes: list[dict[str, object]]) -> respx.Route:
    return respx.get(STOREFRONT_URL).mock(
        return_value=httpx.Response(200, json=storefront(*nodes))
    )


class TestRegionalPrices:
    """Витрина — единственный публичный источник цен Epic в валюте региона."""

    @respx.mock
    async def test_parses_kzt_price(self, epic: EpicClient) -> None:
        # снято живым запросом: у ITAD выходило 12 807 ₸ вместо 5 184 ₸
        mock_storefront(
            [storefront_node("HITMAN World of Assassination", 1296000, 518400)]
        )

        offer = await epic.offer_for("HITMAN World of Assassination")

        assert offer is not None
        assert offer.price == Decimal("5184.00")
        assert offer.regular_price == Decimal("12960.00")
        assert offer.currency == "KZT"
        assert offer.cut == 60

    @respx.mock
    async def test_english_locale_is_requested(self, epic: EpicClient) -> None:
        """С русской локалью названия переводятся и не сойдутся с ITAD."""
        route = mock_storefront([])

        await epic.regional_prices(country="KZ")

        params = route.calls.last.request.url.params
        assert params["locale"] == "en-US"
        assert params["country"] == "KZ"

    @respx.mock
    async def test_title_match_is_exact(self, epic: EpicClient) -> None:
        mock_storefront(
            [
                storefront_node("Dead by Daylight", 620000, 248000),
                storefront_node(
                    "Dead by Daylight - Alien Chapter Pack", 370500, 185200,
                    offer_type="ADD_ON",
                ),
            ]
        )

        offer = await epic.offer_for("Dead by Daylight")

        assert offer is not None
        assert offer.price == Decimal("2480.00")

    @respx.mock
    async def test_base_game_wins_over_addon(self, epic: EpicClient) -> None:
        """При совпадении названий базовая игра важнее дополнения."""
        mock_storefront(
            [
                storefront_node("Some Game", 100000, 90000, offer_type="ADD_ON"),
                storefront_node("Some Game", 620000, 248000, offer_type="BASE_GAME"),
            ]
        )

        offer = await epic.offer_for("Some Game")

        assert offer is not None
        assert offer.price == Decimal("2480.00")

    @respx.mock
    async def test_unknown_game_returns_none(self, epic: EpicClient) -> None:
        """Витрина покрывает не весь каталог — тогда останется цена ITAD."""
        mock_storefront([storefront_node("Dead by Daylight", 620000, 248000)])

        assert await epic.offer_for("Игры нет на витрине") is None

    @respx.mock
    async def test_no_discount_hides_old_price(self, epic: EpicClient) -> None:
        mock_storefront([storefront_node("Full Price Game", 500000, 500000)])

        offer = await epic.offer_for("Full Price Game")

        assert offer is not None
        assert offer.regular_price is None
        assert offer.cut == 0

    @respx.mock
    async def test_case_insensitive_match(self, epic: EpicClient) -> None:
        mock_storefront([storefront_node("Dead by Daylight", 620000, 248000)])

        assert await epic.offer_for("DEAD BY DAYLIGHT") is not None

    @respx.mock
    async def test_broken_payload_gives_empty_map(self, epic: EpicClient) -> None:
        respx.get(STOREFRONT_URL).mock(
            return_value=httpx.Response(200, json={"data": None})
        )

        assert await epic.regional_prices() == {}

    @respx.mock
    async def test_storefront_is_cached(self, epic: EpicClient) -> None:
        route = mock_storefront([storefront_node("A", 100, 100)])

        await epic.offer_for("A")
        await epic.offer_for("A")

        assert route.call_count == 1
