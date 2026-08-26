"""Клиент IsThereAnyDeal API v2 — основной источник цен по магазинам.

Схема сверена живыми запросами (см. `scripts/probe_apis.py itad`). Что важно:

* Лимит 1000 запросов / 5 минут — все ответы идут через кэш.
* `country` работает не для всех стран: DE→EUR, PL→PLN, TR→TRY, GB→GBP,
  а KZ, UA и RU молча откатываются на USD. Валюту всегда берём из ответа,
  а не из настроек региона.
* `deals=true` оставляет только магазины с активной скидкой (для Cyberpunk
  это один GOG вместо четырёх), поэтому для карточки шлём `deals=false`.
* Тело у POST-эндпоинтов — голый массив ID; `{"ids": [...]}` даёт 400.
* Цена приходит парой: `amount` (17.99) и `amountInt` (1799, в центах).
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.models import ITAD, Deal, Game, HistoricalLow, Offer, Shop
from bot.utils.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.isthereanydeal.com"

# ITAD ограничивает размер батча; больше сотни за раз всё равно не нужно
BATCH_SIZE = 50

# Рейтинги. `waitlisted` — сколько людей ждут скидку, для бота о ценах это
# полезнее всего; `popular` и `collected` — общая популярность.
TOP_ENDPOINTS = {
    "waitlisted": "/stats/most-waitlisted/v1",
    "popular": "/stats/most-popular/v1",
    "collected": "/stats/most-collected/v1",
}


def _price(raw: Any) -> tuple[Decimal, str] | None:
    """`{"amount": 17.99, "amountInt": 1799, "currency": "USD"}` → сумма и код."""
    if not isinstance(raw, dict):
        return None
    currency = str(raw.get("currency") or "USD").upper()

    amount = raw.get("amount")
    if amount is not None:
        try:
            return Decimal(str(amount)), currency
        except (InvalidOperation, ValueError):
            pass

    amount_int = raw.get("amountInt")
    if amount_int is not None:
        try:
            return (Decimal(str(amount_int)) / 100).quantize(Decimal("0.01")), currency
        except (InvalidOperation, ValueError):
            pass

    return None


# Порог «живой» игры. Без него выдача забита ассет-флипами: десятки
# безымянных игр с одинаковым ценником $29.99 → $1.49 и скидкой −95%.
MIN_REVIEWS = 50
MIN_SCORE = 60


def _deal_filter(
    min_cut: int,
    max_price: Decimal | None,
    games_only: bool,
    quality: bool = True,
) -> dict[str, Any]:
    """Собирает JSON-фильтр для `/deals/v2`.

    Диапазоны требуют обоих концов, отсутствующий край — `null`.
    `type: [1]` оставляет только игры, отсекая DLC и издания.
    `quality` отсеивает мусор по числу отзывов и рейтингу в Steam.
    """
    filters: dict[str, Any] = {}
    if min_cut > 0:
        filters["cut"] = {"min": min_cut, "max": None}
    if max_price is not None:
        filters["price"] = {"min": None, "max": float(max_price)}
    if games_only:
        filters["type"] = [1]
    if quality:
        filters["steamCount"] = {"min": MIN_REVIEWS, "max": None}
        filters["steamPerc"] = {"min": MIN_SCORE, "max": 100}
    return filters


def _dt(value: Any) -> datetime | None:
    """`'2026-06-17T14:44:42+02:00'` → aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _image(assets: Any) -> str | None:
    """Обложка: от крупной к мелкой."""
    if not isinstance(assets, dict):
        return None
    for key in ("banner600", "banner400", "banner300", "boxart", "banner145"):
        if url := assets.get(key):
            return str(url)
    return None


def _game(raw: Any) -> Game | None:
    """Общая часть: и поиск, и lookup, и deals отдают игру одинаково."""
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
        return None
    appid = raw.get("appid")
    return Game(
        title=str(raw["title"]),
        itad_id=str(raw["id"]),
        slug=raw.get("slug"),
        steam_appid=int(appid) if appid else None,
        image_url=_image(raw.get("assets")),
    )


def _shop(raw: Any) -> Shop:
    if not isinstance(raw, dict):
        return Shop(id="?", name="Неизвестный магазин", source=ITAD)
    shop_id = str(raw.get("id") or "?")
    return Shop(id=shop_id, name=str(raw.get("name") or f"Магазин {shop_id}"),
                source=ITAD)


