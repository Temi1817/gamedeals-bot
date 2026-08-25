"""Клиент Epic Games Store — бесплатные раздачи.

Эндпоинт `freeGamesPromotions` отдаёт вперемешку: идущие раздачи, будущие
и просто игры из промо-блока. Раздача — это `discountPrice == 0` внутри
активного окна `promotionalOffers`; всё остальное отсекаем.

Цены, как и у Steam, в минорных единицах: `754000` при `KZT` = 7 540 ₸.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

from bot.db.types import from_minor
from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.models import FreeGame
from bot.utils.logging import get_logger

log = get_logger(__name__)

PROMOTIONS_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
)
PRODUCT_URL = "https://store.epicgames.com/{locale}/p/{slug}"

# Раздачи меняются раз в неделю — часа кэша более чем достаточно
FREE_TTL = 3600.0


def _parse_dt(value: Any) -> datetime | None:
    """`'2026-08-27T15:00:00.000Z'` → aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(element: dict[str, Any]) -> str | None:
    """Ссылку на страницу собираем по убыванию надёжности.

    `productSlug` часто приходит `null` или с хвостом `/home`, поэтому
    сначала смотрим на маппинги.
    """
    for source in ("offerMappings", "catalogNs"):
        raw = element.get(source)
        mappings = raw.get("mappings") if isinstance(raw, dict) else raw
        if isinstance(mappings, list):
            for mapping in mappings:
                if isinstance(mapping, dict) and mapping.get("pageSlug"):
                    return str(mapping["pageSlug"])

    for key in ("productSlug", "urlSlug"):
        value = element.get(key)
        if isinstance(value, str) and value:
            return value.removesuffix("/home")

    return None


def _image(element: dict[str, Any]) -> str | None:
    """Берём самую крупную из доступных картинок."""
    images = element.get("keyImages")
    if not isinstance(images, list):
        return None
    by_type = {
        img.get("type"): img.get("url")
        for img in images
        if isinstance(img, dict) and img.get("url")
    }
    for preferred in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail",
                      "OfferImageTall"):
        if url := by_type.get(preferred):
            return str(url)
    return next((str(u) for u in by_type.values() if u), None)


class _Window(NamedTuple):
    starts_at: datetime | None
    ends_at: datetime | None
    percent: int  # доля цены, которую платит покупатель: 0 = бесплатно


def _giveaway_window(offers: Any) -> _Window | None:
    """Первое окно раздачи из вложенной структуры `promotionalOffers`.

    `discountPercentage` у Epic — это сколько процентов цены покупатель
    платит, а не размер скидки: 0 — раздача, 25 — распродажа −75%.
    Поэтому обычные скидки из промо-блока отсеиваем здесь же.
    """
    if not isinstance(offers, list):
        return None
    for group in offers:
        inner = group.get("promotionalOffers") if isinstance(group, dict) else None
        if not isinstance(inner, list):
            continue
        for window in inner:
            if not isinstance(window, dict):
                continue
            setting = window.get("discountSetting")
            setting = setting if isinstance(setting, dict) else {}
            percent = setting.get("discountPercentage")
            if percent != 0:
                continue
            return _Window(
                _parse_dt(window.get("startDate")),
                _parse_dt(window.get("endDate")),
                0,
            )
    return None


class EpicClient:
    def __init__(self, api: ApiClient, cache: TTLCache, ttl: float = FREE_TTL) -> None:
        self.api = api
        self.cache = cache
        self.ttl = ttl

    async def free_games(
        self, country: str = "KZ", locale: str = "ru", include_upcoming: bool = True
    ) -> list[FreeGame]:
        """Текущие (и, по желанию, анонсированные) бесплатные раздачи."""
        key = f"epic:free:{country}:{locale}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                PROMOTIONS_URL,
                params={
                    "locale": locale,
                    "country": country,
                    "allowCountries": country,
                },
            )
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.ttl, fetch)
        elements = self._elements(raw)

        games: list[FreeGame] = []
        for element in elements:
            game = self._parse_element(element, locale)
            if game is None:
                continue
            if game.upcoming and not include_upcoming:
                continue
            games.append(game)

        # сначала то, что можно забрать прямо сейчас
        games.sort(key=lambda g: (g.upcoming, g.ends_at or datetime.max.replace(
            tzinfo=UTC)))
        return games

    @staticmethod
    def _elements(raw: dict[str, Any]) -> list[dict[str, Any]]:
        node: Any = raw
        for step in ("data", "Catalog", "searchStore", "elements"):
            node = node.get(step) if isinstance(node, dict) else None
            if node is None:
                return []
        return [e for e in node if isinstance(e, dict)] if isinstance(node, list) else []

    @staticmethod
    def _parse_element(element: dict[str, Any], locale: str) -> FreeGame | None:
        title = element.get("title")
        if not title:
            return None

        price = element.get("price")
        total = price.get("totalPrice") if isinstance(price, dict) else None
        total = total if isinstance(total, dict) else {}

        currency = str(total.get("currencyCode") or "KZT")
        discount = total.get("discountPrice")
        original = from_minor(total.get("originalPrice"))

        promos = element.get("promotions")
        promos = promos if isinstance(promos, dict) else {}
        current = _giveaway_window(promos.get("promotionalOffers"))
        upcoming = _giveaway_window(promos.get("upcomingPromotionalOffers"))

        # раздача идёт: цена обнулена и открыто окно промо
        if discount == 0 and current is not None:
            window, is_upcoming = current, False
        elif upcoming is not None:
            window, is_upcoming = upcoming, True
        else:
            return None  # обычная игра или распродажа из промо-блока
        starts, ends = window.starts_at, window.ends_at

        slug = _slug(element)
        return FreeGame(
            title=str(title),
            url=PRODUCT_URL.format(locale=locale, slug=slug) if slug else None,
            image_url=_image(element),
            description=element.get("description"),
            starts_at=starts,
            ends_at=ends,
            original_price=original,
            currency=currency,
            upcoming=is_upcoming,
        )
