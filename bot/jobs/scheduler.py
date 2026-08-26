"""Планировщик фоновых задач."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.jobs.free_games import post_free_games
from bot.jobs.price_checker import check_prices
from bot.services.aggregator import Aggregator
from bot.utils.logging import get_logger

log = get_logger(__name__)


def setup_scheduler(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    aggregator: Aggregator,
    settings: Settings,
) -> AsyncIOScheduler:
    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(
        check_prices,
        trigger=IntervalTrigger(minutes=settings.price_check_interval_minutes),
        args=(bot, sessionmaker, aggregator),
        id="price_checker",
        # если предыдущий проход затянулся, накопившиеся запуски не нужны
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        post_free_games,
        trigger=CronTrigger(hour=settings.free_games_post_hour, minute=0, timezone=tz),
        args=(bot, sessionmaker, aggregator),
        id="free_games_post",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    log.info(
        "scheduler_configured",
        price_check_minutes=settings.price_check_interval_minutes,
        free_post_hour=settings.free_games_post_hour,
        timezone=settings.timezone,
    )
    return scheduler
