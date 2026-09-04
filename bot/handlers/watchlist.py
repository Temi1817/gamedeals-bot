"""`/watch` и `/list` — отслеживание цен."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import PriceSnapshot, User, Watch
from bot.db.repo import GameRepo, SnapshotRepo, WatchRepo
from bot.keyboards.games import UnwatchCB, watchlist_keyboard
from bot.services.aggregator import Aggregator, _currency_for
from bot.services.models import CHEAPSHARK, Game
from bot.services.shops import parse_selection, shop_key, title_for
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
            f"Не нашёл игру «{escape(title)}». Попробуй написать название по-английски."
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


def pick_snapshot(
    snapshots: list[PriceSnapshot], watch: Watch, preferred: set[str]
) -> PriceSnapshot | None:
    """Замер, который показываем в строке этого отслеживания.

    Правило то же, что у джобы уведомлений (`price_checker.pick_offer`):
    магазин, выбранный при подписке, важнее общего фильтра из настроек.
    Но отката на чужой магазин здесь нет — именно молчаливая подмена и
    приводила к тому, что рядом с подписью «Steam» стояла цена Epic.
    """
    # ключи реселлеров — не цена магазина, в списке им не место
    shops = [s for s in snapshots if s.shop.source != CHEAPSHARK]
    if not shops:
        return None

    if watch.shop_key:
        exact = [s for s in shops if shop_key(s.shop.name) == watch.shop_key]
        # один магазин мог прийти из двух источников — берём дешёвый замер
        return min(exact, key=lambda s: s.price) if exact else None

    if preferred:
        allowed = [s for s in shops if shop_key(s.shop.name) in preferred]
        shops = allowed or shops
    return min(shops, key=lambda s: s.price)


def price_text(snapshot: PriceSnapshot | None, watch: Watch, where: str) -> str:
    """Цена для строки списка: со скидкой, без скидки или её отсутствие."""
    if snapshot is None:
        # по выбранному магазину замера нет — так и пишем, вместо того
        # чтобы молча подставить цену соседнего
        if watch.shop_key:
            return f"в {escape(where)} цены нет"
        return "цена ещё не замерена"

    price = link(format_price(snapshot.price, snapshot.currency), snapshot.url)

    if snapshot.cut <= 0:
        return f"{price} · скидки пока нет"

    text = f"{price} · −{snapshot.cut}%"
    if snapshot.regular_price is not None:
        was = format_price(snapshot.regular_price, snapshot.currency)
        text += f" (было {escape(was)})"
    return text


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
    batches = await SnapshotRepo(session).latest_batch(
        [w.game_id for w in watches], currency
    )
    preferred = parse_selection(user.preferred_shops)

    lines = [f"🔔 <b>Отслеживаю {len(watches)}</b>", ""]
    buttons: list[tuple[int, str]] = []

    for watch in watches:
        game = watch.game
        where = title_for(watch.shop_key) if watch.shop_key else "любой магазин"
        snapshot = pick_snapshot(batches.get(watch.game_id, []), watch, preferred)

        if watch.target_price is not None:
            goal = f"цель {format_price(watch.target_price, watch.currency)}"
        else:
            goal = "любое снижение"

        lines.append(
            f"• <b>{escape(game.title)}</b> — {price_text(snapshot, watch, where)}\n"
            f"  🏬 {escape(where)} · {escape(goal)}"
        )
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
