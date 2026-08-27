"""Клиент CheapShark — резервный источник.

Ключ не нужен, но есть два ограничения, которые нельзя замалчивать в UI:
цены только в USD и в основном это ключи реселлеров, а не витрины магазинов.
Поэтому все предложения помечаем `is_reseller=True` и `approximate=True` —
складывать их с тенге от ITAD/Steam нельзя.

Числа приходят строками: `"59.99"`, `"0.000000"`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.models import CHEAPSHARK, Deal, Game, HistoricalLow, Offer, Shop
from bot.utils.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://www.cheapshark.com/api/1.0"
REDIRECT_URL = "https://www.cheapshark.com/redirect?dealID={deal_id}"

CURRENCY = "USD"


def _decimal(value: Any) -> Decimal | None:
    """CheapShark отдаёт числа строками — аккуратно приводим."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def deal_url(deal_id: str) -> str:
    """Ссылка на покупку идёт через редирект CheapShark."""
    return REDIRECT_URL.format(deal_id=deal_id)


class CheapSharkClient:
    def __init__(
        self,
        api: ApiClient,
        cache: TTLCache,
        *,
        search_ttl: float = 3600,
        price_ttl: float = 1800,
        shops_ttl: float = 86400,
    ) -> None:
        self.api = api
        self.cache = cache
        self.search_ttl = search_ttl
        self.price_ttl = price_ttl
        self.shops_ttl = shops_ttl

    # -------------------------------------------------------------- магазины
    async def shops(self) -> dict[str, Shop]:
        """Справочник магазинов, `storeID` → магазин. Кэш на сутки."""

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(f"{BASE_URL}/stores")
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set("cheapshark:stores", self.shops_ttl, fetch)
        return {
            str(s["storeID"]): Shop(
                id=str(s["storeID"]),
                name=str(s.get("storeName") or f"Магазин {s['storeID']}"),
                source=CHEAPSHARK,
            )
            for s in raw
            if isinstance(s, dict) and s.get("storeID")
        }

    async def _shops_safe(self) -> dict[str, Shop]:
        """Справочник магазинов; если не дошёл — пустой словарь.

        Тянем его один раз на вызов и прокидываем в разбор: иначе упавший
        `/stores` умножается на число предложений в ответе.
        """
        try:
            return await self.shops()
        except Exception as exc:
            log.warning("cheapshark_stores_failed", error=str(exc))
            return {}

    @staticmethod
    def _shop(shops: dict[str, Shop], store_id: Any) -> Shop:
        key = str(store_id)
        return shops.get(key, Shop(id=key, name=f"Магазин {key}", source=CHEAPSHARK))

    # ----------------------------------------------------------------- поиск
    async def search(self, query: str, limit: int = 5) -> list[Game]:
        key = f"cheapshark:search:{query.lower()}:{limit}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(
                f"{BASE_URL}/games", params={"title": query, "limit": limit}
            )
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)
        return [g for g in (self._parse_search_item(i) for i in raw) if g]

    @staticmethod
    def _parse_search_item(item: Any) -> Game | None:
        if not isinstance(item, dict) or not item.get("gameID"):
            return None
        title = item.get("external") or item.get("internalName")
        if not title:
            return None

        steam_appid = item.get("steamAppID")
        return Game(
            title=str(title),
            cheapshark_id=str(item["gameID"]),
            steam_appid=int(steam_appid) if steam_appid else None,
            image_url=item.get("thumb"),
        )

    # ------------------------------------------------------------ предложения
    async def offers(
        self, game_id: str
    ) -> tuple[Game | None, list[Offer], HistoricalLow | None]:
        """Все предложения по игре плюс исторический минимум (в USD)."""
        key = f"cheapshark:game:{game_id}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(f"{BASE_URL}/games", params={"id": game_id})
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)
        if not raw:
            return None, [], None

        raw_info = raw.get("info")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        steam_appid = info.get("steamAppID")
        game = (
            Game(
                title=str(info["title"]),
                cheapshark_id=game_id,
                steam_appid=int(steam_appid) if steam_appid else None,
                image_url=info.get("thumb"),
            )
            if info.get("title")
            else None
        )

        shops = await self._shops_safe()
        offers: list[Offer] = []
        for deal in raw.get("deals") or []:
            offer = self._parse_deal(deal, shops)
            if offer is not None:
                offers.append(offer)
        offers.sort(key=lambda o: o.price)

        return game, offers, self._parse_low(raw.get("cheapestPriceEver"))

    @classmethod
    def _parse_deal(cls, deal: Any, shops: dict[str, Shop]) -> Offer | None:
        if not isinstance(deal, dict):
            return None
        price = _decimal(deal.get("price") or deal.get("salePrice"))
        if price is None:
            return None

        retail = _decimal(deal.get("retailPrice") or deal.get("normalPrice"))
        deal_id = deal.get("dealID")

        return Offer(
            shop=cls._shop(shops, deal.get("storeID")),
            price=price,
            currency=CURRENCY,
            regular_price=retail if retail and retail > price else None,
            cut=_int(deal.get("savings")),
            url=deal_url(str(deal_id)) if deal_id else None,
            is_reseller=True,
            approximate=True,
        )

    @staticmethod
    def _parse_low(raw: Any) -> HistoricalLow | None:
        if not isinstance(raw, dict):
            return None
        price = _decimal(raw.get("price"))
        if price is None:
            return None

        at: datetime | None = None
        timestamp = raw.get("date")
        if timestamp:
            try:
                at = datetime.fromtimestamp(int(timestamp), tz=UTC)
            except (TypeError, ValueError, OSError):
                at = None

        return HistoricalLow(price=price, currency=CURRENCY, at=at)

    # ------------------------------------------------------------------ deals
    async def deals(
        self,
        *,
        upper_price: Decimal | float | None = None,
        min_savings: int = 0,
        page: int = 0,
        page_size: int = 20,
        sort_by: str = "Savings",
    ) -> list[Deal]:
        """Список скидок. Цена — в долларах, отсюда и `upper_price`."""
        params: dict[str, Any] = {
            "sortBy": sort_by,
            "pageNumber": page,
            "pageSize": page_size,
            "onSale": 1,
        }
        if upper_price is not None:
            params["upperPrice"] = str(upper_price)
        if min_savings:
            params["lowerPrice"] = 0

        key = f"cheapshark:deals:{sort_by}:{upper_price}:{page}:{page_size}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(f"{BASE_URL}/deals", params=params)
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)

        shops = await self._shops_safe()
        result: list[Deal] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            offer = self._parse_deal(item, shops)
            if offer is None or offer.cut < min_savings:
                continue

            steam_appid = item.get("steamAppID")
            result.append(
                Deal(
                    game=Game(
                        title=str(item["title"]),
                        cheapshark_id=str(item.get("gameID") or ""),
                        steam_appid=int(steam_appid) if steam_appid else None,
                        image_url=item.get("thumb"),
                    ),
                    offer=offer,
                )
            )
        return result
