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


def offer_line(offer: Offer, position: int | None = None) -> str:
    """Одна строка магазина: цена, пересчёт, скидка, старая цена.

    Пересчёт показываем только когда валюта магазина отличается от валюты
    региона — иначе получится «17 999 ₸ ≈17 999 ₸».
    """
    marker = ""
    if position is not None:
        marker = MEDALS[position] if position < len(MEDALS) else f"{position + 1}."

    shop = link(offer.shop.name, offer.url)
    price = f"<b>{escape(format_price(offer.price, offer.currency))}</b>"

    parts = [p for p in (marker, shop, "—", price) if p]

    if offer.converted_price is not None and offer.converted_currency:
        approx = format_price(offer.converted_price, offer.converted_currency)
        parts.append(f"≈{escape(approx)}")

    if cut := format_discount(offer.cut):
        parts.append(cut)

    if offer.regular_price is not None:
        was = format_price(offer.regular_price, offer.currency)
        parts.append(f"<s>{escape(was)}</s>")

    if offer.is_reseller:
        parts.append("🔑")

    return " ".join(parts)


def game_card(details: GameDetails, country: str = "KZ") -> str:
    """Карточка игры: магазины по возрастанию цены, минимум и вердикт."""
    game = details.game
    lines = [f"🎮 <b>{escape(game.title)}</b>", ""]

    if not details.offers:
        lines.append("Цен по этой игре сейчас нет — возможно, она ещё не вышла")
        lines.append("или не продаётся в твоём регионе.")
        return "\n".join(lines)

    shop_offers = [o for o in details.offers if not o.is_reseller]
    reseller_offers = [o for o in details.offers if o.is_reseller]

    lines.append("<b>Где купить</b>")
    for index, offer in enumerate(shop_offers):
        lines.append(offer_line(offer, index))

    if reseller_offers:
        lines.append("")
        lines.append("<b>Ключи у реселлеров</b> 🔑")
        for offer in reseller_offers[:3]:
            lines.append(offer_line(offer))

    best = details.best_offer
    low = details.historical_low

    if low is not None:
        lines.append("")
        when = f", {format_date(low.at)}" if low.at else ""
        where = f" в {escape(low.shop)}" if low.shop else ""
        lines.append(
            f"📉 <b>Минимум за всё время:</b> "
            f"{escape(format_price(low.price, low.currency))}{where}{when}"
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

    if _has_converted(details.offers):
        lines.append("")
        lines.append(
            "<i>≈ — пересчёт по курсу. Магазин спишет сумму "
            "в своей валюте.</i>"
        )

    return "\n".join(lines)


def _has_converted(offers: list[Offer]) -> bool:
    return any(o.converted_price is not None for o in offers)


def search_results(query: str, count: int) -> str:
    if not count:
        return (
            f"🔍 По запросу «{escape(query)}» ничего не нашлось.\n\n"
            "Попробуй написать название по-английски — магазины "
            "находят игры точнее по оригинальному названию."
        )
    return f"🔍 Нашёл по запросу «{escape(query)}». Выбери игру:"


def price_history(
    title: str,
    points: list[tuple[datetime, Decimal, str]],
    currency: str,
) -> str:
    """Текстовый график: столбики из блоков, самая дешёвая точка — самая короткая."""
    header = f"📉 <b>История цены — {escape(title)}</b>"

    if not points:
        return (
            f"{header}\n\n"
            "Я слежу за этой игрой недавно и пока не накопил замеров.\n"
            "Загляни через пару дней — история появится сама."
        )

    prices = [p for _, p, _ in points]
    low, high = min(prices), max(prices)
    span = high - low

    lines = [header, ""]
    for moment, price, _ in points[-14:]:
        # без разброса цен все столбики одинаковой средней длины
        filled = int((price - low) / span * 10) if span > 0 else 5
        bar = "█" * max(1, filled) + "░" * (10 - max(1, filled))
        lines.append(
            f"<code>{format_date(moment):>10} {bar}</code> "
            f"{escape(format_price(price, currency))}"
        )

    lines.append("")
    lines.append(f"Минимум по моим замерам: <b>{escape(format_price(low, currency))}</b>")
    return "\n".join(lines)


def deals_list(deals: list[Deal], page: int, currency: str) -> str:
    """Список скидок для /deals и фильтра по цене."""
    if not deals:
        return "Ничего не нашлось под эти условия. Попробуй поднять потолок цены."

    lines = [f"🔥 <b>Скидки</b> — страница {page + 1}", ""]
    for deal in deals:
        offer = deal.offer
        price = format_price(offer.sort_key, currency)
        title = link(deal.game.title, offer.url)
        cut = format_discount(offer.cut)
        shop = escape(offer.shop.name)
        lines.append(f"{cut} {title} — <b>{escape(price)}</b> · {shop}")
    return "\n".join(lines)


def free_games(games: list[FreeGame]) -> str:
    """Раздачи Epic: сначала то, что можно забрать сейчас."""
    if not games:
        return "🎁 Сейчас бесплатных раздач нет. Загляни позже."

    now = datetime.now(UTC)
    active = [g for g in games if not g.upcoming]
    upcoming = [g for g in games if g.upcoming]

    lines: list[str] = []

    if active:
        lines.append("🎁 <b>Забрать бесплатно прямо сейчас</b>")
        lines.append("")
        for game in active:
            lines.append(_free_line(game, now))

    if upcoming:
        if active:
            lines.append("")
        lines.append("🔜 <b>Скоро раздадут</b>")
        lines.append("")
        for game in upcoming:
            lines.append(_free_line(game, now))

    return "\n".join(lines)


def _free_line(game: FreeGame, now: datetime) -> str:
    title = link(game.title, game.url)
    parts = [f"• {title}"]

    if game.original_price is not None and game.original_price > 0:
        was = format_price(game.original_price, game.currency)
        parts.append(f"<s>{escape(was)}</s>")

    if game.upcoming and game.starts_at is not None:
        parts.append(f"— с {format_date(game.starts_at)}")
    elif game.ends_at is not None:
        left = game.ends_at - now
        hours = int(left.total_seconds() // 3600)
        if hours <= 0:
            parts.append("— вот-вот закончится")
        elif hours < 24:
            parts.append(f"— осталось {hours} ч")
        else:
            parts.append(f"— до {format_date(game.ends_at)}")

    return " ".join(parts)
