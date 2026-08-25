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
    country_keyboard,
    settings_keyboard,
)
from bot.utils.formatting import escape
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="start")

WELCOME = """👋 Привет, {name}!

Я слежу за ценами на PC-игры сразу во всех магазинах — Steam, GOG, Epic,
Humble, Fanatical, GreenManGaming и других.

<b>Что умею:</b>
• Напиши название игры (или <code>/find Cyberpunk</code>) — покажу цены
  по магазинам от дешёвой к дорогой, с историческим минимумом.
• Напиши просто число, например <code>5000</code> — соберу скидки дешевле
  этой суммы.
• <code>/watch Hades 3000</code> — сообщу, когда цена упадёт ниже цели.
• /list — что ты отслеживаешь
• /deals — топ скидок дня
• /free — бесплатные раздачи прямо сейчас

Регион сейчас: <b>{country}</b>. Сменить — /settings"""

HELP = """<b>Команды</b>

/find &lt;название&gt; — поиск игры и цены по магазинам
/watch &lt;название&gt; &lt;цена&gt; — следить за ценой
/list — список отслеживаемого
/deals — топ-10 скидок дня
/free — бесплатные раздачи
/settings — регион и уведомления

<b>Без команд</b>
• текст → поиск игры
• число → скидки дешевле этой суммы"""


def _country_label(code: str) -> str:
    return COUNTRIES.get(code, code)


@router.message(CommandStart())
async def cmd_start(message: Message, user: User) -> None:
    name = escape(message.from_user.first_name if message.from_user else "друг")
    await message.answer(
        WELCOME.format(name=name, country=_country_label(user.country)),
        disable_web_page_preview=True,
    )
    log.info("user_started", tg_id=user.tg_id, country=user.country)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, disable_web_page_preview=True)


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User) -> None:
    await message.answer(
        f"⚙️ <b>Настройки</b>\n\nРегион: {_country_label(user.country)}",
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
            f"⚙️ <b>Настройки</b>\n\nРегион: {_country_label(user.country)}",
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


@router.callback_query(lambda c: c.data == "close")
async def close_message(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.delete()
    await callback.answer()
