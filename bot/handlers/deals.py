"""`/deals` и фильтр по цене: пользователь пишет число — получает скидки."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.db.models import User
from bot.keyboards.games import DealsCB, deals_keyboard
from bot.services.aggregator import Aggregator, _currency_for
from bot.services.shops import parse_selection
from bot.utils import cards
from bot.utils.formatting import format_price
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="deals")

PAGE_SIZE = 10
# «5000», «5 000», «5000₸» — всё это потолок цены
PRICE_PATTERN = re.compile(r"^\s*(\d[\d\s.,]*)\s*(?:₸|тг|тенге|\$)?\s*$", re.I)


def parse_price(text: str) -> Decimal | None:
    """Достаёт потолок цены из сообщения вида «5000»."""
    match = PRICE_PATTERN.match(text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    # «5.000» у нас значит пять тысяч, а не пять
    if raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")
    try:
        price = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return price if price > 0 else None


async def _show_deals(
    target: Message,
    user: User,
    aggregator: Aggregator,
    *,
    page: int,
    min_cut: int,
    max_price: Decimal | None,
    edit: bool = False,
) -> None:
    currency = _currency_for(user.country, "KZT")

    deals, next_offset = await aggregator.deals(
        user.country,
        limit=PAGE_SIZE,
        offset=page * PAGE_SIZE,
        min_cut=min_cut,
        max_price=max_price,
        shops=parse_selection(user.preferred_shops),
    )

    text = cards.deals_list(deals, page, currency)
    if max_price is not None:
        text = f"{text}\n\n<i>Потолок: {format_price(max_price, currency)}</i>"

    markup = deals_keyboard(
        page=page,
        min_cut=min_cut,
        price=str(max_price) if max_price is not None else "",
        has_more=next_offset is not None,
    )

    if edit:
        await target.edit_text(
            text, reply_markup=markup, disable_web_page_preview=True
        )
    else:
        await target.answer(
            text, reply_markup=markup, disable_web_page_preview=True
        )


@router.message(Command("deals"))
async def cmd_deals(message: Message, user: User, aggregator: Aggregator) -> None:
    notice = await message.answer("🔥 Собираю скидки…")
    try:
        await _show_deals(message, user, aggregator, page=0, min_cut=0, max_price=None)
    finally:
        await notice.delete()


@router.message(F.text.regexp(PRICE_PATTERN))
async def on_price_filter(
    message: Message, user: User, aggregator: Aggregator
) -> None:
    """Просто число — значит «покажи скидки дешевле этой суммы»."""
    price = parse_price(message.text or "")
    if price is None:
        return

    notice = await message.answer("🔥 Ищу скидки…")
    try:
        await _show_deals(
            message, user, aggregator, page=0, min_cut=0, max_price=price
        )
    finally:
        await notice.delete()
    log.info("price_filter", tg_id=user.tg_id, max_price=str(price))


@router.callback_query(DealsCB.filter())
async def on_deals_page(
    callback: CallbackQuery,
    callback_data: DealsCB,
    user: User,
    aggregator: Aggregator,
) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return

    max_price: Decimal | None = None
    if callback_data.price:
        try:
            max_price = Decimal(callback_data.price)
        except (InvalidOperation, ValueError):
            max_price = None

    await _show_deals(
        callback.message,
        user,
        aggregator,
        page=callback_data.page,
        min_cut=callback_data.cut,
        max_price=max_price,
        edit=True,
    )
