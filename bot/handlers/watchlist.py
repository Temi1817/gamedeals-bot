"""`/watch` и `/list` — отслеживание цен."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repo import GameRepo, SnapshotRepo, WatchRepo
from bot.keyboards.games import UnwatchCB, watchlist_keyboard
from bot.services.aggregator import Aggregator, _currency_for
from bot.services.models import Game
from bot.utils.formatting import escape, format_price, link
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="watchlist")

MAX_WATCHES = 50

WATCH_USAGE = """Как пользоваться:

<code>/watch Hades 3000</code> — напишу, когда Hades подешевеет до 3000 ₸
<code>/watch Hades</code> — напишу при любом снижении цены

Ещё проще: найди игру и нажми 🔔 на карточке."""


def _parse_args(raw: str) -> tuple[str, Decimal | None]:
    """`'Hades 3000'` → `('Hades', 3000)`.

    Целевая цена — последнее слово, если оно число. Иначе считаем, что
    пользователь хочет знать о любом снижении.
    """
    parts = raw.split()
    if not parts:
        return "", None

    candidate = parts[-1].replace(",", ".").replace(" ", "")
    try:
        price = Decimal(candidate)
    except (InvalidOperation, ValueError):
        return raw.strip(), None

    if price <= 0 or len(parts) == 1:
        return raw.strip(), None
    return " ".join(parts[:-1]).strip(), price


async def _resolve(
    aggregator: Aggregator, raw: str, title: str, target: Decimal | None
) -> tuple[Game | None, Decimal | None]:
    """Решает, было ли последнее число ценой или частью названия.

    «/watch Cyberpunk 2077» — это игра, а «/watch Hades 3000» — цель по
    цене. Различаем по факту: если вся строка целиком совпадает с
    названием реальной игры, значит число было частью названия.
    """
    if target is not None:
        whole = await aggregator.search(raw, limit=5)
        exact = next((g for g in whole if g.title.casefold() == raw.casefold()), None)
        if exact is not None:
            return exact, None

    found = await aggregator.search(title, limit=1)
    return (found[0] if found else None), target


@router.message(Command("watch"))
async def cmd_watch(
    message: Message,
    command: CommandObject,
    user: User,
    session: AsyncSession,
    aggregator: Aggregator,
) -> None:
    title, target = _parse_args(command.args or "")
    if not title:
        await message.answer(WATCH_USAGE)
        return

    watches = WatchRepo(session)
    if await watches.count_for_user(user.id) >= MAX_WATCHES:
        await message.answer(
            f"У тебя уже {MAX_WATCHES} игр в отслеживании — больше не потяну. "
            "Убери что-нибудь через /list."
        )
        return

    raw = (command.args or "").strip()

    notice = await message.answer("🔎 Ищу игру…")
    try:
        game, target = await _resolve(aggregator, raw, title, target)
    finally:
        await notice.delete()

    if game is None:
        await message.answer(
            f"Не нашёл игру «{escape(title)}». Попробуй написать название "
            "по-английски."
        )
        return

    currency = _currency_for(user.country, "KZT")

    stored = await GameRepo(session).upsert(
        title=game.title,
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
        slug=game.slug,
        image_url=game.image_url,
    )
    _, created = await watches.add(
        user_id=user.id,
        game_id=stored.id,
        target_price=target,
        currency=currency,
        notify_any_drop=target is None,
    )

    if target is not None:
        goal = f"как только цена упадёт ниже {format_price(target, currency)}"
    else:
        goal = "при любом снижении цены"

    verb = "Слежу за" if created else "Обновил цель для"
    await message.answer(
        f"🔔 {verb} <b>{escape(game.title)}</b> — напишу, {escape(goal)}."
    )
    log.info("watch_set", tg_id=user.tg_id, game=game.title, target=str(target))


@router.message(Command("list"))
async def cmd_list(message: Message, user: User, session: AsyncSession) -> None:
    watches = await WatchRepo(session).for_user(user.id)
    if not watches:
        await message.answer(
            "Пока пусто.\n\n"
            "Найди игру и нажми 🔔 на карточке, либо напиши "
            "<code>/watch Hades 3000</code>."
        )
        return

    currency = _currency_for(user.country, "KZT")
    latest = await SnapshotRepo(session).latest_prices(
        [w.game_id for w in watches], currency
    )

    lines = [f"🔔 <b>Отслеживаю {len(watches)}</b>", ""]
    buttons: list[tuple[int, str]] = []

    for watch in watches:
        game = watch.game
        snapshot = latest.get(watch.game_id)

        if snapshot is not None:
            now = format_price(snapshot.price, snapshot.currency)
            current = link(now, snapshot.url)
        else:
            current = "цена ещё не замерена"

        if watch.target_price is not None:
            goal = f"цель {format_price(watch.target_price, watch.currency)}"
        else:
            goal = "любое снижение"

        lines.append(f"• <b>{escape(game.title)}</b> — {current} · {escape(goal)}")
        buttons.append((watch.id, game.title))

    lines.append("")
    lines.append("<i>Цены обновляются раз в час. Кнопка ниже — удалить.</i>")

    await message.answer(
        "\n".join(lines),
        reply_markup=watchlist_keyboard(buttons),
        disable_web_page_preview=True,
    )


@router.callback_query(UnwatchCB.filter())
async def on_unwatch(
    callback: CallbackQuery,
    callback_data: UnwatchCB,
    user: User,
    session: AsyncSession,
) -> None:
    removed = await WatchRepo(session).remove(callback_data.watch_id, user.id)
    await callback.answer("Убрал из отслеживания" if removed else "Уже удалено")

    if not isinstance(callback.message, Message):
        return

    remaining = await WatchRepo(session).for_user(user.id)
    if not remaining:
        await callback.message.edit_text("Список отслеживания пуст.")
        return

    await callback.message.edit_reply_markup(
        reply_markup=watchlist_keyboard([(w.id, w.game.title) for w in remaining])
    )
