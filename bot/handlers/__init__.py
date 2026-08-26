"""Регистрация роутеров.

Порядок важен. Надписи кнопок меню приходят обычным текстом, поэтому menu
идёт до deals и search — иначе «🔥 Скидки» уехало бы в поиск как название
игры. deals ловит сообщения из одних цифр, search — весь оставшийся текст,
поэтому search последний.
"""

from __future__ import annotations

from aiogram import Router

from bot.handlers import (
    deals,
    errors,
    free,
    menu,
    search,
    start,
    top,
    watchlist,
)


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(errors.router)
    router.include_router(start.router)
    router.include_router(watchlist.router)
    router.include_router(free.router)
    router.include_router(top.router)
    router.include_router(menu.router)
    router.include_router(deals.router)
    router.include_router(search.router)
    return router


__all__ = ["build_router"]
