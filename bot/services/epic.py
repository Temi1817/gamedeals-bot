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
from bot.services.models import EPIC, FreeGame, Game, Offer, Shop
from bot.utils.logging import get_logger

log = get_logger(__name__)

PROMOTIONS_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
# Витрина магазина. Единственный публичный источник цен Epic в валюте
# региона: каталог (catalog-public-service) требует авторизации, а
# store.epicgames.com/graphql закрыт Cloudflare. Отдаёт около трёхсот игр
# из подборок и распродаж — как раз тех, что попадают в /deals.
STOREFRONT_URL = "https://store-site-backend-static.ak.epicgames.com/storefrontLayout"
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
    for preferred in (
        "OfferImageWide",
        "DieselStoreFrontWide",
        "Thumbnail",
        "OfferImageTall",
    ):
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


SHOP = Shop(id="epic", name="Epic Games Store", source=EPIC)

# Базовая игра важнее дополнения, если названия совпали
_TYPE_PRIORITY = {"BASE_GAME": 0, "BUNDLE": 1}


def _price_offer(node: dict[str, Any], locale: str) -> Offer | None:
    """Строит предложение из узла витрины."""
    price = node.get("price")
    total = price.get("totalPrice") if isinstance(price, dict) else None
    if not isinstance(total, dict):
        return None

    final = from_minor(total.get("discountPrice"))
    if final is None:
        return None
    original = from_minor(total.get("originalPrice"))

    cut = 0
    if original and original > 0 and final < original:
        cut = int((original - final) / original * 100)

    slug = _slug(node)
    return Offer(
        shop=SHOP,
        price=final,
        currency=str(total.get("currencyCode") or "KZT"),
        regular_price=original if original and original > final else None,
        cut=cut,
        url=PRODUCT_URL.format(locale=locale, slug=slug) if slug else None,
    )


def _module_offers(raw: Any, module_title: str) -> list[dict[str, Any]]:
    """Предложения из блока витрины с заданным заголовком.

    Внутри блока лежат обёртки вида `{id, namespace, offer}`, а название и
    цена — уже внутри `offer`. Разворачиваем, если обёртка есть.
    """
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if found:
            return
        if isinstance(node, dict):
            if str(node.get("title") or "").strip() == module_title:
                offers = node.get("offers")
                if isinstance(offers, list):
                    found.extend(_unwrap(o) for o in offers if isinstance(o, dict))
                    return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(raw)
    return [node for node in found if node]


def _unwrap(node: dict[str, Any]) -> dict[str, Any]:
    inner = node.get("offer")
    return inner if isinstance(inner, dict) else node


class EpicClient:
    def __init__(self, api: ApiClient, cache: TTLCache, ttl: float = FREE_TTL) -> None:
        self.api = api
        self.cache = cache
        self.ttl = ttl

    async def _storefront(self, country: str, locale: str) -> dict[str, Any]:
        """Витрина целиком. Один запрос на регион, дальше из кэша."""
        key = f"epic:storefront:{country}:{locale}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                STOREFRONT_URL, params={"country": country, "locale": locale}
            )
            return data if isinstance(data, dict) else {}

        result: dict[str, Any] = await self.cache.get_or_set(key, self.ttl, fetch)
        return result

    async def regional_prices(
        self, country: str = "KZ", locale: str = "en-US"
    ) -> dict[str, Offer]:
        """Цены витрины Epic в валюте региона: название → предложение.

        Нужно потому, что у ITAD нет региональных цен для Казахстана и он
        отдаёт международные. Разница большая: HITMAN World of Assassination
        стоит в Epic 5 184 ₸, а по данным ITAD выходило 12 807 ₸.

        Локаль обязательно английская: иначе названия приходят переведёнными
        («Мир наёмных убийц HITMAN») и не сойдутся с названиями от ITAD.
        """
        raw = await self._storefront(country, locale)

        offers: dict[str, Offer] = {}
        priorities: dict[str, int] = {}

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                title = node.get("title")
                if title:
                    offer = _price_offer(node, locale)
                    if offer is not None:
                        key_title = str(title).casefold().strip()
                        rank = _TYPE_PRIORITY.get(str(node.get("offerType")), 2)
                        if rank <= priorities.get(key_title, 99):
                            offers[key_title] = offer
                            priorities[key_title] = rank
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(raw)
        return offers

    async def top_sellers(
        self, country: str = "KZ", locale: str = "en-US", limit: int = 10
    ) -> list[tuple[Game, Offer]]:
        """Модуль «Top Sellers» с витрины Epic.

        Витрина состоит из именованных блоков: Top Sellers, Trending,
        Most Played и других. Берём нужный по заголовку.
        """
        raw = await self._storefront(country, locale)
        offers = _module_offers(raw, "Top Sellers")

        result: list[tuple[Game, Offer]] = []
        seen: set[str] = set()
        for node in offers:
            title = node.get("title")
            offer = _price_offer(node, locale)
            if not title or offer is None or offer.price <= 0:
                continue
            key = str(title).casefold().strip()
            if key in seen:
                continue
            seen.add(key)
            slug = _slug(node)
            result.append(
                (
                    Game(
                        title=str(title),
                        slug=slug,
                        image_url=_image(node),
                    ),
                    offer,
                )
            )
            if len(result) >= limit:
                break
        return result

    async def offer_for(
        self, title: str, country: str = "KZ", locale: str = "en-US"
    ) -> Offer | None:
        """Цена Epic для игры с точно таким названием, иначе None."""
        prices = await self.regional_prices(country, locale)
        return prices.get(title.casefold().strip())

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
        games.sort(
            key=lambda g: (g.upcoming, g.ends_at or datetime.max.replace(tzinfo=UTC))
        )
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
