"""Клавиатуры и упаковка callback_data.

Здесь ловится целый класс поломок: `callback_data` в Telegram — это
64 байта плюс жёсткие правила aiogram, и нарушение видно только в рантайме,
когда бот уже отвечает пользователю ошибкой.
"""

from __future__ import annotations

import pytest

from bot.keyboards.games import (
    MAX_KEY_LENGTH,
    DealsCB,
    GameCB,
    HistoryCB,
    UnwatchCB,
    WatchCB,
    fits_callback,
    game_card_keyboard,
    search_keyboard,
    watchlist_keyboard,
)
from bot.services.models import KEY_SEP, Game

# Настоящий ID из ответа ITAD — на нём всё и ломалось
ITAD_ID = "018d937f-2997-7131-b8b9-7c8af4825fa8"
CYBERPUNK = Game(title="Cyberpunk 2077", itad_id=ITAD_ID, steam_appid=1091500)


class TestGameKey:
    def test_key_has_no_colon(self) -> None:
        """Двоеточием aiogram разделяет поля — ключ с ним не упакуется."""
        assert ":" not in CYBERPUNK.key

    def test_key_prefers_itad_id(self) -> None:
        assert CYBERPUNK.key == f"i{KEY_SEP}{ITAD_ID}"

    def test_key_falls_back_to_steam(self) -> None:
        assert Game(title="X", steam_appid=1091500).key == f"s{KEY_SEP}1091500"

    def test_key_falls_back_to_cheapshark(self) -> None:
        assert Game(title="X", cheapshark_id="202350").key == f"c{KEY_SEP}202350"

    def test_itad_key_fits_the_limit(self) -> None:
        assert fits_callback(CYBERPUNK)
        assert len(CYBERPUNK.key.encode()) <= MAX_KEY_LENGTH


class TestCallbackPacking:
    """Каждый CallbackData обязан упаковываться с реальным ключом игры."""

    @pytest.mark.parametrize("factory", [GameCB, WatchCB, HistoryCB])
    def test_packs_with_itad_key(self, factory: type[GameCB]) -> None:
        packed = factory(key=CYBERPUNK.key).pack()

        assert len(packed.encode()) <= 64
        assert factory.unpack(packed).key == CYBERPUNK.key

    def test_packs_with_steam_key(self) -> None:
        game = Game(title="X", steam_appid=1091500)

        assert GameCB.unpack(GameCB(key=game.key).pack()).key == game.key

    def test_unwatch_roundtrip(self) -> None:
        assert UnwatchCB.unpack(UnwatchCB(watch_id=42).pack()).watch_id == 42

    def test_deals_roundtrip(self) -> None:
        packed = DealsCB(page=2, cut=75, price="5000").pack()
        restored = DealsCB.unpack(packed)

        assert (restored.page, restored.cut, restored.price) == (2, 75, "5000")

    def test_deals_without_price(self) -> None:
        restored = DealsCB.unpack(DealsCB(page=0, cut=0, price="").pack())

        assert restored.price == ""


class TestSearchKeyboard:
    def test_builds_button_per_game(self) -> None:
        games = [
            CYBERPUNK,
            Game(title="Cyberpunk 2077: Phantom Liberty", itad_id="018d937f-6ed7"),
        ]

        markup = search_keyboard(games)

        assert len(markup.inline_keyboard) == 2
        assert markup.inline_keyboard[0][0].text == "Cyberpunk 2077"

    def test_long_title_is_trimmed(self) -> None:
        game = Game(title="О" * 100, itad_id=ITAD_ID)

        button = search_keyboard([game]).inline_keyboard[0][0]

        assert len(button.text) <= 60
        assert button.text.endswith("…")

    def test_skips_games_with_oversized_key(self) -> None:
        """Лучше не показать одну строку, чем уронить весь ответ."""
        huge = Game(title="Огромный ключ", itad_id="x" * 200)

        assert search_keyboard([huge]).inline_keyboard == []
        assert len(search_keyboard([huge, CYBERPUNK]).inline_keyboard) == 1

    def test_empty_list(self) -> None:
        assert search_keyboard([]).inline_keyboard == []


class TestGameCardKeyboard:
    def test_has_watch_and_history(self) -> None:
        markup = game_card_keyboard(CYBERPUNK)
        buttons = [b for row in markup.inline_keyboard for b in row]

        assert len(buttons) == 2
        assert "Отслеживать" in buttons[0].text
        assert "История" in buttons[1].text

    def test_watched_state_flips_label(self) -> None:
        markup = game_card_keyboard(CYBERPUNK, watched=True)
        buttons = [b for row in markup.inline_keyboard for b in row]

        assert "Не отслеживать" in buttons[0].text

    def test_no_buttons_for_oversized_key(self) -> None:
        huge = Game(title="X", itad_id="x" * 200)

        assert game_card_keyboard(huge).inline_keyboard == []


class TestWatchlistKeyboard:
    def test_delete_button_per_watch(self) -> None:
        markup = watchlist_keyboard([(1, "Hades"), (2, "Cyberpunk 2077")])

        assert len(markup.inline_keyboard) == 2
        assert markup.inline_keyboard[0][0].text == "🗑 Hades"

    def test_long_title_trimmed(self) -> None:
        button = watchlist_keyboard([(1, "И" * 80)]).inline_keyboard[0][0]

        assert len(button.text) <= 43
