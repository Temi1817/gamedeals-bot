"""Постоянное меню под полем ввода.

Кнопки reply-клавиатуры отправляются как обычный текст, поэтому их надписи
одновременно служат «командами». Роутер меню подключается раньше поиска —
иначе поиск примет «🔥 Скидки» за название игры.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Найти игру"
BTN_DEALS = "🔥 Скидки"
BTN_TOP = "🏆 Популярное"
BTN_FREE = "🎁 Раздачи"
BTN_WATCHLIST = "🔔 Отслеживаю"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "❓ Помощь"

MENU_BUTTONS = frozenset(
    {BTN_SEARCH, BTN_DEALS, BTN_TOP, BTN_FREE, BTN_WATCHLIST, BTN_SETTINGS, BTN_HELP}
)


def main_menu() -> ReplyKeyboardMarkup:
    """Клавиатура, которая остаётся на экране после ответа бота."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_DEALS)],
            [KeyboardButton(text=BTN_TOP), KeyboardButton(text=BTN_FREE)],
            [KeyboardButton(text=BTN_WATCHLIST), KeyboardButton(text=BTN_SETTINGS)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Название игры или сумма, например 5000",
    )
