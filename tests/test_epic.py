"""Разбор раздач Epic Games."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import respx

from bot.services.epic import PROMOTIONS_URL, EpicClient
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
