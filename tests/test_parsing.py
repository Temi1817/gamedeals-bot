"""Разбор пользовательского ввода: /watch и фильтр по цене."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.handlers.deals import PRICE_PATTERN, parse_price
from bot.handlers.watchlist import _parse_args, _resolve
from bot.services.models import Game


class TestWatchArgs:
    def test_title_and_price(self) -> None:
        assert _parse_args("Hades 3000") == ("Hades", Decimal("3000"))

    def test_multiword_title(self) -> None:
        assert _parse_args("Cyberpunk 2077 Ultimate 15000") == (
            "Cyberpunk 2077 Ultimate",
            Decimal("15000"),
        )

    def test_trailing_number_is_ambiguous_at_this_layer(self) -> None:
        """«/watch Cyberpunk 2077» и «/watch Hades 3000» тут неразличимы.

        По одной строке решить нельзя, поэтому разбор всегда предполагает
        цену, а спор разрешает `_resolve`, сверяясь с реальными названиями
        игр. См. TestResolve ниже.
        """
        assert _parse_args("Cyberpunk 2077") == ("Cyberpunk", Decimal("2077"))

    def test_single_word_is_always_title(self) -> None:
        assert _parse_args("Hades") == ("Hades", None)
        assert _parse_args("2077") == ("2077", None)

    def test_comma_decimal(self) -> None:
        assert _parse_args("Hades 29,99") == ("Hades", Decimal("29.99"))

    def test_negative_price_ignored(self) -> None:
        assert _parse_args("Hades -100") == ("Hades -100", None)

    def test_empty(self) -> None:
        assert _parse_args("") == ("", None)
        assert _parse_args("   ") == ("", None)


class TestPriceFilter:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5000", Decimal("5000")),
            ("  5000  ", Decimal("5000")),
            ("5000₸", Decimal("5000")),
            ("5000 тг", Decimal("5000")),
            ("5000 тенге", Decimal("5000")),
            ("5 000", Decimal("5000")),
            ("5.000", Decimal("5000")),  # точка как разделитель тысяч
            ("29,99", Decimal("29.99")),
            ("1", Decimal("1")),
        ],
    )
    def test_parses_prices(self, text: str, expected: Decimal) -> None:
        assert parse_price(text) == expected

    @pytest.mark.parametrize(
        "text", ["Cyberpunk", "", "abc123", "0", "-100", "5000 рублей за игру"]
    )
    def test_rejects_non_prices(self, text: str) -> None:
        assert parse_price(text) is None

    def test_pattern_does_not_match_titles(self) -> None:
        """Иначе фильтр по цене перехватит поиск по названию."""
        assert not PRICE_PATTERN.match("Cyberpunk 2077")
        assert not PRICE_PATTERN.match("Hades")
        assert PRICE_PATTERN.match("5000")


class FakeAggregator:
    """Отдаёт игры по точному совпадению названия."""

    def __init__(self, titles: list[str]) -> None:
        self.titles = titles
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 5) -> list[Game]:
        self.queries.append(query)
        hits = [t for t in self.titles if query.casefold() in t.casefold()]
        return [Game(title=t) for t in hits[:limit]]


class TestResolve:
    """Число в конце: часть названия или целевая цена?"""

    async def test_full_title_wins_over_price(self) -> None:
        agg = FakeAggregator(["Cyberpunk 2077"])

        game, target = await _resolve(agg, "Cyberpunk 2077", "Cyberpunk", Decimal("2077"))

        assert game is not None
        assert game.title == "Cyberpunk 2077"
        assert target is None

    async def test_price_kept_when_no_such_game(self) -> None:
        agg = FakeAggregator(["Hades"])

        game, target = await _resolve(agg, "Hades 3000", "Hades", Decimal("3000"))

        assert game is not None
        assert game.title == "Hades"
        assert target == Decimal("3000")

    async def test_partial_match_is_not_enough(self) -> None:
        """«Hades 3000» не должен цепляться за «Hades» как за точное имя."""
        agg = FakeAggregator(["Hades"])

        _, target = await _resolve(agg, "Hades 3000", "Hades", Decimal("3000"))

        assert target == Decimal("3000")

    async def test_no_price_skips_extra_search(self) -> None:
        agg = FakeAggregator(["Hades"])

        await _resolve(agg, "Hades", "Hades", None)

        assert agg.queries == ["Hades"]

    async def test_game_not_found(self) -> None:
        agg = FakeAggregator([])

        game, _ = await _resolve(agg, "Такой игры нет", "Такой игры нет", None)

        assert game is None
