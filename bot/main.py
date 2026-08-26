"""Точка входа: миграции, роутеры, запуск в режиме polling или webhook."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import ROOT_DIR, Settings, get_settings
from bot.db.session import create_sessionmaker, dispose_engine, get_engine
from bot.handlers import build_router
from bot.middlewares import DbSessionMiddleware, UserMiddleware
from bot.services.factory import Services, build_services
from bot.utils.logging import get_logger, setup_logging

log = get_logger(__name__)

COMMANDS = [
    BotCommand(command="find", description="Найти игру и цены по магазинам"),
    BotCommand(command="watch", description="Следить за ценой: /watch Hades 3000"),
    BotCommand(command="list", description="Что я отслеживаю"),
    BotCommand(command="deals", description="Топ скидок дня"),
    BotCommand(command="free", description="Бесплатные раздачи"),
    BotCommand(command="settings", description="Регион и уведомления"),
    BotCommand(command="help", description="Справка"),
]


def _run_migrations_sync(root: Path) -> None:
    """Синхронный прогон `alembic upgrade head` — вызывается в отдельном
    потоке, потому что внутри Alembic делает свой `asyncio.run`."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    # иначе alembic перенастроит логирование под себя и заглушит логи бота
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def apply_migrations() -> None:
    await asyncio.to_thread(_run_migrations_sync, ROOT_DIR)
    log.info("migrations_applied")


def create_dispatcher(settings: Settings, services: Services) -> Dispatcher:
    engine = get_engine(settings)
    sessionmaker = create_sessionmaker(engine)

    dp = Dispatcher()
    dp["settings"] = settings
    dp["sessionmaker"] = sessionmaker
    # хендлеры получают агрегатор как обычный аргумент
    dp["aggregator"] = services.aggregator

    # Порядок важен: сначала сессия, потом пользователь (он её использует).
    for observer in (dp.message, dp.callback_query, dp.inline_query):
        observer.middleware(DbSessionMiddleware(sessionmaker))
        observer.middleware(UserMiddleware(settings.default_country))

    dp.include_router(build_router())
    return dp


async def on_startup(bot: Bot, settings: Settings) -> None:
    await apply_migrations()
    await bot.set_my_commands(COMMANDS)
    me = await bot.get_me()
    log.info(
        "bot_started",
        username=me.username,
        country=settings.default_country,
        itad=bool(settings.itad_key),
        mode="webhook" if settings.use_webhook else "polling",
    )


async def on_shutdown() -> None:
    await dispose_engine()
    log.info("bot_stopped")


async def run_polling(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await on_startup(bot, settings)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()


async def run_webhook(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    if not settings.webhook_base_url:
        raise RuntimeError("USE_WEBHOOK=true, но WEBHOOK_BASE_URL пуст")

    secret = (
        settings.webhook_secret.get_secret_value() if settings.webhook_secret else None
    )
    await bot.set_webhook(
        settings.webhook_url,
        secret_token=secret,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    await on_startup(bot, settings)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=settings.webhook_path
    )
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    log.info("webhook_listening", url=settings.webhook_url)

    try:
        await asyncio.Event().wait()  # держим процесс до сигнала
    finally:
        await runner.cleanup()
        await on_shutdown()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    services = build_services(settings)
    dp = create_dispatcher(settings, services)

    try:
        if settings.use_webhook:
            await run_webhook(bot, dp, settings)
        else:
            await run_polling(bot, dp, settings)
    finally:
        await services.close()
        await bot.session.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