def _offer(raw: Any) -> Offer | None:
    """Одно предложение из `deals[]`."""
    if not isinstance(raw, dict):
        return None
    price = _price(raw.get("price"))
    if price is None:
        return None
    amount, currency = price

    regular = _price(raw.get("regular"))
    regular_amount = regular[0] if regular else None

    return Offer(
        shop=_shop(raw.get("shop")),
        price=amount,
        currency=currency,
        # старую цену показываем, только если она реально выше текущей
        regular_price=regular_amount if regular_amount and regular_amount > amount
        else None,
        cut=int(raw.get("cut") or 0),
        url=raw.get("url"),
    )


class ItadClient:
    """Клиент ITAD. Ключ обязателен — без него источник не поднимается."""

    def __init__(
        self,
        api: ApiClient,
        cache: TTLCache,
        api_key: str,
        *,
        search_ttl: float = 3600,
        price_ttl: float = 1800,
        shops_ttl: float = 86400,
    ) -> None:
        self.api = api
        self.cache = cache
        self.api_key = api_key
        self.search_ttl = search_ttl
        self.price_ttl = price_ttl
        self.shops_ttl = shops_ttl

    def _params(self, **extra: Any) -> dict[str, Any]:
        return {"key": self.api_key, **extra}

    # ----------------------------------------------------------------- поиск
    async def search(self, query: str, limit: int = 5) -> list[Game]:
        """Поиск по названию. DLC и издания приходят вперемешку с играми."""
        key = f"itad:search:{query.lower()}:{limit}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(
                f"{BASE_URL}/games/search/v1",
                params=self._params(title=query, results=limit),
            )
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)
        return [g for g in (_game(item) for item in raw) if g]

    async def lookup_by_appid(self, appid: int) -> Game | None:
        """Найти игру по Steam appid — так связываем Steam и ITAD."""
        key = f"itad:lookup:{appid}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                f"{BASE_URL}/games/lookup/v1", params=self._params(appid=appid)
            )
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)
        return _game(raw.get("game")) if raw.get("found") else None

    async def info(self, game_id: str) -> Game | None:
        """Метаданные игры: обложка, slug, Steam appid."""
        key = f"itad:info:{game_id}"

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(
                f"{BASE_URL}/games/info/v2", params=self._params(id=game_id)
            )
            return data if isinstance(data, dict) else {}

        return _game(await self.cache.get_or_set(key, self.search_ttl, fetch))

    # ----------------------------------------------------------------- цены
    async def prices(
        self, game_ids: list[str], country: str = "KZ", *, only_deals: bool = False
    ) -> dict[str, list[Offer]]:
        """Цены по магазинам, ID игры → список предложений (уже отсортирован).

        `only_deals=True` оставит лишь магазины с активной скидкой.
        """
        if not game_ids:
            return {}

        result: dict[str, list[Offer]] = {}
        for start in range(0, len(game_ids), BATCH_SIZE):
            chunk = game_ids[start : start + BATCH_SIZE]
            raw = await self._prices_chunk(chunk, country, only_deals)
            for entry in raw:
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                offers = [o for o in (_offer(d) for d in entry.get("deals") or []) if o]
                offers.sort(key=lambda o: o.price)
                result[str(entry["id"])] = offers
        return result

    async def _prices_chunk(
        self, game_ids: list[str], country: str, only_deals: bool
    ) -> list[dict[str, Any]]:
        key = f"itad:prices:{country}:{only_deals}:{','.join(sorted(game_ids))}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.post_json(
                f"{BASE_URL}/games/prices/v3",
                params=self._params(
                    country=country,
                    deals="true" if only_deals else "false",
                    capacity=0,
                ),
                json=game_ids,
            )
            return data if isinstance(data, list) else []

        return await self.cache.get_or_set(key, self.price_ttl, fetch)

    async def history_lows(
        self, game_ids: list[str], country: str = "KZ"
    ) -> dict[str, HistoricalLow]:
        """Исторический минимум по каждой игре."""
        if not game_ids:
            return {}

        key = f"itad:historylow:{country}:{','.join(sorted(game_ids))}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.post_json(
                f"{BASE_URL}/games/historylow/v1",
                params=self._params(country=country),
                json=game_ids,
            )
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)

        lows: dict[str, HistoricalLow] = {}
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            low = entry.get("low")
            if not isinstance(low, dict):
                continue  # у новинок минимума ещё нет
            price = _price(low.get("price"))
            if price is None:
                continue
            amount, currency = price
            shop = low.get("shop")
            lows[str(entry["id"])] = HistoricalLow(
                price=amount,
                currency=currency,
                at=_dt(low.get("timestamp")),
                shop=str(shop.get("name")) if isinstance(shop, dict) else None,
            )
        return lows

    # ---------------------------------------------------------------- скидки
    async def deals(
        self,
        country: str = "KZ",
        *,
        limit: int = 20,
        offset: int = 0,
        sort: str = "-trending",
        min_cut: int = 0,
        max_price: Decimal | None = None,
        games_only: bool = True,
        quality: bool = True,
        shops: list[int] | None = None,
    ) -> tuple[list[Deal], int | None]:
        """Актуальные скидки. Возвращает список и offset следующей страницы.

        Сортировка по умолчанию — `-trending`, а не `-cut`. При `-cut`
        наверху всегда скидки под 100%, поэтому кнопки «от 50%» и «от 75%»
        визуально ничего не меняли: порог и так был выполнен.
        Список значений `sort` ITAD проверяет и на неизвестное отвечает 400,
        в отличие от полей фильтра, которые молча игнорирует.

        Порог скидки и потолок цены задаются не отдельными параметрами, а
        полем `filter` — JSON вида `{"cut": {"min": 75, "max": null}}`.
        Отдельных `minCut`/`maxPrice` у ITAD нет, а неизвестные параметры он
        молча игнорирует, поэтому промах здесь не даёт ошибки — просто
        возвращается неотфильтрованный список.

        `max_price` — в валюте ответа, а для KZ это доллары. Пересчёт из
        тенге делает вызывающий код.
        """
        filters = _deal_filter(min_cut, max_price, games_only, quality)
        shop_ids = ",".join(str(s) for s in shops) if shops else ""
        key = (
            f"itad:deals:{country}:{sort}:{limit}:{offset}:{shop_ids}:"
            f"{json.dumps(filters, sort_keys=True)}"
        )

        params = self._params(
            country=country, limit=limit, offset=offset, sort=sort, nondeals="false"
        )
        if filters:
            params["filter"] = json.dumps(filters)
        # фильтруем на стороне API: иначе страницы забиваются отсеянными
        # магазинами и приходят полупустыми
        if shop_ids:
            params["shops"] = shop_ids

        async def fetch() -> dict[str, Any]:
            data = await self.api.get_json(f"{BASE_URL}/deals/v2", params=params)
            return data if isinstance(data, dict) else {}

        raw = await self.cache.get_or_set(key, self.price_ttl, fetch)

        deals: list[Deal] = []
        for item in raw.get("list") or []:
            game = _game(item)
            offer = _offer(item.get("deal") if isinstance(item, dict) else None)
            if game is not None and offer is not None:
                deals.append(Deal(game=game, offer=offer))

        next_offset = raw.get("nextOffset") if raw.get("hasMore") else None
        return deals, int(next_offset) if next_offset is not None else None

    # ---------------------------------------------------------------- топы
    async def top_games(self, kind: str = "waitlisted", limit: int = 10) -> list[Game]:
        """Рейтинги ITAD: что ждут ради скидки или во что больше играют.

        Окна «за сегодня» у эндпоинтов нет — это накопительные рейтинги.
        """
        path = TOP_ENDPOINTS.get(kind, TOP_ENDPOINTS["waitlisted"])
        key = f"itad:top:{kind}:{limit}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(
                f"{BASE_URL}{path}", params=self._params(limit=limit)
            )
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.search_ttl, fetch)
        return [g for g in (_game(item) for item in raw) if g]

    # -------------------------------------------------------------- магазины
    async def shops(self, country: str = "KZ") -> dict[str, Shop]:
        """Справочник магазинов региона, ID → магазин."""
        key = f"itad:shops:{country}"

        async def fetch() -> list[dict[str, Any]]:
            data = await self.api.get_json(
                f"{BASE_URL}/service/shops/v1", params=self._params(country=country)
            )
            return data if isinstance(data, list) else []

        raw = await self.cache.get_or_set(key, self.shops_ttl, fetch)
        return {
            str(s["id"]): Shop(
                id=str(s["id"]),
                name=str(s.get("title") or f"Магазин {s['id']}"),
                source=ITAD,
            )
            for s in raw
            if isinstance(s, dict) and s.get("id")
        }
