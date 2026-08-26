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

# Сколько магазинов показываем помимо лучшего. У популярных игр их бывает
# полтора десятка с почти одинаковой ценой — сплошной стеной это не читается.
SHOPS_SHOWN = 5

# Магазины, у которых мы спрашиваем цену напрямую и потому знаем её точно
# для региона пользователя. Остальные приходят от ITAD по международному
# прайсу: региональных цен для Казахстана у него нет.
VERIFIED_SOURCES = frozenset({"steam", "gog", "epic"})


def _is_exact(offer: Offer) -> bool:
    """Знаем ли мы цену этого магазина точно для региона пользователя."""
    return offer.shop.source in VERIFIED_SOURCES


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


def _amount(offer: Offer) -> str:
    """Сумма со значком приблизительности, если цена не точная.

    Знак ≈ несёт смысл: у такого магазина мы знаем только международный
    прайс, и на месте цена может оказаться другой.
    """
    if offer.is_free:
        return "<b>бесплатно</b> 🎉"
    price = escape(_display_price(offer))
    return f"<b>{price}</b>" if _is_exact(offer) else f"≈<b>{price}</b>"


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def offer_line(offer: Offer, position: int | None = None) -> str:
    """Строка магазина: было → стало и скидка."""
    marker = ""
    if position is not None:
        marker = MEDALS[position] if position < len(MEDALS) else "▫️"

    shop = link(offer.shop.name, offer.url)
    head = f"{marker} <b>{shop}</b>" if marker else f"<b>{shop}</b>"

    money = [_amount(offer)]
    if (was := _display_regular(offer)) is not None:
        money.insert(0, f"<s>{escape(was)}</s> →")
    if cut := format_discount(offer.cut):
        money.append(f"· <b>{cut}</b>")

    line = head + "\n   " + " ".join(money)

    # Валюту магазина показываем только там, где цена точная: иначе в
    # скобках оказалась бы международная сумма, которую никто не спишет.
    if offer.converted_price is not None and _is_exact(offer):
        native = format_price(offer.price, offer.currency)
        line += f"  <i>({escape(native)})</i>"
    if offer.is_reseller:
        line += " 🔑"

    return line


def _hero(offer: Offer) -> list[str]:
    """Крупный блок с лучшей ценой — то, ради чего открывают карточку."""
    lines = ["💰 <b>Лучшая цена</b>", f"   {_amount(offer)}"]

    tail = [link(offer.shop.name, offer.url)]
    if cut := format_discount(offer.cut):
        tail.append(cut)
    if (was := _display_regular(offer)) is not None:
        tail.append(f"было <s>{escape(was)}</s>")
    lines.append("   " + " · ".join(tail))

    if offer.converted_price is not None and _is_exact(offer):
        native = format_price(offer.price, offer.currency)
        lines.append(f"   <i>спишут {escape(native)}</i>")
    return lines


def _rest_line(offers: list[Offer]) -> str:
    """Свёрнутый хвост списка: сколько магазинов и от какой цены."""
    cheapest = min(offers, key=lambda o: o.sort_key)
    price = escape(_display_price(cheapest))
    approx = "" if _is_exact(cheapest) else "≈"
    word = _plural(len(offers), "магазин", "магазина", "магазинов")
    return f"   <i>и ещё {len(offers)} {word} — от {approx}{price}</i>"


