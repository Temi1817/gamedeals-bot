"""Регистрация роутеров.

Порядок важен. deals ловит сообщения из одних цифр, search — любой
оставшийся текст, поэтому search идёт последним: иначе он перехватил бы
и числа, и всё остальное.
"""

from __future__ import annotations

from aiogram import Router

from bot.handlers import deals, errors, free, search, start, watchlist


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(errors.router)
    router.include_router(start.router)
    router.include_router(watchlist.router)
    router.include_router(free.router)
    router.include_router(deals.router)
    router.include_router(search.router)
    return router


__all__ = ["build_router"]
