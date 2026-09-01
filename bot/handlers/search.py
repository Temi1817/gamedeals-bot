"""Поиск игры и карточка с ценами по магазинам."""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repo import GameRepo, ShopRepo, SnapshotRepo, WatchRepo
from bot.keyboards.games import (
    GameCB,
    HistoryCB,
    WatchCB,
    WatchShopCB,
    WatchTargetCB,
    game_card_keyboard,
    search_keyboard,
    watch_shop_keyboard,
    watch_target_keyboard,
)
from bot.services.aggregator import Aggregator, _currency_for
from bot.services.models import GameDetails, Offer, PricePoint
from bot.services.shops import KNOWN_SHOPS, parse_selection, shop_key, title_for
from bot.utils import cards
from bot.utils.formatting import format_price
from bot.utils.logging import get_logger

log = get_logger(__name__)
router = Router(name="search")

SEARCH_LIMIT = 5
MIN_QUERY_LENGTH = 2

SEARCHING = "🔎 Ищу…"
TOO_SHORT = "Слишком короткий запрос — напиши хотя бы два символа."
NOT_FOUND_CARD = "Не смог собрать карточку: игра пропала из источников."


# --------------------------------------------------------------------------- #
# поиск
# --------------------------------------------------------------------------- #
@router.message(Command("find"))
async def cmd_find(
    message: Message, command: CommandObject, user: User, aggregator: Aggregator
) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Напиши, что искать: <code>/find Cyberpunk</code>\n"
            "Или просто пришли название без команды."
        )
        return
    await _run_search(message, query, aggregator)


@router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message, user: User, aggregator: Aggregator) -> None:
    """Любой текст без команды считаем поисковым запросом."""
    await _run_search(message, (message.text or "").strip(), aggregator, user.country)


async def _run_search(
    message: Message, query: str, aggregator: Aggregator, country: str = "KZ"
) -> None:
    if len(query) < MIN_QUERY_LENGTH:
        await message.answer(TOO_SHORT)
        return

    notice = await message.answer(SEARCHING)
    try:
        results = await aggregator.search_with_prices(
            query, country=country, limit=SEARCH_LIMIT
        )
    finally:
        await notice.delete()

    if not results:
        await message.answer(cards.search_results(query, 0))
        return

    await message.answer(
        cards.search_results(query, len(results)),
        reply_markup=search_keyboard(results, _currency_for(country, "KZT")),
    )
    log.info("search_done", query=query, found=len(results))


# --------------------------------------------------------------------------- #
# карточка
# --------------------------------------------------------------------------- #
@router.callback_query(GameCB.filter())
async def on_game_selected(
    callback: CallbackQuery,
    callback_data: GameCB,
    user: User,
    session: AsyncSession,
    aggregator: Aggregator,
) -> None:
    await callback.answer("Собираю цены…")

    game = await aggregator.resolve_game(callback_data.key)
    if game is None:
        await callback.answer(NOT_FOUND_CARD, show_alert=True)
        return

    details = await aggregator.game_details(
        game, country=user.country, shops=parse_selection(user.preferred_shops)
    )
    await _remember(session, details, user.country)

    watched = await _is_watched(session, user, details)
    text = cards.game_card(details, user.country)
    markup = game_card_keyboard(details.game, watched=watched)

    await _send_card(callback, details, text, markup)


async def _send_card(
    callback: CallbackQuery,
    details: GameDetails,
    text: str,
    markup: InlineKeyboardMarkup,
) -> None:
    """Шлём с обложкой, если подпись влезает в лимит Telegram."""
    if not isinstance(callback.message, Message):
        return

    image = details.game.image_url
    if image and len(text) <= cards.CAPTION_LIMIT:
        await callback.message.answer_photo(
            photo=image, caption=text, reply_markup=markup
        )
        return

    await callback.message.answer(
        text, reply_markup=markup, disable_web_page_preview=True
    )


# --------------------------------------------------------------------------- #
# сохранение замеров
# --------------------------------------------------------------------------- #
async def _remember(session: AsyncSession, details: GameDetails, country: str) -> None:
    """Кладёт игру и текущие цены в базу.

    На этих замерах потом строится «История цены» и работает джоба
    уведомлений, поэтому пишем при каждом показе карточки.
    """
    game = details.game
    stored = await GameRepo(session).upsert(
        title=game.title,
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
        slug=game.slug,
        image_url=game.image_url,
    )

    shops = ShopRepo(session)
    snapshots = SnapshotRepo(session)
    for offer in details.offers:
        shop = await shops.get_or_create(
            source=offer.shop.source, external_id=offer.shop.id, name=offer.shop.name
        )
        await snapshots.add(
            game_id=stored.id,
            shop_id=shop.id,
            price=offer.price,
            regular_price=offer.regular_price,
            cut=offer.cut,
            currency=offer.currency,
            url=offer.url,
        )


async def _is_watched(session: AsyncSession, user: User, details: GameDetails) -> bool:
    stored = await GameRepo(session).find(
        itad_id=details.game.itad_id,
        steam_appid=details.game.steam_appid,
        cheapshark_id=details.game.cheapshark_id,
    )
    if stored is None:
        return False
    watches = await WatchRepo(session).for_user(user.id)
    return any(w.game_id == stored.id for w in watches)


