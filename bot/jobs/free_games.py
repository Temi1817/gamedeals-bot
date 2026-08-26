"""Автопост бесплатных раздач тем, у кого включены уведомления."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.repo import UserRepo
from bot.services.aggregator import Aggregator
from bot.utils import cards
from bot.utils.logging import get_logger

log = get_logger(__name__)


async def post_free_games(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    aggregator: Aggregator,
) -> None:
    """Рассылает текущие раздачи. Молчит, если раздавать нечего."""
    async with sessionmaker() as session:
        users = await UserRepo(session).all_with_notifications()

    if not users:
        return

    # раздачи одинаковы для всех в одном регионе — тянем по региону, не по юзеру
    by_country: dict[str, str] = {}
    sent = 0

    for user in users:
        text = by_country.get(user.country)
        if text is None:
            try:
                games = await aggregator.free_games(country=user.country)
            except Exception as exc:
                log.warning("free_post_failed", country=user.country, error=str(exc))
                continue

            active = [g for g in games if not g.upcoming]
            if not active:
                # без активных раздач молчим: рассылка «сегодня ничего» бесит
                by_country[user.country] = ""
                continue

            text = cards.free_games(games)
            by_country[user.country] = text

        if not text:
            continue

        try:
            await bot.send_message(user.tg_id, text, disable_web_page_preview=True)
            sent += 1
        except TelegramForbiddenError:
            log.info("free_post_skipped_blocked", tg_id=user.tg_id)
        except Exception as exc:
            log.warning("free_post_send_failed", tg_id=user.tg_id, error=str(exc))

    log.info("free_post_finished", sent=sent)
