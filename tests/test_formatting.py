"""Форматирование цен и вердикт по цене."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from bot.utils.formatting import (
    NBSP,
    escape,
    format_date,
    format_discount,
    format_price,
    link,
    percent_diff,
    verdict,
)


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (Decimal("17999.00"), "KZT", f"17{NBSP}999{NBSP}₸"),
        (Decimal("999"), "KZT", f"999{NBSP}₸"),
        (Decimal("0"), "KZT", f"0{NBSP}₸"),
        (Decimal("1234567"), "KZT", f"1{NBSP}234{NBSP}567{NBSP}₸"),
        # тенге округляем до целых — тиыны в карточке только мешают
        (Decimal("17999.60"), "KZT", f"18{NBSP}000{NBSP}₸"),
        (Decimal("59.99"), "USD", "$59.99"),
        (Decimal("5"), "USD", "$5.00"),
        (Decimal("1299.5"), "USD", "$1,299.50".replace(",", NBSP)),
        (Decimal("19.99"), "EUR", f"19,99{NBSP}€"),
        (Decimal("700"), "RUB", f"700{NBSP}₽"),
        # неизвестная валюта: код вместо символа, два знака
        (Decimal("42.5"), "PLN", f"42,50{NBSP}PLN"),
    ],
)
def test_format_price(amount: Decimal, currency: str, expected: str) -> None:
    assert format_price(amount, currency) == expected


def test_format_price_accepts_plain_numbers() -> None:
    assert format_price(5000, "KZT") == f"5{NBSP}000{NBSP}₸"
    assert format_price(59.99, "USD") == "$59.99"


def test_format_price_none_is_dash() -> None:
    assert format_price(None) == "—"


@pytest.mark.parametrize(
    ("cut", "expected"),
    [(75, "−75%"), (1, "−1%"), (0, ""), (-5, "")],
)
def test_format_discount(cut: int, expected: str) -> None:
    assert format_discount(cut) == expected


def test_format_date() -> None:
    assert format_date(datetime(2025, 12, 20)) == "20.12.2025"
    assert format_date(None) == "—"


@pytest.mark.parametrize(
    ("current", "baseline", "expected"),
    [
        (Decimal("100"), Decimal("100"), 0),
        (Decimal("140"), Decimal("100"), 40),
        (Decimal("95"), Decimal("100"), -5),
        (Decimal("100"), Decimal("0"), 0),  # деления на ноль быть не должно
    ],
)
def test_percent_diff(current: Decimal, baseline: Decimal, expected: int) -> None:
    assert percent_diff(current, baseline) == expected


class TestVerdict:
    def test_at_historical_low(self) -> None:
        assert "исторический минимум" in verdict(Decimal("100"), Decimal("100"))

    def test_below_historical_low(self) -> None:
        assert "исторический минимум" in verdict(Decimal("90"), Decimal("100"))

    def test_close_to_low_is_good(self) -> None:
        text = verdict(Decimal("105"), Decimal("100"))
        assert "хорошая цена" in text and "+5%" in text

    def test_far_from_low_suggests_waiting(self) -> None:
        text = verdict(Decimal("140"), Decimal("100"))
        assert "подождать" in text and "40%" in text

    def test_no_price(self) -> None:
        assert verdict(None, Decimal("100")) == "Нет данных о цене."

    def test_no_history(self) -> None:
        assert "минимума пока нет" in verdict(Decimal("100"), None)


def test_escape_and_link() -> None:
    assert escape("Tom & Jerry <b>") == "Tom &amp; Jerry &lt;b&gt;"
    assert link("Steam", "https://s.tld/?a=1&b=2") == (
        '<a href="https://s.tld/?a=1&amp;b=2">Steam</a>'
    )
    assert link("Steam", None) == "Steam"
