"""Middleware: сессия БД и текущий пользователь в каждом апдейте."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.repo import UserRepo

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию на апдейт и коммитит её, если хендлер не упал."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result


class UserMiddleware(BaseMiddleware):
    """Кладёт в `data["user"]` запись из БД, создавая её при первом контакте."""

    def __init__(self, default_country: str = "KZ") -> None:
        self.default_country = default_country

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")

        if tg_user is not None and session is not None and not tg_user.is_bot:
            data["user"] = await UserRepo(session).get_or_create(
                tg_id=tg_user.id,
                username=tg_user.username,
                country=self.default_country,
            )

        return await handler(event, data)
