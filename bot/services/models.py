"""Доменные модели.

Клиенты API возвращают только эти типы — хендлеры не должны знать, из
какого источника пришли данные и как выглядит его JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Источник данных. Влияет на то, как трактовать цену: `cheapshark` отдаёт
# доллары и ключи реселлеров, остальные — цену магазина в валюте региона.
Source = str

ITAD = "itad"
STEAM = "steam"
EPIC = "epic"
CHEAPSHARK = "cheapshark"

# Из чего состоит Game.key. Двоеточие занято aiogram под разделитель полей
# callback_data, поэтому берём `~`.
KEY_SEP = "~"
KEY_ITAD = "i"
KEY_STEAM = "s"
KEY_CHEAPSHARK = "c"
KEY_TITLE = "t"


@dataclass(frozen=True, slots=True)
class Game:
    """Игра без привязки к источнику. Идентификаторов может быть несколько."""

    title: str
    itad_id: str | None = None
    steam_appid: int | None = None
    cheapshark_id: str | None = None
    slug: str | None = None
    image_url: str | None = None

    @property
    def key(self) -> str:
        """Стабильный ключ для callback-данных и кэша.

        Разделитель именно `~`, а не двоеточие: двоеточием aiogram
        разделяет поля внутри `callback_data`, и ключ с ним не упакуется.
        В UUID, appid и ID CheapShark символа `~` не бывает.
        """
        if self.itad_id:
            return f"{KEY_ITAD}{KEY_SEP}{self.itad_id}"
        if self.steam_appid:
            return f"{KEY_STEAM}{KEY_SEP}{self.steam_appid}"
        if self.cheapshark_id:
            return f"{KEY_CHEAPSHARK}{KEY_SEP}{self.cheapshark_id}"
        return f"{KEY_TITLE}{KEY_SEP}{self.title.lower()}"


@dataclass(frozen=True, slots=True)
class Shop:
    """Магазин: имя для показа плюс идентификатор внутри источника."""

    id: str
    name: str
    source: Source = ITAD


@dataclass(frozen=True, slots=True)
class Offer:
    """Одно предложение: цена конкретной игры в конкретном магазине."""

    shop: Shop
    price: Decimal
    currency: str
    regular_price: Decimal | None = None
    cut: int = 0
    url: str | None = None
    # CheapShark торгует в основном ключами реселлеров — такие цены
    # помечаем, чтобы не выдавать их за цену магазина
    is_reseller: bool = False
    # цена не в валюте региона пользователя (тот же CheapShark — всегда USD)
    approximate: bool = False
    # Пересчёт в валюту региона, заполняет агрегатор. Нужен, потому что у
    # ITAD нет цен для Казахстана и он отдаёт доллары: без общей валюты
    # список не отсортировать «от дешёвой к дорогой».
    converted_price: Decimal | None = None
    converted_currency: str | None = None

    @property
    def is_free(self) -> bool:
        return self.price <= 0

    @property
    def sort_key(self) -> Decimal:
        """Цена в единой валюте — по ней сортируется карточка."""
        return self.converted_price if self.converted_price is not None else self.price

    @property
    def savings(self) -> Decimal | None:
        """Сколько экономим против обычной цены."""
        if self.regular_price is None or self.regular_price <= self.price:
            return None
        return self.regular_price - self.price


@dataclass(frozen=True, slots=True)
class HistoricalLow:
    """Исторический минимум цены."""

    price: Decimal
    currency: str
    at: datetime | None = None
    shop: str | None = None


@dataclass(frozen=True, slots=True)
class GameDetails:
    """Всё, что нужно для карточки игры."""

    game: Game
    offers: list[Offer] = field(default_factory=list)
    historical_low: HistoricalLow | None = None
    # источники, которые реально ответили — чтобы честно сказать
    # пользователю, если часть данных недоступна
    sources: tuple[Source, ...] = ()

    @property
    def best_offer(self) -> Offer | None:
        """Самое дешёвое предложение из настоящих магазинов.

        Реселлеров исключаем: их ключи — это не покупка в магазине, и
        подсовывать их как лучшую цену нечестно.
        """
        shops = [o for o in self.offers if not o.is_reseller]
        return min(shops, key=lambda o: o.sort_key) if shops else None


@dataclass(frozen=True, slots=True)
class Deal:
    """Позиция в списке скидок (`/deals`, фильтр по цене)."""

    game: Game
    offer: Offer


@dataclass(frozen=True, slots=True)
class FreeGame:
    """Бесплатная раздача."""

    title: str
    url: str | None = None
    image_url: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    original_price: Decimal | None = None
    currency: str = "KZT"
    # true — раздача ещё не началась, а только анонсирована
    upcoming: bool = False
