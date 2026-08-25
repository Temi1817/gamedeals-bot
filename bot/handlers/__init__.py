"""Регистрация роутеров. Порядок важен: общий текстовый роутер — последним."""

from __future__ import annotations

from aiogram import Router

from bot.handlers import errors, start


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(errors.router)
    router.include_router(start.router)
    return router


__all__ = ["build_router"]
