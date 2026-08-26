"""`/free` — бесплатные раздачи."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db.models import User
from bot.services.aggregator import Aggregator
from bot.utils import cards
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="free")


@router.message(Command("free"))
async def cmd_free(message: Message, user: User, aggregator: Aggregator) -> None:
    notice = await message.answer("🎁 Смотрю раздачи…")
    try:
        games = await aggregator.free_games(country=user.country)
    finally:
        await notice.delete()

    await message.answer(cards.free_games(games), disable_web_page_preview=True)
    log.info("free_shown", tg_id=user.tg_id, count=len(games))
