"""Канонические имена магазинов и фильтр по ним.

Один и тот же магазин приходит из разных источников под разными именами и
ID: Steam у ITAD — `61`, у CheapShark — `1`, у нашего Steam-клиента —
`steam`. Сравнивать по ID нельзя, поэтому сводим всё к короткому ключу
по названию.

Список составлен по живому ответу `/service/shops/v1` (33 магазина), в
подборку вынесены те, у кого больше всего игр и скидок.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from bot.services.models import Offer

# Пробелы, точки и дефисы при сравнении названий отбрасываем: «GOG.com»,
# «Green Man Gaming» и «GreenManGaming» — один магазин. Буквы оставляем
# любые, включая нелатинские: иначе магазин с кириллическим названием
# нормализуется в пустую строку и сольётся со всеми такими же.
_NOISE = re.compile(r"[\W_]+", re.UNICODE)


class ShopInfo(NamedTuple):
    key: str
    title: str  # как показываем в кнопках
    aliases: frozenset[str]  # нормализованные написания из разных источников


def _norm(name: str) -> str:
    return _NOISE.sub("", name.casefold())


def _info(key: str, title: str, *aliases: str) -> ShopInfo:
    names = {title, *aliases}
    return ShopInfo(key, title, frozenset(_norm(n) for n in names))


# Порядок задаёт раскладку кнопок в настройках
KNOWN_SHOPS: tuple[ShopInfo, ...] = (
    _info("steam", "Steam"),
    _info("epic", "Epic Games", "Epic Game Store", "Epic Games Store", "Epic"),
    _info("gog", "GOG", "GOG.com"),
    _info("humble", "Humble Store", "Humble Bundle", "Humble"),
    _info("fanatical", "Fanatical"),
    _info("gmg", "GreenManGaming", "Green Man Gaming"),
    _info("gamesplanet", "GamesPlanet", "GamesPlanet US", "GamesPlanet UK",
          "GamesPlanet DE", "GamesPlanet FR"),
    _info("gamebillet", "GameBillet"),
    _info("gamersgate", "GamersGate"),
    _info("indiegala", "IndieGala Store", "IndieGala"),
    _info("microsoft", "Microsoft Store", "Xbox", "Windows Store"),
    _info("ubisoft", "Ubisoft Store", "Ubisoft Connect", "Uplay"),
)

BY_KEY: dict[str, ShopInfo] = {shop.key: shop for shop in KNOWN_SHOPS}

_BY_ALIAS: dict[str, str] = {
    alias: shop.key for shop in KNOWN_SHOPS for alias in shop.aliases
}


def shop_key(name: str) -> str:
    """Канонический ключ магазина по его названию из любого источника.

    Незнакомый магазин получает ключ из собственного имени — так он не
    сольётся с другими и останется фильтруемым.
    """
    normalized = _norm(name)
    return _BY_ALIAS.get(normalized, normalized)


def title_for(key: str) -> str:
    shop = BY_KEY.get(key)
    return shop.title if shop else key


def parse_selection(raw: str | None) -> set[str]:
    """Разбирает сохранённую в БД строку настроек. Пусто — значит все."""
    if not raw:
        return set()
    return {part for part in (p.strip() for p in raw.split(",")) if part}


def dump_selection(keys: set[str]) -> str:
    """Обратно в строку для хранения. Пустое множество — все магазины."""
    return ",".join(sorted(keys))


def filter_offers(offers: list[Offer], selected: set[str]) -> list[Offer]:
    """Оставляет предложения только выбранных магазинов.

    Пустой выбор означает «все». Если фильтр не оставил ничего, возвращаем
    исходный список: показать цену не из любимого магазина полезнее, чем
    пустую карточку.
    """
    if not selected:
        return offers
    kept = [o for o in offers if shop_key(o.shop.name) in selected]
    return kept or offers


def itad_shop_ids(selected: set[str], directory: dict[str, object]) -> list[int]:
    """Переводит выбранные ключи в ID магазинов ITAD.

    Нужен, чтобы фильтровать скидки на стороне API: иначе постраничная
    выдача набивается отсеянными магазинами и страницы приходят пустыми.
    """
    if not selected:
        return []

    ids: list[int] = []
    for shop_id, shop in directory.items():
        name = getattr(shop, "name", None)
        if name and shop_key(name) in selected:
            try:
                ids.append(int(shop_id))
            except (TypeError, ValueError):
                continue
    return sorted(ids)
