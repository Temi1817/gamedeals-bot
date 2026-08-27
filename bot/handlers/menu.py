"""Обработка нажатий постоянного меню.

Роутер подключается до поиска и фильтра по цене: надписи кнопок приходят
обычным текстом, и без этого «🔥 Скидки» ушло бы в поиск как название игры.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.deals import cmd_deals
from bot.handlers.free import cmd_free
from bot.handlers.start import HELP, cmd_settings
from bot.handlers.top import cmd_top
from bot.handlers.watchlist import cmd_list
from bot.keyboards.menu import (
    BTN_DEALS,
    BTN_FREE,
    BTN_HELP,
    BTN_SEARCH,
    BTN_SETTINGS,
    BTN_TOP,
    BTN_WATCHLIST,
    main_menu,
)
from bot.services.aggregator import Aggregator

router = Router(name="menu")

SEARCH_PROMPT = """🔍 <b>Поиск игры</b>

Просто напиши название — например <code>Cyberpunk</code> или <code>Hades</code>.

💡 Ещё варианты:
• число (<code>5000</code>) — скидки дешевле этой суммы
• <code>/watch Hades 3000</code> — следить за ценой"""


@router.message(F.text == BTN_SEARCH)
async def on_search_button(message: Message) -> None:
    await message.answer(SEARCH_PROMPT, reply_markup=main_menu())


@router.message(F.text == BTN_DEALS)
async def on_deals_button(message: Message, user: User, aggregator: Aggregator) -> None:
    await cmd_deals(message, user, aggregator)


@router.message(F.text == BTN_TOP)
async def on_top_button(message: Message, user: User, aggregator: Aggregator) -> None:
    await cmd_top(message, user, aggregator)


@router.message(F.text == BTN_FREE)
async def on_free_button(message: Message, user: User, aggregator: Aggregator) -> None:
    await cmd_free(message, user, aggregator)


@router.message(F.text == BTN_WATCHLIST)
async def on_watchlist_button(
    message: Message, user: User, session: AsyncSession
) -> None:
    await cmd_list(message, user, session)


@router.message(F.text == BTN_SETTINGS)
async def on_settings_button(message: Message, user: User) -> None:
    await cmd_settings(message, user)


@router.message(F.text == BTN_HELP)
async def on_help_button(message: Message) -> None:
    await message.answer(HELP, disable_web_page_preview=True, reply_markup=main_menu())
