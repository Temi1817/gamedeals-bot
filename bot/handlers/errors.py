"""Глобальный обработчик ошибок: ни одно исключение не роняет бота."""

from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="errors")

USER_MESSAGE = (
    "😵 Что-то пошло не так на нашей стороне. Уже видим это в логах — "
    "попробуй ещё раз через минуту."
)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Логирует исключение и, если возможно, отвечает пользователю."""
    exc = event.exception

    # Пользователь заблокировал бота — это не ошибка приложения
    if isinstance(exc, TelegramForbiddenError):
        log.info("bot_blocked_by_user", error=str(exc))
        return True

    # "message is not modified" прилетает при повторном нажатии кнопки
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        return True

    log.exception("unhandled_error", error=str(exc), update=event.update.event_type)

    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(USER_MESSAGE, show_alert=True)
        elif update.message is not None:
            await update.message.answer(USER_MESSAGE)
    except Exception as notify_exc:  # ответить не смогли — просто пишем в лог
        log.warning("error_notify_failed", error=str(notify_exc))

    return True
