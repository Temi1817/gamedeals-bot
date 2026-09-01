"""Клавиатуры поиска и карточки игры.

`callback_data` в Telegram ограничен 64 байтами, поэтому игру носим не
целиком, а ключом вида `i:<uuid>` / `s:<appid>` / `c:<id>` — по нему
агрегатор восстанавливает всё остальное.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.models import Game, Offer
from bot.utils.formatting import format_price

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


# Длина надписи кнопки. Telegram допускает больше, но на телефоне длинное
# название переносится и ломает столбик цен.
TITLE_LIMIT = 34


def search_keyboard(
    results: list[tuple[Game, Offer | None]], currency: str = "KZT"
) -> InlineKeyboardMarkup:
    """Кнопки результатов поиска — по одной на игру, с ценой.

    Цена на кнопке решает вполне конкретную беду: по запросу «Grand Theft
    Auto V» первым идёт снятое с продажи издание за 18 465 ₸, а живое
    «Enhanced» за 6 921 ₸ — вторым, и по названиям их не различить.
    """
    builder = InlineKeyboardBuilder()
    for game, offer in results:
        if not fits_callback(game):
            continue

        title = game.title
        if len(title) > TITLE_LIMIT:
            title = title[: TITLE_LIMIT - 1] + "…"

        if offer is None:
            label = f"{title} · нет в продаже"
        elif offer.is_free:
            label = f"{title} · бесплатно 🎉"
        else:
            label = f"{title} · от {format_price(offer.sort_key, currency)}"

        builder.button(text=label, callback_data=GameCB(key=game.key))
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
        builder.button(text=f"🗑 {label}", callback_data=UnwatchCB(watch_id=watch_id))
    builder.adjust(1)
    return builder.as_markup()


class DealsCB(CallbackData, prefix="deals"):
    """Пагинация и фильтры в списке скидок.

    `price` — строка, а не Decimal: пустое значение означает «без потолка»,
    а CallbackData не умеет опциональные числовые поля.
    """

    page: int
    cut: int
    price: str


# Пороги скидки для кнопок под списком скидок. 0 — «любая».
CUT_PRESETS: tuple[int, ...] = (0, 25, 50, 75, 90)


def deals_keyboard(
    *, page: int, min_cut: int, price: str, has_more: bool
) -> InlineKeyboardMarkup:
    """Пресеты скидки плюс постраничная навигация."""
    builder = InlineKeyboardBuilder()

    for cut in CUT_PRESETS:
        label = "любая" if cut == 0 else f"от {cut}%"
        mark = "✅ " if min_cut == cut else ""
        builder.button(
            text=f"{mark}{label}",
            callback_data=DealsCB(page=0, cut=cut, price=price),
        )
    builder.adjust(3, 2)

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=DealsCB(page=page - 1, cut=min_cut, price=price).pack(),
            )
        )
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text="Дальше ▶️",
                callback_data=DealsCB(page=page + 1, cut=min_cut, price=price).pack(),
            )
        )
    if nav:
        builder.row(*nav)

    return builder.as_markup()


class TopCB(CallbackData, prefix="top"):
    """Переключение вкладок рейтинга."""

    kind: str


class WatchTargetCB(CallbackData, prefix="wt"):
    """Выбор цели по цене кнопками, без набора команды.

    `percent` — на сколько ниже текущей цены ждать. 0 означает «любое
    снижение», то есть цель не задана. `shop` — магазин, выбранный шагом
    раньше; пустой означает «любой».
    """

    key: str
    percent: int
    shop: str


TOP_KINDS: tuple[tuple[str, str], ...] = (
    ("steam", "🎮 Steam"),
    ("epic", "🟣 Epic"),
    ("all", "🌐 Все магазины"),
)

# Насколько ниже текущей цены можно ждать, не набирая сумму руками
TARGET_PERCENTS: tuple[int, ...] = (20, 30, 50)


def top_keyboard(current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for kind, label in TOP_KINDS:
        mark = "✅ " if kind == current else ""
        builder.button(text=f"{mark}{label}", callback_data=TopCB(kind=kind))
    builder.adjust(2)
    return builder.as_markup()


def watch_target_keyboard(game: Game, shop: str = "") -> InlineKeyboardMarkup:
    """Цель по цене одним нажатием вместо «/watch Hades 3000»."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔔 Любое снижение",
        callback_data=WatchTargetCB(key=game.key, percent=0, shop=shop),
    )
    for percent in TARGET_PERCENTS:
        builder.button(
            text=f"🎯 −{percent}% от текущей",
            callback_data=WatchTargetCB(key=game.key, percent=percent, shop=shop),
        )
    builder.button(text="◀️ Отмена", callback_data=GameCB(key=game.key))
    builder.adjust(1, 3, 1)
    return builder.as_markup()


class WatchShopCB(CallbackData, prefix="ws"):
    """Магазин для конкретного отслеживания. Пустой ключ — любой."""

    key: str
    shop: str


def watch_shop_keyboard(game: Game, shops: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Выбор магазина при подписке.

    Показываем только магазины, где игра реально продаётся: предлагать
    следить за ценой там, где её нет, бессмысленно.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌐 Любой магазин", callback_data=WatchShopCB(key=game.key, shop="")
    )
    for shop_key, title in shops:
        builder.button(
            text=f"🏬 {title}",
            callback_data=WatchShopCB(key=game.key, shop=shop_key),
        )
    builder.button(text="◀️ Отмена", callback_data=GameCB(key=game.key))
    builder.adjust(1, 2, 2, 2, 1)
    return builder.as_markup()
