"""Канонические имена магазинов и фильтр по ним."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.services.models import CHEAPSHARK, ITAD, STEAM, Offer, Shop
from bot.services.shops import (
    KNOWN_SHOPS,
    dump_selection,
    filter_offers,
    itad_shop_ids,
    parse_selection,
    shop_key,
    title_for,
)


def offer(shop_name: str, source: str = ITAD) -> Offer:
    return Offer(
        shop=Shop(id=shop_name.lower(), name=shop_name, source=source),
        price=Decimal("10"),
        currency="USD",
    )


class TestShopKey:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Steam", "steam"),
            ("steam", "steam"),
            # один магазин приходит из источников под разными именами
            ("Epic Game Store", "epic"),
            ("Epic Games Store", "epic"),
            ("Epic", "epic"),
            ("GOG", "gog"),
            ("GOG.com", "gog"),
            ("Humble Store", "humble"),
            ("Humble Bundle", "humble"),
            ("GreenManGaming", "gmg"),
            ("Green Man Gaming", "gmg"),
            # региональные витрины GamesPlanet — это один магазин
            ("GamesPlanet US", "gamesplanet"),
            ("GamesPlanet DE", "gamesplanet"),
        ],
    )
    def test_canonical_keys(self, name: str, expected: str) -> None:
        assert shop_key(name) == expected

    def test_unknown_shop_keeps_own_identity(self) -> None:
        """Незнакомый магазин не должен слиться с другими."""
        assert shop_key("Совсем Новый Магазин") != shop_key("Другой Магазин")

    def test_unknown_shop_is_normalised(self) -> None:
        assert shop_key("Wingamestore") == shop_key("WinGameStore")

    def test_title_for_known_and_unknown(self) -> None:
        assert title_for("gmg") == "GreenManGaming"
        assert title_for("что-то") == "что-то"

    def test_all_known_keys_are_unique(self) -> None:
        keys = [s.key for s in KNOWN_SHOPS]
        assert len(keys) == len(set(keys))


class TestSelectionStorage:
    def test_roundtrip(self) -> None:
        assert parse_selection(dump_selection({"steam", "gog"})) == {"steam", "gog"}

    def test_empty_means_all(self) -> None:
        assert parse_selection("") == set()
        assert parse_selection(None) == set()
        assert dump_selection(set()) == ""

    def test_ignores_blanks(self) -> None:
        assert parse_selection("steam,,  ,gog") == {"steam", "gog"}


class TestFilterOffers:
    def test_empty_selection_keeps_everything(self) -> None:
        offers = [offer("Steam", STEAM), offer("GOG")]

        assert filter_offers(offers, set()) == offers

    def test_keeps_only_selected(self) -> None:
        offers = [offer("Steam", STEAM), offer("GOG"), offer("Humble Store")]

        result = filter_offers(offers, {"steam", "gog"})

        assert [o.shop.name for o in result] == ["Steam", "GOG"]

    def test_matches_across_source_naming(self) -> None:
        """«Epic Game Store» от ITAD должен попасть под ключ epic."""
        offers = [offer("Epic Game Store")]

        assert filter_offers(offers, {"epic"}) == offers

    def test_falls_back_when_nothing_matches(self) -> None:
        """Пустая карточка хуже, чем цена из нелюбимого магазина."""
        offers = [offer("Steam", STEAM), offer("GOG")]

        assert filter_offers(offers, {"fanatical"}) == offers

    def test_reseller_offers_are_filtered_too(self) -> None:
        offers = [offer("Steam", STEAM), offer("Humble Store", CHEAPSHARK)]

        result = filter_offers(offers, {"steam"})

        assert [o.shop.name for o in result] == ["Steam"]


class TestItadShopIds:
    def directory(self) -> dict[str, Shop]:
        return {
            "61": Shop(id="61", name="Steam", source=ITAD),
            "35": Shop(id="35", name="GOG", source=ITAD),
            "16": Shop(id="16", name="Epic Game Store", source=ITAD),
            "6": Shop(id="6", name="Fanatical", source=ITAD),
        }

    def test_maps_keys_to_ids(self) -> None:
        assert itad_shop_ids({"steam", "gog"}, self.directory()) == [35, 61]

    def test_matches_alias_names(self) -> None:
        assert itad_shop_ids({"epic"}, self.directory()) == [16]

    def test_empty_selection_means_no_filter(self) -> None:
        assert itad_shop_ids(set(), self.directory()) == []

    def test_unknown_selection_yields_nothing(self) -> None:
        assert itad_shop_ids({"нетакого"}, self.directory()) == []

    def test_broken_directory_entry_is_skipped(self) -> None:
        directory = {"не-число": Shop(id="x", name="Steam", source=ITAD)}

        assert itad_shop_ids({"steam"}, directory) == []
