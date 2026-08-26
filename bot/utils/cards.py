"""Рендер сообщений бота: карточка игры, результаты поиска, история цен."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bot.services.models import Deal, FreeGame, GameDetails, Offer
from bot.utils.formatting import (
    escape,
    format_date,
    format_discount,
    format_price,
    link,
    verdict,
)

# Подпись к фото в Telegram ограничена 1024 символами
CAPTION_LIMIT = 1024

MEDALS = ("🥇", "🥈", "🥉")
RULE = "━━━━━━━━━━━━━━━"


def _display_price(offer: Offer) -> str:
    """Цена в валюте региона, если пересчёт есть, иначе родная."""
    if offer.converted_price is not None and offer.converted_currency:
        return format_price(offer.converted_price, offer.converted_currency)
    return format_price(offer.price, offer.currency)


def _display_regular(offer: Offer) -> str | None:
    """Старая цена в той же валюте, что и текущая."""
    if offer.regular_price is None:
        return None
    if offer.converted_regular_price is not None and offer.converted_currency:
        return format_price(offer.converted_regular_price, offer.converted_currency)
    return format_price(offer.regular_price, offer.currency)


def offer_line(offer: Offer, position: int | None = None) -> str:
    """Строка магазина: было → стало, скидка, и родная валюта, если считали."""
    marker = ""
    if position is not None:
        marker = MEDALS[position] if position < len(MEDALS) else "▫️"

    shop = link(offer.shop.name, offer.url)
    head = f"{marker} <b>{shop}</b>" if marker else f"<b>{shop}</b>"

    if offer.is_free:
        price = "<b>бесплатно</b> 🎉"
    else:
        price = f"<b>{escape(_display_price(offer))}</b>"

    money = [price]
    if (was := _display_regular(offer)) is not None:
        money.insert(0, f"<s>{escape(was)}</s> →")
    if cut := format_discount(offer.cut):
        money.append(f"<b>{cut}</b>")

    line = f"{head}\n   {' '.join(money)}"

    # Если цену пересчитали, показываем и исходную — чтобы было видно,
    # сколько магазин спишет на самом деле.
    if offer.converted_price is not None:
        native = format_price(offer.price, offer.currency)
        line += f"  <i>({escape(native)})</i>"
    if offer.is_reseller:
        line += " 🔑"

    return line


def game_card(details: GameDetails, country: str = "KZ") -> str:
    """Карточка игры: магазины по возрастанию цены, минимум и вердикт."""
    game = details.game
    lines = [f"🎮 <b>{escape(game.title)}</b>"]

    if not details.offers:
        lines.append("")
        lines.append("😕 Цен по этой игре сейчас нет.")
        lines.append("Возможно, она ещё не вышла или не продаётся в твоём регионе.")
        return "\n".join(lines)

    shop_offers = [o for o in details.offers if not o.is_reseller]
    reseller_offers = [o for o in details.offers if o.is_reseller]

    lines.append(RULE)
    lines.append("🏬 <b>Где купить</b>")
    lines.append("")
    for index, offer in enumerate(shop_offers):
        lines.append(offer_line(offer, index))

    if reseller_offers:
        lines.append("")
        lines.append("🔑 <b>Ключи у реселлеров</b>")
        lines.append("")
        for offer in reseller_offers[:3]:
            lines.append(offer_line(offer))

    best = details.best_offer
    low = details.historical_low

    if low is not None:
        lines.append(RULE)
        when = f" · {format_date(low.at)}" if low.at else ""
        where = f" · {escape(low.shop)}" if low.shop else ""
        lines.append(
            f"📉 <b>Минимум за всё время</b>\n"
            f"   {escape(format_price(low.price, low.currency))}{where}{when}"
        )

    if best is not None:
        lines.append("")
        lines.append(
            verdict(
                best.sort_key,
                low.price if low is not None else None,
                low.currency if low is not None else best.currency,
            )
        )

    lines.extend(_price_notes(details.offers))
    return "\n".join(lines)


# Магазины, у которых мы спрашиваем цену напрямую и потому знаем её точно
# для региона пользователя. Остальные приходят от ITAD по международному
# прайсу: у ITAD нет региональных цен для Казахстана.
VERIFIED_SOURCES = frozenset({"steam", "gog", "epic"})


def _price_notes(offers: list[Offer]) -> list[str]:
    """Сноски под карточкой: что за цифры в скобках и чему верить.

    Список точных магазинов собирается из самой карточки, а не пишется
    текстом: иначе он разъезжается с кодом, стоит подключить ещё один
    магазин напрямую.
    """
    notes: list[str] = []
    shops = [o for o in offers if not o.is_reseller]

    if any(o.converted_price is not None for o in offers):
        notes.append("<i>В скобках — сумма, которую спишет магазин.</i>")

    exact = sorted({o.shop.name for o in shops if o.shop.source in VERIFIED_SOURCES})
    international = [o for o in shops if o.shop.source not in VERIFIED_SOURCES]

    if international and exact:
        names = ", ".join(escape(name) for name in exact)
        notes.append(
            f"<i>✅ Точные цены для твоего региона: {names}.\n"
            "⚠️ Остальные — международный прайс, на месте может быть "
            "дешевле.</i>"
        )
    elif international:
        notes.append(
            "<i>⚠️ Цены показаны по международному прайсу — в самом магазине "
            "для Казахстана может быть дешевле.</i>"
        )

    return ["", *notes] if notes else []


def search_results(query: str, count: int) -> str:
    if not count:
        return (
            f"🔍 По запросу «<b>{escape(query)}</b>» ничего не нашлось.\n\n"
            "💡 Попробуй оригинальное название на английском — "
            "магазины находят игры по нему точнее."
        )
    return (
        f"🔍 Нашёл <b>{count}</b> по запросу «<b>{escape(query)}</b>»\n\n"
        "👇 Выбери игру, чтобы увидеть цены:"
    )


def price_history(
    title: str,
    points: list[tuple[datetime, Decimal, str]],
    currency: str,
) -> str:
    """Текстовый график: столбики из блоков, самая дешёвая точка — короткая."""
    header = f"📉 <b>История цены</b>\n🎮 {escape(title)}"

    if not points:
        return (
            f"{header}\n{RULE}\n\n"
            "⏳ Я слежу за этой игрой недавно и пока не накопил замеров.\n"
            "Загляни через пару дней — история появится сама."
        )

    prices = [p for _, p, _ in points]
    low, high = min(prices), max(prices)
    span = high - low

    lines = [header, RULE, ""]
    for moment, price, _ in points[-14:]:
        # без разброса цен все столбики одинаковой средней длины
        filled = int((price - low) / span * 10) if span > 0 else 5
        bar = "█" * max(1, filled) + "░" * (10 - max(1, filled))
        mark = "🔻" if price == low else "  "
        lines.append(
            f"<code>{format_date(moment)} {bar}</code> "
            f"{escape(format_price(price, currency))} {mark}"
        )

    lines.append("")
    lines.append(f"🔻 Минимум по замерам: <b>{escape(format_price(low, currency))}</b>")
    return "\n".join(lines)


def deals_list(deals: list[Deal], page: int, currency: str) -> str:
    """Список скидок для /deals и фильтра по цене."""
    if not deals:
        return (
            "🤷 Ничего не нашлось под эти условия.\n\n"
            "💡 Попробуй поднять потолок цены или снизить порог скидки."
        )

    lines = [f"🔥 <b>Скидки</b> · страница {page + 1}", RULE, ""]
    for deal in deals:
        offer = deal.offer
        title = link(deal.game.title, offer.url)
        cut = format_discount(offer.cut)

        if offer.is_free:
            price = "<b>бесплатно</b> 🎉"
        else:
            price = f"<b>{escape(_display_price(offer))}</b>"
        if (was := _display_regular(offer)) is not None:
            price = f"<s>{escape(was)}</s> → {price}"

        lines.append(f"<b>{cut}</b> · <b>{title}</b>")
        lines.append(f"   {price}  ·  {escape(offer.shop.name)}")
        lines.append("")

    return "\n".join(lines).rstrip()


def free_games(games: list[FreeGame]) -> str:
    """Раздачи Epic: сначала то, что можно забрать сейчас."""
    if not games:
        return (
            "🎁 <b>Раздачи</b>\n\n"
            "Сейчас ничего не раздают. Загляни позже — "
            "Epic обновляет раздачи по четвергам."
        )

    now = datetime.now(UTC)
    active = [g for g in games if not g.upcoming]
    upcoming = [g for g in games if g.upcoming]

    lines: list[str] = []

    if active:
        lines.append("🎁 <b>Забирай бесплатно</b>")
        lines.append(RULE)
        lines.append("")
        for game in active:
            lines.append(_free_line(game, now))
            lines.append("")

    if upcoming:
        lines.append("🔜 <b>Скоро раздадут</b>")
        lines.append(RULE)
        lines.append("")
        for game in upcoming:
            lines.append(_free_line(game, now))
            lines.append("")

    return "\n".join(lines).rstrip()


def _free_line(game: FreeGame, now: datetime) -> str:
    line = f"🎮 <b>{link(game.title, game.url)}</b>"

    details = []
    if game.original_price is not None and game.original_price > 0:
        was = format_price(game.original_price, game.currency)
        details.append(f"<s>{escape(was)}</s>")

    if game.upcoming and game.starts_at is not None:
        details.append(f"с {format_date(game.starts_at)}")
    elif game.ends_at is not None:
        left = game.ends_at - now
        hours = int(left.total_seconds() // 3600)
        if hours <= 0:
            details.append("⚠️ вот-вот закончится")
        elif hours < 24:
            details.append(f"⏳ осталось {hours} ч")
        else:
            details.append(f"до {format_date(game.ends_at)}")

    if details:
        line += "\n   " + "  ·  ".join(details)
    return line
