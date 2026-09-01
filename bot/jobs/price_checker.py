"""Фоновая проверка цен и уведомления.

Правила уведомлений разные для двух видов отслеживания:

* **С целевой ценой.** Пишем, когда цена опустилась не выше цели. Повторно —
  только если она упала ещё ниже, иначе бот будет слать одно и то же
  каждый час всю распродажу.
* **Без цели («любое снижение»).** `last_notified_price` работает как
  «последняя виденная цена»: пишем, когда стало дешевле, чем в прошлый раз,
  и подтягиваем ориентир вверх, если цена выросла. Иначе после одной
  глубокой скидки бот замолчал бы навсегда.
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import Watch
from bot.db.repo import ShopRepo, SnapshotRepo, WatchRepo
from bot.services.aggregator import Aggregator
from bot.services.models import Game, GameDetails, Offer
from bot.services.shops import filter_offers, parse_selection, shop_key
from bot.utils.formatting import escape, format_price, link
from bot.utils.logging import get_logger

log = get_logger(__name__)


def should_notify(watch: Watch, price: Decimal) -> bool:
    """Стоит ли писать пользователю про эту цену."""
    last = watch.last_notified_price

    if watch.target_price is not None:
        if price > watch.target_price:
            return False
        # цель достигнута: первый раз сообщаем всегда, дальше — только глубже
        return last is None or price < last

    # «любое снижение»: первый замер только запоминаем, без сообщения
    return last is not None and price < last


def next_watermark(watch: Watch, price: Decimal) -> Decimal | None:
    """Каким станет `last_notified_price` после проверки."""
    if watch.target_price is None:
        # ориентир идёт за ценой в обе стороны — это «последняя виденная»
        return price

    last = watch.last_notified_price
    if last is None:
        # пока цель не достигнута, отметку не ставим: иначе первое же
        # достижение цели окажется «не ниже предыдущего» и уведомление
        # проглотится
        return price if price <= watch.target_price else None
    return min(last, price)


def notification_text(game: Game, offer: Offer, watch: Watch) -> str:
    price = format_price(offer.sort_key, watch.currency)
    where = link(offer.shop.name, offer.url)

    lines = [
        f"📉 <b>{escape(game.title)}</b> подешевела!",
        "",
        f"Сейчас <b>{escape(price)}</b> — {where}",
    ]

    if offer.regular_price is not None:
        was = format_price(offer.regular_price, offer.currency)
        lines.append(f"Было {escape(was)} · −{offer.cut}%")

    if watch.target_price is not None:
        goal = format_price(watch.target_price, watch.currency)
        lines.append(f"Твоя цель была {escape(goal)}")

    return "\n".join(lines)


def pick_offer(details: GameDetails, watch: Watch) -> Offer | None:
    """За какой ценой следит это отслеживание.

    Магазин, выбранный при подписке, важнее общего фильтра из настроек:
    пользователь сказал «следить в Steam» именно для этой игры. Если
    выбранного магазина в выдаче нет, молчать нельзя — откатываемся на
    общий фильтр, иначе бот просто перестанет писать.
    """
    shops = [o for o in details.offers if not o.is_reseller]
    if not shops:
        return None

    if watch.shop_key:
        exact = [o for o in shops if shop_key(o.shop.name) == watch.shop_key]
        if exact:
            return min(exact, key=lambda o: o.sort_key)

    allowed = filter_offers(shops, parse_selection(watch.user.preferred_shops))
    return min(allowed, key=lambda o: o.sort_key) if allowed else None


async def _save_snapshots(
    session: AsyncSession, game_id: int, details: GameDetails
) -> None:
    shops = ShopRepo(session)
    snapshots = SnapshotRepo(session)
    for offer in details.offers:
        shop = await shops.get_or_create(
            source=offer.shop.source, external_id=offer.shop.id, name=offer.shop.name
        )
        await snapshots.add(
            game_id=game_id,
            shop_id=shop.id,
            price=offer.price,
            regular_price=offer.regular_price,
            cut=offer.cut,
            currency=offer.currency,
            url=offer.url,
        )


async def check_prices(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    aggregator: Aggregator,
) -> None:
    """Обходит вотчлист, обновляет замеры и рассылает уведомления."""
    async with sessionmaker() as session:
        watches = await WatchRepo(session).all_active()

    if not watches:
        log.info("price_check_skipped", reason="вотчлист пуст")
        return

    log.info("price_check_started", watches=len(watches))
    notified = 0

    # по игре может следить несколько человек — цены тянем один раз на игру
    for game_id in {w.game_id for w in watches}:
        group = [w for w in watches if w.game_id == game_id]
        notified += await _check_game(bot, sessionmaker, aggregator, group)

    log.info("price_check_finished", watches=len(watches), notified=notified)


async def _check_game(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    aggregator: Aggregator,
    watches: list[Watch],
) -> int:
    stored = watches[0].game
    game = Game(
        title=stored.title,
        itad_id=stored.itad_id,
        steam_appid=stored.steam_appid,
        cheapshark_id=stored.cheapshark_id,
        slug=stored.slug,
        image_url=stored.image_url,
    )

    # Цены тянем по одному разу на страну, а не на каждое отслеживание:
    # пять человек на одну игру давали пять одинаковых запросов.
    details_by_country: dict[str, GameDetails] = {}
    for country in {w.user.country for w in watches}:
        try:
            details_by_country[country] = await aggregator.game_details(
                game, country=country
            )
        except Exception as exc:
            log.warning("price_check_failed", game=stored.title, error=str(exc))

    # Замеры сохраняем полными, без пользовательских фильтров: на них
    # строится история цен и они общие для всех, кто следит за игрой.
    async with sessionmaker() as session:
        for details in details_by_country.values():
            await _save_snapshots(session, stored.id, details)
        await session.commit()

    sent = 0
    for watch in watches:
        found = details_by_country.get(watch.user.country)
        if found is None:
            continue  # источник не ответил по этой стране
        details = found

        offer = pick_offer(details, watch)
        if offer is None:
            continue

        async with sessionmaker() as session:
            fresh = await WatchRepo(session).get(watch.id)
            if fresh is None:
                continue  # пользователь успел удалить отслеживание

            price = offer.sort_key
            notify = should_notify(fresh, price)
            fresh.last_notified_price = next_watermark(fresh, price)
            await session.commit()

        if not notify or not watch.user.notify_enabled:
            continue

        try:
            await bot.send_message(
                watch.user.tg_id,
                notification_text(details.game, offer, watch),
                disable_web_page_preview=True,
            )
            sent += 1
            log.info("price_alert_sent", tg_id=watch.user.tg_id, game=stored.title)
        except TelegramForbiddenError:
            # пользователь заблокировал бота — молчим, это не наша ошибка
            log.info("alert_skipped_blocked", tg_id=watch.user.tg_id)
        except Exception as exc:
            log.warning("alert_failed", tg_id=watch.user.tg_id, error=str(exc))

    return sent
