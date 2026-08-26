"""`/top` — рейтинги игр с текущей ценой."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.db.models import User
from bot.keyboards.games import TopCB, top_keyboard
from bot.services.aggregator import Aggregator, _currency_for
from bot.utils import cards
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="top")

TOP_LIMIT = 10
DEFAULT_KIND = "waitlisted"


async def show_top(
    target: Message,
    user: User,
    aggregator: Aggregator,
    kind: str = DEFAULT_KIND,
    *,
    edit: bool = False,
) -> None:
    deals = await aggregator.top_games(kind, country=user.country, limit=TOP_LIMIT)
    text = cards.top_list(deals, kind, _currency_for(user.country, "KZT"))
    markup = top_keyboard(kind)

    if edit:
        await target.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("top"))
async def cmd_top(message: Message, user: User, aggregator: Aggregator) -> None:
    notice = await message.answer("🏆 Собираю рейтинг…")
    try:
        await show_top(message, user, aggregator)
    finally:
        await notice.delete()
    log.info("top_shown", tg_id=user.tg_id)


@router.callback_query(TopCB.filter())
async def on_top_tab(
    callback: CallbackQuery,
    callback_data: TopCB,
    user: User,
    aggregator: Aggregator,
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_top(
            callback.message, user, aggregator, callback_data.kind, edit=True
        )
