"""Пользовательские типы колонок."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger, Dialect
from sqlalchemy.types import TypeDecorator

MINOR_UNITS = Decimal(100)
_CENT = Decimal("0.01")


class Money(TypeDecorator[Decimal]):
    """Деньги как `Decimal`, в БД — целое число минорных единиц.

    SQLite не умеет NUMERIC нативно и гоняет значения через float, что даёт
    ошибки округления на ценах. Храним тиыны/центы целым числом: точно,
    одинаково ведёт себя в SQLite и Postgres, легко переносится.

    Предполагается два знака после запятой — так отдают и Steam (1799900 =
    17 999,00 ₸), и Epic, и CheapShark (USD).
    """

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return int((value * MINOR_UNITS).to_integral_value(rounding=ROUND_HALF_UP))

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(value) / MINOR_UNITS).quantize(_CENT)


def from_minor(value: int | None) -> Decimal | None:
    """Минорные единицы (как отдают Steam/Epic) → Decimal."""
    if value is None:
        return None
    return (Decimal(value) / MINOR_UNITS).quantize(_CENT)
