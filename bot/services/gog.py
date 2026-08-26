"""Клиент витрины GOG.

Зачем отдельный клиент, хотя GOG есть в ITAD: у GOG **своя региональная
цена для Казахстана**, а ITAD её не знает и отдаёт международную. Разница
не косметическая — Cyberpunk 2077 на 2026-08-26:

* реальная цена GOG для KZ — $8.99, обычная $29.99
* то же самое от ITAD — $17.99, обычная $59.99

То есть ровно вдвое дороже. Поэтому цену GOG, как и цену Steam, берём
у самого магазина.

Эндпоинт `catalog.gog.com/v1/catalog` — тот же, что использует поиск на
сайте: ключа не требует, страну принимает параметром `countryCode`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.models import Game, Offer, Shop
from bot.utils.logging import get_logger

log = get_logger(__name__)

CATALOG_URL = "https://catalog.gog.com/v1/catalog"

SHOP = Shop(id="gog", name="GOG", source="gog")

# Дополнения и наборы под тем же именем сбивают сопоставление, поэтому
# берём только то, что GOG считает игрой или паком.
GAME_TYPES = frozenset({"game", "pack"})


def _money(raw: Any) -> tuple[Decimal, str] | None:
    """`{"amount": "8.99", "currency": "USD"}` → сумма и код валюты."""
    if not isinstance(raw, dict):
        return None
    amount = raw.get("amount")
    if amount is None:
        return None
    try:
        return Decimal(str(amount)), str(raw.get("currency") or "USD").upper()
    except (InvalidOperation, ValueError):
        return None


def _cut(raw: Any) -> int:
    """`'-70%'` → `70`."""
    if not isinstance(raw, str):
        return 0
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


class GogClient:
    def __init__(
        self,
        api: ApiClient,
        cache: TTLCache,
        *,
        search_ttl: float = 3600,
        price_ttl: float = 1800,
    ) -> None:
        self.api = api
        self.cache = cache
        self.search_ttl = search_ttl
        self.price_ttl = price_ttl

    async def _catalog(self, query: str, country: str, limit: int) -> list[Any]:
        key = f"gog:catalog:{country}:{query.lower()}:{limit}"

        async def fetch() -> list[Any]:
            data = await self.api.get_json(
                CATALOG_URL,
                params={
                    "limit": limit,
                    "query": f"like:{query}",
                    "countryCode": country.upper(),
                    "locale": "en-US",
                },
            )
            if not isinstance(data, dict):
                return []
            products = data.get("products")
            return products if isinstance(products, list) else []

        result: list[Any] = await self.cache.get_or_set(key, self.price_ttl, fetch)
        return result

    async def search(self, query: str, limit: int = 5) -> list[Game]:
        products = await self._catalog(query, "US", limit)
        return [g for g in (self._parse_game(p) for p in products) if g]

    async def offer_for(
        self, title: str, country: str = "KZ"
    ) -> tuple[Game, Offer] | None:
        """Цена GOG для игры с точно таким названием.

        Сопоставляем строго по названию: «Cyberpunk 2077» и «Cyberpunk 2077:
        Ultimate Edition» — разные товары с разной ценой, и подменить одно
        другим значит соврать пользователю. Не нашли точное совпадение —
        возвращаем None, и в карточке останется цена от ITAD.
        """
        products = await self._catalog(title, country, limit=10)

        wanted = title.casefold().strip()
        for product in products:
            if not isinstance(product, dict):
                continue
            if str(product.get("productType") or "").lower() not in GAME_TYPES:
                continue
            if str(product.get("title") or "").casefold().strip() != wanted:
                continue

            game = self._parse_game(product)
            offer = self._parse_offer(product)
            if game is not None and offer is not None:
                return game, offer
        return None

    @staticmethod
    def _parse_game(product: Any) -> Game | None:
        if not isinstance(product, dict) or not product.get("title"):
            return None
        return Game(
            title=str(product["title"]),
            slug=product.get("slug"),
            image_url=product.get("coverVertical") or product.get("coverHorizontal"),
        )

    @staticmethod
    def _parse_offer(product: dict[str, Any]) -> Offer | None:
        price = product.get("price")
        if not isinstance(price, dict):
            return None

        final = _money(price.get("finalMoney"))
        if final is None:
            return None
        amount, currency = final

        base = _money(price.get("baseMoney"))
        base_amount = base[0] if base else None

        return Offer(
            shop=SHOP,
            price=amount,
            currency=currency,
            # старую цену показываем, только если она реально выше текущей
            regular_price=base_amount if base_amount and base_amount > amount else None,
            cut=_cut(price.get("discount")),
            url=product.get("storeLink"),
        )
