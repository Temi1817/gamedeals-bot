"""Сборка клиентов. Один HTTP-клиент и один кэш на весь процесс."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from bot.config import Settings
from bot.services.aggregator import Aggregator
from bot.services.cache import TTLCache
from bot.services.cheapshark import CheapSharkClient
from bot.services.epic import EpicClient
from bot.services.gog import GogClient
from bot.services.http import ApiClient, create_client
from bot.services.itad import ItadClient
from bot.services.rates import RatesClient
from bot.services.steam import SteamClient
from bot.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Services:
    """Всё, что нужно хендлерам, плюс ресурсы для закрытия на выходе."""

    aggregator: Aggregator
    cache: TTLCache
    http: httpx.AsyncClient

    async def close(self) -> None:
        await self.http.aclose()


def build_services(settings: Settings) -> Services:
    http = create_client(settings.http_timeout)
    cache = TTLCache()

    def api(source: str) -> ApiClient:
        return ApiClient(http, source=source, max_retries=settings.http_max_retries)

    itad_key = settings.itad_key
    if itad_key is None:
        # Без ключа бот живой, но цены будут только от Steam, Epic
        # и CheapShark — то есть без GOG, Humble, Fanatical и прочих.
        log.warning("itad_key_missing")

    itad = (
        ItadClient(
            api("itad"),
            cache,
            itad_key,
            search_ttl=settings.cache_ttl_search,
            price_ttl=settings.cache_ttl_prices,
            shops_ttl=settings.cache_ttl_shops,
        )
        if itad_key
        else None
    )

    aggregator = Aggregator(
        itad=itad,
        steam=SteamClient(
            api("steam"),
            cache,
            search_ttl=settings.cache_ttl_search,
            price_ttl=settings.cache_ttl_prices,
        ),
        epic=EpicClient(api("epic"), cache, ttl=settings.cache_ttl_search),
        gog=GogClient(
            api("gog"),
            cache,
            search_ttl=settings.cache_ttl_search,
            price_ttl=settings.cache_ttl_prices,
        ),
        cheapshark=CheapSharkClient(
            api("cheapshark"),
            cache,
            search_ttl=settings.cache_ttl_search,
            price_ttl=settings.cache_ttl_prices,
            shops_ttl=settings.cache_ttl_shops,
        ),
        rates=RatesClient(api("rates"), cache, ttl=settings.cache_ttl_shops),
        default_currency=settings.default_currency,
    )

    return Services(aggregator=aggregator, cache=cache, http=http)
