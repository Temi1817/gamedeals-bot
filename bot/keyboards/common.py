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