def game_card(details: GameDetails, country: str = "KZ") -> str:
    """Карточка игры: лучшая цена, магазины по возрастанию, минимум, вердикт."""
    game = details.game
    lines = [f"🎮 <b>{escape(game.title)}</b>"]

    if not details.offers:
        lines.append("")
        lines.append("😕 Цен по этой игре сейчас нет.")
        lines.append("Возможно, она ещё не вышла или не продаётся в твоём регионе.")
        return "\n".join(lines)

    shop_offers = [o for o in details.offers if not o.is_reseller]
    reseller_offers = [o for o in details.offers if o.is_reseller]

    best = details.best_offer
    if best is not None:
        lines.append(RULE)
        lines.extend(_hero(best))

    others = [o for o in shop_offers if o is not best]
    if others:
        lines.append(RULE)
        lines.append("🏬 <b>Другие магазины</b>")
        lines.append("")
        for index, offer in enumerate(others[:SHOPS_SHOWN]):
            lines.append(offer_line(offer, index + 1))
        if len(others) > SHOPS_SHOWN:
            lines.append("")
            lines.append(_rest_line(others[SHOPS_SHOWN:]))

    if reseller_offers:
        lines.append("")
        lines.append("🔑 <b>Ключи у реселлеров</b>")
        lines.append("")
        for offer in reseller_offers[:2]:
            lines.append(offer_line(offer))

    low = details.historical_low
    if low is not None:
        lines.append(RULE)
        note = " · <i>международный</i>" if low.converted else ""
        where = f" · {escape(low.shop)}" if low.shop else ""
        when = f" · {format_date(low.at)}" if low.at else ""
        lines.append("📉 <b>Минимум за всё время</b>")
        lines.append(
            f"   {escape(format_price(low.price, low.currency))}{where}{when}{note}"
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


def _price_notes(offers: list[Offer]) -> list[str]:
    """Сноска о точности цен.

    Список точных магазинов собирается из самой карточки, а не пишется
    текстом: иначе он разъезжается с кодом, стоит подключить ещё один
    магазин напрямую.
    """
    shops = [o for o in offers if not o.is_reseller]
    exact = sorted({o.shop.name for o in shops if _is_exact(o)})

    if not any(not _is_exact(o) for o in shops):
        return []

    if exact:
        names = ", ".join(escape(name) for name in exact)
        return [
            "",
            f"<i>✅ Точная цена: {names}\n"
            "≈ — международный прайс, в магазине может быть дешевле</i>",
        ]
    return ["", "<i>≈ — международный прайс, в магазине может быть дешевле</i>"]


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

        price = _amount(offer)
        if (was := _display_regular(offer)) is not None:
            price = f"<s>{escape(was)}</s> → {price}"

        cut = format_discount(offer.cut)
        head = f"<b>{cut}</b> · {title}" if cut else f"<b>{title}</b>"

        lines.append(head)
        lines.append(f"   {price} · {escape(offer.shop.name)}")
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


TOP_TITLES = {
    "steam": "🎮 <b>Топ продаж Steam</b>",
    "epic": "🟣 <b>Топ продаж Epic</b>",
    "all": "🌐 <b>Ждут скидку</b>",
    "waitlisted": "🌐 <b>Ждут скидку</b>",
}

TOP_HINTS = {
    "steam": "Что покупают в Steam прямо сейчас",
    "epic": "Что покупают в Epic Games Store",
    "all": "Игры, которых больше всего ждут по скидке",
    "waitlisted": "Игры, которых больше всего ждут по скидке",
}


def top_list(deals: list[Deal], kind: str, currency: str) -> str:
    """Рейтинг игр с текущей лучшей ценой."""
    header = TOP_TITLES.get(kind, TOP_TITLES["all"])
    hint = TOP_HINTS.get(kind, "")

    if not deals:
        return f"{header}\n\n😕 Рейтинг сейчас недоступен, попробуй позже."

    lines = [header, f"<i>{hint}</i>", RULE, ""]
    for index, deal in enumerate(deals, start=1):
        offer = deal.offer
        place = MEDALS[index - 1] if index <= len(MEDALS) else f"{index}."
        title = link(deal.game.title, offer.url)

        price = _amount(offer)
        if cut := format_discount(offer.cut):
            price += f" · <b>{cut}</b>"

        lines.append(f"{place} <b>{title}</b>")
        lines.append(f"   {price} · {escape(offer.shop.name)}")
        lines.append("")

    return "\n".join(lines).rstrip()