# --------------------------------------------------------------------------- #
# история цены
# --------------------------------------------------------------------------- #
@router.callback_query(HistoryCB.filter())
async def on_history(
    callback: CallbackQuery,
    callback_data: HistoryCB,
    user: User,
    session: AsyncSession,
    aggregator: Aggregator,
) -> None:
    await callback.answer()

    game = await aggregator.resolve_game(callback_data.key)
    if game is None or not isinstance(callback.message, Message):
        return

    stored = await GameRepo(session).find(
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
    )
    currency = _currency_for(user.country, "KZT")

    # Наши замеры уже в валюте региона и сняты у самих витрин, поэтому
    # идут как точные. Историю за годы добавит ITAD, но международную.
    own: list[PricePoint] = []
    if stored is not None:
        for snapshot in await SnapshotRepo(session).history(stored.id, currency):
            if snapshot.cut > 0:
                own.append(
                    PricePoint(
                        at=snapshot.checked_at,
                        price=snapshot.price,
                        currency=snapshot.currency,
                        cut=snapshot.cut,
                        shop=snapshot.shop.name if snapshot.shop else None,
                        exact=True,
                    )
                )

    points = await aggregator.price_history(game, country=user.country, own=own)

    await callback.message.answer(
        cards.price_history(game.title, points, currency),
        disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------- #
# отслеживание с карточки
# --------------------------------------------------------------------------- #
@router.callback_query(WatchCB.filter())
async def on_watch_pressed(
    callback: CallbackQuery,
    callback_data: WatchCB,
    user: User,
    session: AsyncSession,
    aggregator: Aggregator,
) -> None:
    """Уже следим — снимаем; иначе предлагаем выбрать цель кнопками."""
    game = await aggregator.resolve_game(callback_data.key)
    if game is None:
        await callback.answer(NOT_FOUND_CARD, show_alert=True)
        return

    stored = await GameRepo(session).find(
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
    )
    watches = WatchRepo(session)
    already = None
    if stored is not None:
        existing = await watches.for_user(user.id)
        already = next((w for w in existing if w.game_id == stored.id), None)

    if already is not None:
        await watches.remove(already.id, user.id)
        await callback.answer("Больше не отслеживаю")
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(
                reply_markup=game_card_keyboard(game, watched=False)
            )
        return

    await callback.answer()
    if not isinstance(callback.message, Message):
        return

    # Сначала магазин: следить за ценой там, где игры нет, бессмысленно,
    # поэтому список берём из самой карточки.
    details = await aggregator.game_details(
        game, country=user.country, shops=parse_selection(user.preferred_shops)
    )
    await callback.message.edit_reply_markup(
        reply_markup=watch_shop_keyboard(game, _watchable_shops(details))
    )


def _offer_in_shop(details: GameDetails, shop: str) -> Offer | None:
    """Предложение конкретного магазина из карточки."""
    if not shop:
        return None
    for offer in details.offers:
        if not offer.is_reseller and shop_key(offer.shop.name) == shop:
            return offer
    return None


def _watchable_shops(details: GameDetails) -> list[tuple[str, str]]:
    """Магазины, где игра действительно продаётся.

    Только известные: у незнакомых ключ строится из названия и может не
    влезть в callback_data. Реселлеров не предлагаем — это не покупка
    в магазине.
    """
    known = {shop.key for shop in KNOWN_SHOPS}
    seen: dict[str, str] = {}
    for offer in details.offers:
        if offer.is_reseller:
            continue
        key = shop_key(offer.shop.name)
        if key in known and key not in seen:
            seen[key] = title_for(key)
    return list(seen.items())


@router.callback_query(WatchShopCB.filter())
async def on_watch_shop(
    callback: CallbackQuery,
    callback_data: WatchShopCB,
    aggregator: Aggregator,
) -> None:
    """Магазин выбран — спрашиваем, какую цену ждать."""
    game = await aggregator.resolve_game(callback_data.key)
    if game is None:
        await callback.answer(NOT_FOUND_CARD, show_alert=True)
        return

    where = title_for(callback_data.shop) if callback_data.shop else "любом магазине"
    await callback.answer(f"Слежу в {where}")

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=watch_target_keyboard(game, callback_data.shop)
        )


@router.callback_query(WatchTargetCB.filter())
async def on_watch_target(
    callback: CallbackQuery,
    callback_data: WatchTargetCB,
    user: User,
    session: AsyncSession,
    aggregator: Aggregator,
) -> None:
    """Ставит отслеживание с целью, выбранной кнопкой.

    Процент считается от текущей лучшей цены, поэтому цель получается
    осмысленной без набора суммы руками.
    """
    game = await aggregator.resolve_game(callback_data.key)
    if game is None:
        await callback.answer(NOT_FOUND_CARD, show_alert=True)
        return

    currency = _currency_for(user.country, "KZT")
    target: Decimal | None = None

    if callback_data.percent:
        details = await aggregator.game_details(
            game, country=user.country, shops=parse_selection(user.preferred_shops)
        )
        # процент считаем от цены выбранного магазина: цель «−30% от
        # Steam» и «−30% от самого дешёвого» — разные суммы
        base = _offer_in_shop(details, callback_data.shop) or details.best_offer
        if base is not None:
            share = Decimal(100 - callback_data.percent) / Decimal(100)
            target = (base.sort_key * share).quantize(Decimal("1"))

    games = GameRepo(session)
    stored = await games.find(
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
    ) or await games.upsert(
        title=game.title,
        itad_id=game.itad_id,
        steam_appid=game.steam_appid,
        cheapshark_id=game.cheapshark_id,
        slug=game.slug,
        image_url=game.image_url,
    )

    await WatchRepo(session).add(
        user_id=user.id,
        game_id=stored.id,
        target_price=target,
        currency=currency,
        notify_any_drop=target is None,
        shop_key=callback_data.shop,
    )

    where = f" в {title_for(callback_data.shop)}" if callback_data.shop else ""
    if target is not None:
        note = f"Жду {format_price(target, currency)}{where}"
    else:
        note = f"Напишу при любом снижении{where}"
    await callback.answer(f"🔔 {note}")

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=game_card_keyboard(game, watched=True)
        )
    log.info("watch_added", tg_id=user.tg_id, game=game.title, target=str(target))
