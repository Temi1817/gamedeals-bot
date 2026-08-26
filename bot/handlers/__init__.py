"""Регистрация роутеров.

Порядок важен: search ловит любой текст без команды, поэтому идёт
последним — иначе он перехватит сообщения у остальных хендлеров.
"""

from __future__ import annotations

from aiogram import Router

from bot.handlers import errors, search, start


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(errors.router)
    router.include_router(start.router)
    router.include_router(search.router)
    return router


__all__ = ["build_router"]
