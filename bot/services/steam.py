"""Клиент Steam Store.

API неофициальный, поэтому: короткие таймауты, батчи где можно и терпимость
к `success: false` — Steam отдаёт его и для несуществующих appid, и для игр,
недоступных в регионе.

Цены приходят в минорных единицах: `1799900` при `currency: "KZT"` — это
17 999 ₸. `initial_formatted` при нулевой скидке приходит пустой строкой,
поэтому старую цену считаем сами из `initial`.
"""

from __future__ import annotations

from typing import Any

from bot.db.types import from_minor
from bot.services.cache import TTLCache
from bot.services.http import ApiClient, ApiError
from bot.services.models import STEAM, Game, Offer, Shop
from bot.utils.logging import get_logger

log = get_logger(__name__)

SEARCH_URL = "https://steamcommunity.com/actions/SearchApps/{query}"
FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORE_URL = "https://store.steampowered.com/app/{appid}/"

SHOP = Shop(id="steam", name="Steam", source=STEAM)

# Steam молча обрезает слишком длинные батчи — держим порцию небольшой
BATCH_SIZE = 20


class SteamClient:
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

    # ----------------------------------------------------------------- поиск
    async def search(self, query: str, limit: int = 5) -> list[Game]:
        """Поиск игр по названию."""
        key = f"steam:search:{query.lower()}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(SEARCH_URL.format(query=query))
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)
        return [g for g in (self._parse_search_item(i) for i in raw[:limit]) if g]

    @staticmethod
    def _parse_search_item(item: dict[str, Any]) -> Game | None:
        appid = item.get("appid")
        name = item.get("name")
        if not appid or not name:
            return None
        try:
            appid_int = int(appid)
        except (TypeError, ValueError):
            return None
        return Game(
            title=str(name),
            steam_appid=appid_int,
            image_url=item.get("logo") or item.get("icon"),
        )

    # ----------------------------------------------------------------- цены
    async def prices(self, appids: list[int], country: str = "KZ") -> dict[int, Offer]:
        """Цены пачкой. Игры без цены (F2P, нет в регионе) просто отсутствуют."""
        if not appids:
            return {}

        result: dict[int, Offer] = {}
        for start in range(0, len(appids), BATCH_SIZE):
            chunk = appids[start : start + BATCH_SIZE]
            try:
                result.update(await self._prices_chunk(chunk, country))
            except ApiError as exc:
                # частичный ответ лучше пустого: остальные порции могут дойти
                log.warning("steam_prices_failed", error=str(exc), appids=chunk)
        return result

    async def _prices_chunk(self, appids: list[int], country: str) -> dict[int, Offer]:
        ids = ",".join(str(a) for a in appids)
        key = f"steam:prices:{country}:{ids}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                DETAILS_URL,
                params={
                    "appids": ids,
                    "cc": country.lower(),
                    "l": "russian",
                    "filters": "price_overview",
                },
            )
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)

        offers: dict[int, Offer] = {}
        for appid_str, payload in raw.items():
            offer = self._parse_price(appid_str, payload)
            if offer is not None:
                offers[int(appid_str)] = offer
        return offers

    @staticmethod
    def _parse_price(appid_str: str, payload: Any) -> Offer | None:
        if not isinstance(payload, dict) or not payload.get("success"):
            return None

        data = payload.get("data")
        # у части игр data приходит пустым списком, а не объектом
        if not isinstance(data, dict):
            return None

        price = data.get("price_overview")
        if not isinstance(price, dict):
            return None  # F2P или не продаётся в регионе

        final = from_minor(price.get("final"))
        if final is None:
            return None

        initial = from_minor(price.get("initial"))
        cut = int(price.get("discount_percent") or 0)

        return Offer(
            shop=SHOP,
            price=final,
            currency=str(price.get("currency") or "KZT"),
            # старую цену показываем, только если она реально выше текущей
            regular_price=initial if initial and initial > final else None,
            cut=cut,
            url=STORE_URL.format(appid=appid_str),
        )

    # ---------------------------------------------------------- топ продаж
    async def top_sellers(
        self, country: str = "KZ", limit: int = 10
    ) -> list[tuple[Game, Offer]]:
        """Топ продаж витрины Steam с ценами в валюте региона.

        В выдаче попадаются железо и промо-карточки с нулевой ценой, а одна
        позиция может повторяться несколько раз — чистим и то, и другое.
        """
        key = f"steam:top:{country}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                FEATURED_URL, params={"cc": country.lower(), "l": "russian"}
            )
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)

        block = raw.get("top_sellers")
        items = block.get("items") if isinstance(block, dict) else None
        if not isinstance(items, list):
            return []

        result: list[tuple[Game, Offer]] = []
        seen: set[int] = set()
        for item in items:
            parsed = self._parse_featured(item)
            if parsed is None:
                continue
            game, offer = parsed
            if game.steam_appid in seen:
                continue
            seen.add(game.steam_appid or 0)
            result.append((game, offer))
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _parse_featured(item: Any) -> tuple[Game, Offer] | None:
        if not isinstance(item, dict) or not item.get("name"):
            return None

        appid = item.get("id")
        final = from_minor(item.get("final_price"))
        original = from_minor(item.get("original_price"))

        # железо и промо-карточки приходят без цены вовсе
        if appid is None or final is None or (final <= 0 and original is None):
            return None

        game = Game(
            title=str(item["name"]),
            steam_appid=int(appid),
            image_url=item.get("large_capsule_image") or item.get("small_capsule_image"),
        )
        offer = Offer(
            shop=SHOP,
            price=final,
            currency=str(item.get("currency") or "KZT"),
            regular_price=original if original and original > final else None,
            cut=int(item.get("discount_percent") or 0),
            url=STORE_URL.format(appid=appid),
        )
        return game, offer

    # -------------------------------------------------------------- описание
    async def details(self, appid: int, country: str = "KZ") -> Game | None:
        """Название и обложка. Нужно для карточки, когда игра пришла не из поиска."""
        key = f"steam:details:{country}:{appid}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                DETAILS_URL,
                params={
                    "appids": appid,
                    "cc": country.lower(),
                    "l": "russian",
                    "filters": "basic",
                },
            )
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)

        payload = raw.get(str(appid))
        if not isinstance(payload, dict) or not payload.get("success"):
            return None
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("name"):
            return None

        return Game(
            title=str(data["name"]),
            steam_appid=appid,
            image_url=data.get("header_image") or data.get("capsule_image"),
        )


def store_url(appid: int) -> str:
    return STORE_URL.format(appid=appid)


__all__ = ["SHOP", "SteamClient", "store_url"]
