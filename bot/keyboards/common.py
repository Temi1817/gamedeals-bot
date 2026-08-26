"""Клавиатуры и callback-данные общего назначения."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Страны, между которыми имеет смысл переключаться в один клик.
# KZ — по умолчанию; остальные добавлены как ближайшие ценовые регионы.
COUNTRIES: dict[str, str] = {
    "KZ": "🇰🇿 Казахстан (₸)",
    "RU": "🇷🇺 Россия (₽)",
    "UA": "🇺🇦 Украина (₴)",
    "US": "🇺🇸 США ($)",
    "TR": "🇹🇷 Турция",
    "PL": "🇵🇱 Польша",
}


class CountryCB(CallbackData, prefix="country"):
    code: str


class NotifyCB(CallbackData, prefix="notify"):
    enabled: bool


def country_keyboard(current: str) -> InlineKeyboardMarkup:
    """Сетка выбора региона; текущий помечен галочкой."""
    builder = InlineKeyboardBuilder()
    for code, label in COUNTRIES.items():
        mark = "✅ " if code == current else ""
        builder.button(text=f"{mark}{label}", callback_data=CountryCB(code=code))
    builder.adjust(2)
    return builder.as_markup()


def settings_keyboard(current_country: str, notify_enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🌍 Регион: {current_country}", callback_data="settings:country"
    )
    builder.button(text="🏬 Магазины", callback_data="settings:shops")
    builder.button(
        text=("🔔 Уведомления: вкл" if notify_enabled else "🔕 Уведомления: выкл"),
        callback_data=NotifyCB(enabled=not notify_enabled),
    )
    builder.adjust(1)
    return builder.as_markup()


def close_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="close")]]
    )


class ShopCB(CallbackData, prefix="shop"):
    """Переключение магазина в настройках. `key` пустой — сбросить на все."""

    key: str


def shops_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    """Выбор магазинов. Пустой выбор означает «все»."""
    from bot.services.shops import KNOWN_SHOPS

    builder = InlineKeyboardBuilder()
    for shop in KNOWN_SHOPS:
        mark = "✅ " if shop.key in selected else "▫️ "
        builder.button(text=f"{mark}{shop.title}", callback_data=ShopCB(key=shop.key))
    builder.adjust(2)

    all_mark = "✅ " if not selected else ""
    builder.row(
        InlineKeyboardButton(
            text=f"{all_mark}🌐 Все магазины", callback_data=ShopCB(key="").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:back")
    )
    return builder.as_markup()
