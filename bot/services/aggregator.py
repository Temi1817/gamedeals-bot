"""Агрегатор: сводит четыре источника в один список предложений.

Кто за что отвечает:

* **ITAD** — широта охвата. Даёт GOG, Humble, Fanatical, GreenManGaming,
  GamesPlanet и остальных, плюс исторический минимум. Для Казахстана
  отдаёт доллары — региональных цен для KZ у него нет.
* **Steam** — настоящая цена в тенге. Это не украшение: для Cyberpunk ITAD
  показывает $59.99 (≈27 400 ₸), а Steam в KZ реально просит 17 999 ₸.
  Поэтому цену Steam из ITAD мы **заменяем** на родную.
* **Epic** — только раздачи. Поштучных цен по играм публичного API нет,
  так что цена Epic остаётся долларовой из ITAD.
* **CheapShark** — подстраховка, если ITAD недоступен. Всегда USD и ключи
  реселлеров, поэтому помечается отдельно и никогда не считается «лучшей
  ценой».

Падение любого источника не роняет ответ: собираем что дошло и сообщаем,
какие источники ответили.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from bot.services.cheapshark import CheapSharkClient
from bot.services.epic import EpicClient
from bot.services.gog import GogClient
from bot.services.itad import ItadClient
from bot.services.models import (
    CHEAPSHARK,
    EPIC,
    ITAD,
    KEY_CHEAPSHARK,
    KEY_ITAD,
    KEY_SEP,
    KEY_STEAM,
    STEAM,
    Deal,
    FreeGame,
    Game,
    GameDetails,
    HistoricalLow,
    Offer,
    PricePoint,
    Source,
)
from bot.services.rates import RatesClient
from bot.services.shops import filter_offers, itad_shop_ids
from bot.services.steam import SteamClient
from bot.utils.logging import get_logger

log = get_logger(__name__)

# Как магазин называется в справочнике ITAD — по имени ищем, что заменить
STEAM_SHOP_NAMES = frozenset({"steam"})
GOG_SHOP_NAMES = frozenset({"gog", "gog.com"})
EPIC_SHOP_NAMES = frozenset({"epic game store", "epic games store", "epic"})
GOG = "gog"


class Aggregator:
    """Единая точка входа для хендлеров."""

    def __init__(
        self,
        *,
        steam: SteamClient,
        epic: EpicClient,
        cheapshark: CheapSharkClient,
        rates: RatesClient,
        gog: GogClient | None = None,
        itad: ItadClient | None = None,
        default_currency: str = "KZT",
    ) -> None:
        self.itad = itad
        self.steam = steam
        self.epic = epic
        self.cheapshark = cheapshark
        self.gog = gog
        self.rates = rates
        self.default_currency = default_currency

    # ----------------------------------------------------------------- поиск
    async def search(self, query: str, limit: int = 5) -> list[Game]:
        """Поиск по названию. ITAD — основной, остальные подстраховывают."""
        if self.itad is not None:
            try:
                games = await self.itad.search(query, limit=limit)
                if games:
                    return games
            except Exception as exc:
                log.warning("itad_search_failed", error=str(exc), query=query)

        # ITAD недоступен или ничего не нашёл — идём к Steam, затем CheapShark
        for name, client in (("steam", self.steam), ("cheapshark", self.cheapshark)):
            try:
                games = await client.search(query, limit=limit)
                if games:
                    return games
            except Exception as exc:
                log.warning(f"{name}_search_failed", error=str(exc), query=query)

        return []

    async def resolve_game(self, key: str) -> Game | None:
        """Восстанавливает игру по ключу из callback-данных (`Game.key`).

        В `callback_data` Telegram влезает только 64 байта, поэтому карточка
        носит с собой не игру, а её идентификатор — название и обложку
        добираем здесь.
        """
        prefix, _, value = key.partition(KEY_SEP)
        if not value:
            return None

        if prefix == KEY_ITAD:
            game = Game(title="", itad_id=value)
        elif prefix == KEY_STEAM:
            try:
                game = Game(title="", steam_appid=int(value))
            except ValueError:
                return None
        elif prefix == KEY_CHEAPSHARK:
            game = Game(title="", cheapshark_id=value)
        else:
            return None

        game = await self._enrich(game)
        if game.title:
            return game

        # ITAD не помог (нет ключа или не знает игру) — спросим Steam
        if game.steam_appid is not None:
            try:
                found = await self.steam.details(game.steam_appid)
            except Exception as exc:
                log.warning("steam_details_failed", error=str(exc))
                found = None
            if found is not None:
                return _merge(found, game)

        return None

    # -------------------------------------------------------------- карточка
    async def game_details(
        self, game: Game, country: str = "KZ", shops: set[str] | None = None
    ) -> GameDetails:
        """Собирает карточку: цены по магазинам плюс исторический минимум.

        `shops` — канонические ключи выбранных магазинов; пусто значит все.
        """
        currency = _currency_for(country, self.default_currency)
        sources: list[Source] = []

        game = await self._enrich(game)

        offers, low, itad_ok = await self._from_itad(game, country)
        if itad_ok:
            sources.append(ITAD)

        if not offers:
            offers, low = await self._from_cheapshark(game, low)
            if offers:
                sources.append(CHEAPSHARK)

        # У Steam и GOG есть свои цены для региона, а ITAD их не знает и
        # отдаёт международные. Для GOG разница доходит до двух раз, поэтому
        # обе цены берём у самих магазинов.
        steam_offer = await self._steam_offer(game, country)
        if steam_offer is not None:
            offers = _replace_shop(offers, steam_offer, STEAM_SHOP_NAMES)
            sources.append(STEAM)

        gog_offer = await self._gog_offer(game, country)
        if gog_offer is not None:
            offers = _replace_shop(offers, gog_offer, GOG_SHOP_NAMES)
            sources.append(GOG)

        epic_offer = await self._epic_offer(game, country)
        if epic_offer is not None:
            offers = _replace_shop(offers, epic_offer, EPIC_SHOP_NAMES)
            sources.append(EPIC)

        offers = filter_offers(offers, shops or set())
        offers = await self._convert_all(offers, currency)
        offers.sort(key=lambda o: (o.is_reseller, o.sort_key))

        low = await self._convert_low(low, currency)

        return GameDetails(
            game=game, offers=offers, historical_low=low, sources=tuple(sources)
        )

    async def _enrich(self, game: Game) -> Game:
        """Дотягивает недостающие ID: без itad_id не будет цен по магазинам,
        без steam_appid не будет настоящей цены в тенге."""
        if self.itad is None:
            return game

        if game.itad_id is None and game.steam_appid is not None:
            try:
                found = await self.itad.lookup_by_appid(game.steam_appid)
                if found is not None:
                    game = _merge(game, found)
            except Exception as exc:
                log.warning("itad_lookup_failed", error=str(exc))

        if game.steam_appid is None and game.itad_id is not None:
            try:
                info = await self.itad.info(game.itad_id)
                if info is not None:
                    game = _merge(game, info)
            except Exception as exc:
                log.warning("itad_info_failed", error=str(exc))

        return game

    async def _from_itad(
        self, game: Game, country: str
    ) -> tuple[list[Offer], HistoricalLow | None, bool]:
        if self.itad is None or game.itad_id is None:
            return [], None, False

        try:
            prices, lows = await asyncio.gather(
                self.itad.prices([game.itad_id], country=country),
                self.itad.history_lows([game.itad_id], country=country),
            )
        except Exception as exc:
            log.warning("itad_prices_failed", error=str(exc), game=game.title)
            return [], None, False

        return list(prices.get(game.itad_id, [])), lows.get(game.itad_id), True

    async def _from_cheapshark(
        self, game: Game, low: HistoricalLow | None
    ) -> tuple[list[Offer], HistoricalLow | None]:
        """Запасной источник, когда ITAD молчит."""
        game_id = game.cheapshark_id
        if game_id is None:
            try:
                found = await self.cheapshark.search(game.title, limit=1)
            except Exception as exc:
                log.warning("cheapshark_search_failed", error=str(exc))
                return [], low
            if not found:
                return [], low
            game_id = found[0].cheapshark_id

        if game_id is None:
            return [], low

        try:
            _, offers, cs_low = await self.cheapshark.offers(game_id)
        except Exception as exc:
            log.warning("cheapshark_offers_failed", error=str(exc))
            return [], low

        return offers, low or cs_low

    async def _epic_offer(self, game: Game, country: str) -> Offer | None:
        """Реальная цена Epic для региона. Витрина покрывает не весь
        каталог, поэтому для многих игр вернётся None и останется ITAD."""
        if not game.title:
            return None
        try:
            return await self.epic.offer_for(game.title, country=country)
        except Exception as exc:
            log.warning("epic_price_failed", error=str(exc), game=game.title)
            return None

    async def _gog_offer(self, game: Game, country: str) -> Offer | None:
        """Реальная цена GOG для региона вместо международной из ITAD."""
        if self.gog is None or not game.title:
            return None
        try:
            found = await self.gog.offer_for(game.title, country=country)
        except Exception as exc:
            log.warning("gog_price_failed", error=str(exc), game=game.title)
            return None
        return found[1] if found else None

    async def _steam_offer(self, game: Game, country: str) -> Offer | None:
        if game.steam_appid is None:
            return None
        try:
            prices = await self.steam.prices([game.steam_appid], country=country)
        except Exception as exc:
            log.warning("steam_price_failed", error=str(exc))
            return None
        return prices.get(game.steam_appid)

    # -------------------------------------------------------------- история
    async def price_history(
        self,
        game: Game,
        country: str = "KZ",
        *,
        days: int = 365,
        own: list[PricePoint] | None = None,
        limit: int = 12,
    ) -> list[PricePoint]:
        """История скидок: данные ITAD плюс наши собственные замеры.

        ITAD знает историю за годы, но международную — её пересчитываем и
        помечаем приблизительной. Наши замеры по Steam, GOG и Epic уже в
        валюте региона и идут как точные.

        Одна дата — одна точка: в один день скидка обычно приходит сразу в
        несколько магазинов, и четыре одинаковые строки только мешают.
        """
        currency = _currency_for(country, self.default_currency)
        points: list[PricePoint] = list(own or [])

        if self.itad is not None and game.itad_id:
            try:
                raw = await self.itad.price_history(
                    game.itad_id, country=country, days=days
                )
            except Exception as exc:
                log.warning("itad_history_failed", error=str(exc), game=game.title)
                raw = []
            for point in raw:
                points.append(await self._convert_point(point, currency))

        return _dedupe_by_day(points)[-limit:]

    async def _convert_point(self, point: PricePoint, currency: str) -> PricePoint:
        if point.currency.upper() == currency.upper():
            return point
        converted = await self.rates.convert(point.price, point.currency, currency)
        if converted is None:
            return point
        return PricePoint(
            at=point.at,
            price=converted,
            currency=currency,
            cut=point.cut,
            shop=point.shop,
            exact=False,
        )

    # ------------------------------------------------------------------ топ
    async def store_top(
        self, kind: str = "steam", country: str = "KZ", limit: int = 10
    ) -> list[Deal]:
        """Топ продаж конкретного магазина либо общий рейтинг ITAD.

        У Steam и Epic есть собственные витрины с ценами в валюте региона —
        их и берём, по одному запросу на магазин. `all` отдаёт рейтинг ITAD
        по всем магазинам сразу.
        """
        currency = _currency_for(country, self.default_currency)

        if kind == "steam":
            pairs = await self._safe_top(self.steam.top_sellers, country, limit)
        elif kind == "epic":
            pairs = await self._safe_top(self.epic.top_sellers, country, limit)
        else:
            return await self.top_games("waitlisted", country, limit)

        deals: list[Deal] = []
        for game, offer in pairs:
            converted = await self._convert(offer, currency)
            deals.append(Deal(game=game, offer=converted))
        return deals

    async def _safe_top(
        self, fetch: Any, country: str, limit: int
    ) -> list[tuple[Game, Offer]]:
        try:
            result: list[tuple[Game, Offer]] = await fetch(country=country, limit=limit)
            return result
        except Exception as exc:
            log.warning("store_top_failed", error=str(exc))
            return []

    async def top_games(
        self, kind: str = "waitlisted", country: str = "KZ", limit: int = 10
    ) -> list[Deal]:
        """Рейтинг игр с текущей лучшей ценой по каждой.

        Точные цены магазинов подтягиваем только пакетно — Steam одним
        запросом на все appid, Epic одной витриной. GOG спрашивает цену по
        одной игре, поэтому в топе не участвует: десяток лишних запросов
        того не стоит, а пометка ≈ честно об этом скажет.
        """
        if self.itad is None:
            return []

        currency = _currency_for(country, self.default_currency)
        try:
            games = await self.itad.top_games(kind, limit=limit)
        except Exception as exc:
            log.warning("itad_top_failed", error=str(exc), kind=kind)
            return []
        if not games:
            return []

        ids = [g.itad_id for g in games if g.itad_id]
        try:
            prices = await self.itad.prices(ids, country=country)
        except Exception as exc:
            log.warning("itad_top_prices_failed", error=str(exc))
            prices = {}

        steam_prices = await self._steam_batch(games, country)
        epic_prices = await self._epic_map(country)

        result: list[Deal] = []
        for game in games:
            offers = list(prices.get(game.itad_id or "", []))

            steam_offer = steam_prices.get(game.steam_appid or -1)
            if steam_offer is not None:
                offers = _replace_shop(offers, steam_offer, STEAM_SHOP_NAMES)

            epic_offer = epic_prices.get(game.title.casefold().strip())
            if epic_offer is not None:
                offers = _replace_shop(offers, epic_offer, EPIC_SHOP_NAMES)

            offers = await self._convert_all(offers, currency)
            shops = [o for o in offers if not o.is_reseller]
            if shops:
                result.append(
                    Deal(game=game, offer=min(shops, key=lambda o: o.sort_key))
                )
        return result

    async def _steam_batch(
        self, games: list[Game], country: str
    ) -> dict[int, Offer]:
        """Цены Steam на весь список одним запросом."""
        appids = [g.steam_appid for g in games if g.steam_appid]
        if not appids:
            return {}
        try:
            return await self.steam.prices(appids, country=country)
        except Exception as exc:
            log.warning("steam_batch_failed", error=str(exc))
            return {}

    async def _epic_map(self, country: str) -> dict[str, Offer]:
        try:
            return await self.epic.regional_prices(country=country)
        except Exception as exc:
            log.warning("epic_map_failed", error=str(exc))
            return {}

    # ---------------------------------------------------------------- скидки
    async def deals(
        self,
        country: str = "KZ",
        *,
        limit: int = 10,
        offset: int = 0,
        min_cut: int = 0,
        max_price: Decimal | None = None,
        shops: set[str] | None = None,
    ) -> tuple[list[Deal], int | None]:
        """Топ скидок. `max_price` — в валюте региона, пересчитываем сами."""
        currency = _currency_for(country, self.default_currency)

        if self.itad is None:
            return await self._deals_from_cheapshark(max_price, min_cut, currency)

        # ITAD фильтрует в своей валюте ответа, а для KZ это доллары
        itad_max = await self._to_source_currency(max_price, currency)
        shop_ids = await self._itad_shop_ids(shops or set(), country)

        try:
            deals, next_offset = await self.itad.deals(
                country,
                limit=limit,
                offset=offset,
                sort="-trending",
                min_cut=min_cut,
                max_price=itad_max,
                shops=shop_ids or None,
            )
        except Exception as exc:
            log.warning("itad_deals_failed", error=str(exc))
            return await self._deals_from_cheapshark(max_price, min_cut, currency)

        converted = [
            Deal(game=d.game, offer=await self._convert(d.offer, currency))
            for d in deals
        ]
        return converted, next_offset

    async def _itad_shop_ids(self, shops: set[str], country: str) -> list[int]:
        """Выбранные магазины → ID для параметра `shops` у ITAD."""
        if not shops or self.itad is None:
            return []
        try:
            directory = await self.itad.shops(country)
        except Exception as exc:
            log.warning("itad_shops_failed", error=str(exc))
            return []
        return itad_shop_ids(shops, dict(directory))

    async def _deals_from_cheapshark(
        self, max_price: Decimal | None, min_cut: int, currency: str
    ) -> tuple[list[Deal], int | None]:
        usd_max = await self._to_source_currency(max_price, currency)
        try:
            deals = await self.cheapshark.deals(
                upper_price=usd_max, min_savings=min_cut
            )
        except Exception as exc:
            log.warning("cheapshark_deals_failed", error=str(exc))
            return [], None

        converted = [
            Deal(game=d.game, offer=await self._convert(d.offer, currency))
            for d in deals
        ]
        return converted, None

    # ---------------------------------------------------------------- раздачи
    async def free_games(self, country: str = "KZ") -> list[FreeGame]:
        try:
            return await self.epic.free_games(country=country)
        except Exception as exc:
            log.warning("epic_free_failed", error=str(exc))
            return []

    # ------------------------------------------------------------ конвертация
    async def _convert(self, offer: Offer, currency: str) -> Offer:
        """Дописывает предложению цену в валюте региона."""
        if offer.currency.upper() == currency.upper():
            return offer
        converted = await self.rates.convert(offer.price, offer.currency, currency)
        if converted is None:
            return offer

        # старую цену переводим тем же курсом, а не пропорцией от новой:
        # при скидке −100% новая цена равна нулю и пропорция не считается
        converted_regular: Decimal | None = None
        if offer.regular_price is not None:
            converted_regular = await self.rates.convert(
                offer.regular_price, offer.currency, currency
            )

        return replace_offer(offer, converted, currency, converted_regular)

    async def _convert_all(self, offers: list[Offer], currency: str) -> list[Offer]:
        return [await self._convert(o, currency) for o in offers]

    async def _convert_low(
        self, low: HistoricalLow | None, currency: str
    ) -> HistoricalLow | None:
        """Минимум приводим к валюте региона — иначе вердикт сравнит
        доллары с тенге и наврёт."""
        if low is None or low.currency.upper() == currency.upper():
            return low
        converted = await self.rates.convert(low.price, low.currency, currency)
        if converted is None:
            return low
        return HistoricalLow(
            price=converted,
            currency=currency,
            at=low.at,
            shop=low.shop,
            converted=True,
        )

    async def _to_source_currency(
        self, amount: Decimal | None, currency: str
    ) -> Decimal | None:
        """Порог из тенге в доллары — в них фильтруют ITAD и CheapShark."""
        if amount is None:
            return None
        if currency.upper() == "USD":
            return amount
        return await self.rates.convert(amount, currency, "USD")


# --------------------------------------------------------------------------- #
# помощники
# --------------------------------------------------------------------------- #
def replace_offer(
    offer: Offer,
    converted: Decimal,
    currency: str,
    converted_regular: Decimal | None = None,
) -> Offer:
    """`Offer` заморожен — пересобираем с проставленной конвертацией."""
    return Offer(
        shop=offer.shop,
        price=offer.price,
        currency=offer.currency,
        regular_price=offer.regular_price,
        cut=offer.cut,
        url=offer.url,
        is_reseller=offer.is_reseller,
        approximate=True,  # цена не в валюте региона
        converted_price=converted,
        converted_currency=currency,
        converted_regular_price=converted_regular,
    )


def _dedupe_by_day(points: list[PricePoint]) -> list[PricePoint]:
    """По одной точке на дату — самой дешёвой, а из равных точная важнее."""
    best: dict[date, PricePoint] = {}
    for point in points:
        day = point.at.date()
        current = best.get(day)
        if current is None:
            best[day] = point
            continue
        # ниже цена побеждает; при равной цене выигрывает точная
        if (point.price, not point.exact) < (current.price, not current.exact):
            best[day] = point
    return [best[day] for day in sorted(best)]


def _merge(base: Game, extra: Game) -> Game:
    """Склеивает сведения об игре из двух источников."""
    return Game(
        title=base.title or extra.title,
        itad_id=base.itad_id or extra.itad_id,
        steam_appid=base.steam_appid or extra.steam_appid,
        cheapshark_id=base.cheapshark_id or extra.cheapshark_id,
        slug=base.slug or extra.slug,
        image_url=base.image_url or extra.image_url,
    )


def _replace_shop(
    offers: list[Offer], fresh: Offer, names: frozenset[str]
) -> list[Offer]:
    """Меняет международную цену магазина от ITAD на родную из самой витрины."""
    kept = [
        o
        for o in offers
        if not (o.shop.source == ITAD and o.shop.name.strip().lower() in names)
    ]
    return [*kept, fresh]


def _currency_for(country: str, default: str) -> str:
    """Валюта показа для региона."""
    known = {
        "KZ": "KZT",
        "RU": "RUB",
        "UA": "UAH",
        "US": "USD",
        "GB": "GBP",
        "DE": "EUR",
        "PL": "PLN",
        "TR": "TRY",
    }
    return known.get(country.upper(), default)


__all__ = ["Aggregator"]
