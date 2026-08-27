"""`/start`, `/help`, `/settings` — знакомство и настройки пользователя."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repo import UserRepo
from bot.keyboards.common import (
    COUNTRIES,
    CountryCB,
    NotifyCB,
    ShopCB,
    country_keyboard,
    settings_keyboard,
    shops_keyboard,
)
from bot.keyboards.menu import main_menu
from bot.services.shops import dump_selection, parse_selection, title_for
from bot.utils.formatting import escape
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="start")

WELCOME = """👋 <b>Привет, {name}!</b>

Я знаю, где игра стоит дешевле всего — и напишу, когда подешевеет.

━━━━━━━━━━━━━━━
🔍 <b>Найти игру</b>
Просто напиши название. Покажу цены всех магазинов от дешёвой
к дорогой, минимум за всё время и подскажу, стоит ли брать.

💰 <b>Скидки под бюджет</b>
Напиши сумму — <code>5000</code> — соберу всё дешевле неё.

🔔 <b>Отслеживание</b>
Нашёл игру, нажал 🔔 — напишу, как только подешевеет.
━━━━━━━━━━━━━━━

🌍 <b>{country}</b>   🏬 <b>{shops}</b>

👇 Всё остальное — на кнопках снизу"""

HELP = """❓ <b>Как этим пользоваться</b>
━━━━━━━━━━━━━━━

🔍 <b>Найти игру</b>
Напиши название — <code>Cyberpunk</code>, <code>Hades</code>.
Английское название магазины понимают лучше.

💰 <b>Скидки дешевле суммы</b>
Напиши число — <code>5000</code>.

🏆 <b>Популярное</b>
Что сейчас ждут по скидке и во что играют.

🎁 <b>Раздачи</b>
Бесплатные игры Epic — забирай, пока дают.

🔔 <b>Отслеживание</b>
Кнопка 🔔 на карточке игры. Выберешь кнопкой,
ждать любое снижение или конкретную скидку.

━━━━━━━━━━━━━━━
<b>Точность цен</b>

✅ Steam, GOG и Epic — цена ровно та, что в магазине
≈ — международный прайс, на месте бывает дешевле

━━━━━━━━━━━━━━━
<i>Команды набирать не нужно — всё есть на кнопках.</i>"""


def _country_label(code: str) -> str:
    return COUNTRIES.get(code, code)


def _settings_text(user: User) -> str:
    return (
        "⚙️ <b>Настройки</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"🌍 Регион: <b>{_country_label(user.country)}</b>\n"
        f"🏬 Магазины: <b>{_shops_summary(user)}</b>"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, user: User) -> None:
    name = escape(message.from_user.first_name if message.from_user else "друг")
    await message.answer(
        WELCOME.format(
            name=name,
            country=_country_label(user.country),
            shops=_shops_summary(user),
        ),
        disable_web_page_preview=True,
        reply_markup=main_menu(),
    )
    log.info("user_started", tg_id=user.tg_id, country=user.country)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, disable_web_page_preview=True, reply_markup=main_menu())


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User) -> None:
    await message.answer(
        _settings_text(user),
        reply_markup=settings_keyboard(user.country, user.notify_enabled),
    )


@router.callback_query(lambda c: c.data == "settings:country")
async def open_country_picker(callback: CallbackQuery, user: User) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🌍 Выбери регион — от него зависят валюта и набор магазинов:",
            reply_markup=country_keyboard(user.country),
        )
    await callback.answer()


@router.callback_query(CountryCB.filter())
async def set_country(
    callback: CallbackQuery,
    callback_data: CountryCB,
    user: User,
    session: AsyncSession,
) -> None:
    await UserRepo(session).set_country(user, callback_data.code)
    log.info("country_changed", tg_id=user.tg_id, country=callback_data.code)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _settings_text(user),
            reply_markup=settings_keyboard(user.country, user.notify_enabled),
        )
    await callback.answer(f"Регион: {callback_data.code}")


@router.callback_query(NotifyCB.filter())
async def toggle_notify(
    callback: CallbackQuery,
    callback_data: NotifyCB,
    user: User,
    session: AsyncSession,
) -> None:
    await UserRepo(session).set_notify(user, callback_data.enabled)

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=settings_keyboard(user.country, user.notify_enabled)
        )
    await callback.answer(
        "Уведомления включены" if callback_data.enabled else "Уведомления выключены"
    )


SHOPS_PROMPT = """🏬 <b>Магазины</b>

Отмечай те, что тебе интересны — остальные пропадут из карточек, скидок
и уведомлений. Ничего не отмечено значит «показывать все»."""


def _shops_summary(user: User) -> str:
    selected = parse_selection(user.preferred_shops)
    if not selected:
        return "все"
    names = sorted(title_for(key) for key in selected)
    return ", ".join(names) if len(names) <= 3 else f"{len(names)} шт."


@router.callback_query(lambda c: c.data == "settings:shops")
async def open_shops_picker(callback: CallbackQuery, user: User) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            SHOPS_PROMPT,
            reply_markup=shops_keyboard(parse_selection(user.preferred_shops)),
        )
    await callback.answer()


@router.callback_query(ShopCB.filter())
async def toggle_shop(
    callback: CallbackQuery,
    callback_data: ShopCB,
    user: User,
    session: AsyncSession,
) -> None:
    selected = parse_selection(user.preferred_shops)

    if not callback_data.key:
        selected.clear()  # «все магазины» — это пустой выбор
    elif callback_data.key in selected:
        selected.discard(callback_data.key)
    else:
        selected.add(callback_data.key)

    user.preferred_shops = dump_selection(selected)
    await session.flush()

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=shops_keyboard(selected))
    await callback.answer("Все магазины" if not selected else f"Выбрано: {len(selected)}")


@router.callback_query(lambda c: c.data == "settings:back")
async def back_to_settings(callback: CallbackQuery, user: User) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _settings_text(user),
            reply_markup=settings_keyboard(user.country, user.notify_enabled),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "close")
async def close_message(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.delete()
    await callback.answer()
