"""Клавиатуры поиска и карточки игры.

`callback_data` в Telegram ограничен 64 байтами, поэтому игру носим не
целиком, а ключом вида `i:<uuid>` / `s:<appid>` / `c:<id>` — по нему
агрегатор восстанавливает всё остальное.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.models import Game

# С запасом: префикс, разделители и служебные поля тоже считаются
MAX_KEY_LENGTH = 48


class GameCB(CallbackData, prefix="game"):
    """Показать карточку игры."""

    key: str


class WatchCB(CallbackData, prefix="watch"):
    """Начать отслеживать игру с карточки."""

    key: str


class UnwatchCB(CallbackData, prefix="unwatch"):
    watch_id: int


class HistoryCB(CallbackData, prefix="hist"):
    key: str


def fits_callback(game: Game) -> bool:
    """Влезает ли ключ игры в ограничение Telegram."""
    return len(game.key.encode()) <= MAX_KEY_LENGTH


def search_keyboard(games: list[Game]) -> InlineKeyboardMarkup:
    """Кнопки результатов поиска — по одной на игру."""
    builder = InlineKeyboardBuilder()
    for game in games:
        if not fits_callback(game):
            continue
        title = game.title if len(game.title) <= 60 else game.title[:57] + "…"
        builder.button(text=title, callback_data=GameCB(key=game.key))
    builder.adjust(1)
    return builder.as_markup()


def game_card_keyboard(game: Game, *, watched: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if fits_callback(game):
        builder.button(
            text="🔕 Не отслеживать" if watched else "🔔 Отслеживать",
            callback_data=WatchCB(key=game.key),
        )
        builder.button(text="📉 История цены", callback_data=HistoryCB(key=game.key))
    builder.adjust(2)
    return builder.as_markup()


def watchlist_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Кнопки удаления для /list: (id отслеживания, название)."""
    builder = InlineKeyboardBuilder()
    for watch_id, title in items:
        label = title if len(title) <= 40 else title[:37] + "…"
        builder.button(
            text=f"🗑 {label}", callback_data=UnwatchCB(watch_id=watch_id)
        )
    builder.adjust(1)
    return builder.as_markup()
