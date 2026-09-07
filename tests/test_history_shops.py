"""Фильтр истории цен по магазину.

История сворачивается до одной точки на дату — самой дешёвой по всем
магазинам, поэтому в списке чередуются GameBillet, eTail.Market и Epic, а
Steam не появляется вовсе, даже если свои скидки у него были. Отбор
магазина и сбор кнопок проверяются здесь.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bot.keyboards.games import HISTORY_SHOP_LIMIT, history_keyboard
from bot.services.models import Game, PricePoint
from bot.services.shops import filter_points, shop_token, shops_in
from bot.utils import cards

GAME = Game(title="Marvel's Spider-Man Remastered", itad_id="018d937f-2997-7131")


def point(day: int, price: str, shop: str | None, cut: int = 50) -> PricePoint:
    return PricePoint(
        at=datetime(2026, 9, day, tzinfo=UTC),
        price=Decimal(price),
        currency="KZT",
        cut=cut,
        shop=shop,
        exact=True,
    )


# Расклад с реального Spider-Man: Epic скидывает часто, Steam — ни разу
POINTS = [
    point(1, "10030", "GameBillet"),
    point(2, "23757", "eTail.Market"),
    point(3, "9400", "Epic Games Store"),
    point(4, "9400", "Epic Games Store"),
    point(5, "9400", "Epic Games Store"),
    point(6, "27040", "JoyBuggy"),
]


class TestShopsIn:
    def test_known_shops_come_first_in_curated_order(self) -> None:
        """Мелкие ресейлеры скидывают чаще, но кнопка нужна не им."""
        assert [key for key, _ in shops_in(POINTS)] == [
            "epic",
            "gamebillet",
            "etailmarket",
            "joybuggy",
        ]

    def test_steam_survives_the_button_cap(self) -> None:
        """Ради Steam всё и затевалось — сортировка по частоте его теряла."""
        crowd = [point(i, "100", f"Ресейлер {i}") for i in range(1, 9)]
        crowd += [point(9, "100", f"Ресейлер {i}") for i in range(1, 9)]

        keys = [key for key, _ in shops_in([*crowd, point(10, "100", "Steam")])]

        assert keys[0] == "steam"

    def test_unknown_shops_ordered_by_frequency(self) -> None:
        points = [
            point(1, "100", "JoyBuggy"),
            point(2, "100", "eTail.Market"),
            point(3, "100", "eTail.Market"),
        ]

        assert [key for key, _ in shops_in(points)] == ["etailmarket", "joybuggy"]

    def test_keeps_readable_name_for_unknown_shop(self) -> None:
        """Ключ «etailmarket» на кнопке выглядел бы опечаткой."""
        names = dict(shops_in(POINTS))

        assert names["etailmarket"] == "eTail.Market"

    def test_known_shop_gets_canonical_name(self) -> None:
        names = dict(shops_in(POINTS))

        assert names["epic"] == "Epic Games"

    def test_points_without_shop_are_skipped(self) -> None:
        assert shops_in([point(1, "100", None)]) == []

    def test_empty_history(self) -> None:
        assert shops_in([]) == []


class TestFilterPoints:
    def test_keeps_only_chosen_shop(self) -> None:
        picked = filter_points(POINTS, "epic")

        assert len(picked) == 3
        assert {p.shop for p in picked} == {"Epic Games Store"}

    def test_empty_key_means_everything(self) -> None:
        assert filter_points(POINTS, "") == POINTS

    def test_shop_without_history_gives_nothing(self) -> None:
        """Steam эту игру не скидывал — и это правда, а не сбой."""
        assert filter_points(POINTS, "steam") == []


class TestShopToken:
    def test_stable_between_calls(self) -> None:
        assert shop_token("epic") == shop_token("epic")

    def test_differs_between_shops(self) -> None:
        assert shop_token("epic") != shop_token("steam")

    def test_short_enough_for_callback(self) -> None:
        """Ради этого токен и нужен: имя магазина в 64 байта не влезает."""
        assert len(shop_token("gamesplanet")) <= 8


class TestHistoryKeyboard:
    def test_offers_only_shops_present_in_history(self) -> None:
        """Steam эту игру не скидывал — кнопки в пустоту быть не должно."""
        markup = history_keyboard(GAME, shops_in(POINTS))
        labels = [b.text for row in markup.inline_keyboard for b in row]

        assert "Steam" not in labels
        assert "Epic Games" in labels
        assert "eTail.Market" in labels

    def test_all_button_is_marked_by_default(self) -> None:
        markup = history_keyboard(GAME, shops_in(POINTS))
        labels = [b.text for row in markup.inline_keyboard for b in row]

        assert labels[0] == "✅ Все"

    def test_chosen_shop_is_marked(self) -> None:
        markup = history_keyboard(GAME, shops_in(POINTS), "epic")
        labels = [b.text for row in markup.inline_keyboard for b in row]

        assert "✅ Epic Games" in labels
        assert labels[0] == "Все"

    def test_callback_fits_telegram_limit(self) -> None:
        """64 байта: ключ игры съедает почти всё, магазин должен влезть."""
        markup = history_keyboard(GAME, shops_in(POINTS), "epic")

        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data is not None
                assert len(button.callback_data.encode()) <= 64

    def test_long_tail_of_shops_is_cut(self) -> None:
        many = [point(i, "100", f"Магазин {i}") for i in range(1, 12)]

        markup = history_keyboard(GAME, shops_in(many))
        buttons = [b for row in markup.inline_keyboard for b in row]

        assert len(buttons) == HISTORY_SHOP_LIMIT + 1  # плюс «Все»

    def test_no_buttons_without_history(self) -> None:
        assert history_keyboard(GAME, []).inline_keyboard == []


class TestHistoryCard:
    def test_shop_moves_to_header_when_filtered(self) -> None:
        """В строках магазин повторять незачем — он у всех одинаковый."""
        text = cards.price_history(
            "X", filter_points(POINTS, "epic"), "KZT", only_shop="Epic Games"
        )

        assert "только Epic Games" in text
        assert "· Epic Games Store" not in text

    def test_shop_stays_in_lines_without_filter(self) -> None:
        text = cards.price_history("X", POINTS, "KZT")

        assert "· Epic Games Store" in text
        assert "только" not in text

    def test_empty_filtered_history_names_the_shop(self) -> None:
        text = cards.price_history("X", [], "KZT", only_shop="Steam")

        assert "В Steam эта игра не скидывалась" in text
        assert "слишком новая" not in text

    def test_empty_unfiltered_history_keeps_old_wording(self) -> None:
        text = cards.price_history("X", [], "KZT")

        assert "слишком новая" in text
