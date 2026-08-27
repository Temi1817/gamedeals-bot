"""Форматирование цен и текстов сообщений."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple

NBSP = " "

class _Format(NamedTuple):
    symbol: str
    digits: int  # знаков после запятой
    suffix: bool  # символ идёт после числа
    decimal_sep: str


# Тенге, рубли и гривны в магазинах всегда целые — копейки в карточке мешают.
_CURRENCY: dict[str, _Format] = {
    "KZT": _Format("₸", 0, True, ","),
    "RUB": _Format("₽", 0, True, ","),
    "UAH": _Format("₴", 0, True, ","),
    "USD": _Format("$", 2, False, "."),
    "GBP": _Format("£", 2, False, "."),
    "EUR": _Format("€", 2, True, ","),
}

_DEFAULT_FORMAT = _Format("", 2, True, ",")


def format_price(amount: Decimal | int | float | None, currency: str = "KZT") -> str:
    """`Decimal('17999.00'), 'KZT'` → `'17 999 ₸'`; `59.99, 'USD'` → `'$59.99'`."""
    if amount is None:
        return "—"
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    code = currency.upper()
    fmt = _CURRENCY.get(code) or _DEFAULT_FORMAT._replace(symbol=code)

    value = amount.quantize(Decimal(1).scaleb(-fmt.digits))

    # f-string даёт разделители на английский манер — приводим к нужным
    whole, _, frac = f"{value:,.{fmt.digits}f}".partition(".")
    whole = whole.replace(",", NBSP)
    number = f"{whole}{fmt.decimal_sep}{frac}" if frac else whole

    return f"{number}{NBSP}{fmt.symbol}" if fmt.suffix else f"{fmt.symbol}{number}"


def format_discount(cut: int) -> str:
    """`75` → `'−75%'`. Ноль и мусорные значения дают пустую строку."""
    return f"−{cut}%" if cut and cut > 0 else ""


def format_date(value: datetime | None) -> str:
    """`datetime(2025, 12, 20)` → `'20.12.2025'`."""
    return value.strftime("%d.%m.%Y") if value else "—"


def _years_since(moment: datetime) -> float:
    """Сколько лет прошло. Наивную дату считаем UTC — история приходит
    с зоной, но защищаться от чужих данных дешевле, чем ловить падение."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).days / 365.25


def escape(text: str | None) -> str:
    """Экранирование под `parse_mode=HTML` в Telegram."""
    return html.escape(text or "", quote=False)


def link(text: str, url: str | None) -> str:
    """HTML-ссылка; без URL возвращает просто экранированный текст."""
    if not url:
        return escape(text)
    return f'<a href="{html.escape(url, quote=True)}">{escape(text)}</a>'


def percent_diff(current: Decimal, baseline: Decimal) -> int:
    """На сколько процентов `current` отличается от `baseline` (округлённо).

    Отрицательное — текущая цена ниже базовой.
    """
    if baseline <= 0:
        return 0
    return int(((current - baseline) / baseline * 100).to_integral_value())


# После стольких лет минимум перестаёт быть ориентиром: у Grand Theft
# Auto V он поставлен ключевым реселлером в 2018 году, и сравнение с ним
# давало «дороже минимума на 1609%» — формально верно, на деле бесполезно.
STALE_LOW_YEARS = 3


def verdict(
    current: Decimal | None,
    historical_low: Decimal | None,
    currency: str = "KZT",
    low_at: datetime | None = None,
) -> str:
    """Вердикт «стоит ли брать сейчас» — сравнение с историческим минимумом."""
    if current is None:
        return "Нет данных о цене."
    if historical_low is None or historical_low <= 0:
        return "Исторического минимума пока нет — сравнивать не с чем."

    if low_at is not None and _years_since(low_at) >= STALE_LOW_YEARS:
        return (
            f"📅 Минимум {format_price(historical_low, currency)} был "
            f"{format_date(low_at)} — с тех пор цены изменились, "
            "сравнивать с ним смысла мало."
        )

    diff = percent_diff(current, historical_low)
    low = format_price(historical_low, currency)

    if diff <= 0:
        return f"🔥 Это исторический минимум ({low}) или ниже. Лучше не будет."
    if diff <= 10:
        return f"✅ Всего +{diff}% к минимуму ({low}) — хорошая цена, можно брать."
    if diff <= 30:
        return f"🤔 Дороже минимума на {diff}% ({low}). Терпимо, но бывало дешевле."
    return f"⏳ Дороже минимума на {diff}% ({low}) — лучше подождать распродажу."
