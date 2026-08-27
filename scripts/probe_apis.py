"""Шаг 0: разведка внешних API.

Дёргает каждый источник реальным запросом (Cyberpunk 2077, регион KZ)
и печатает сырые ответы, чтобы схемы можно было проверить глазами
до написания клиентов.

Запуск:
    python scripts/probe_apis.py            # все источники
    python scripts/probe_apis.py itad steam # только выбранные

ITAD-ключ берётся из переменной окружения ITAD_API_KEY (или из .env рядом
с проектом). Без ключа блок ITAD пропускается с явным сообщением.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent

# Windows-консоль по умолчанию в cp866 — ₸ и кириллица в ответах её ломают.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

QUERY = "Cyberpunk 2077"
COUNTRY = "KZ"
TIMEOUT = httpx.Timeout(15.0)
MAX_DUMP = 3000  # сколько символов тела печатать целиком


# --------------------------------------------------------------------------- #
# вывод
# --------------------------------------------------------------------------- #
def header(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def step(text: str) -> None:
    print(f"\n--- {text} " + "-" * max(0, 74 - len(text)))


def dump(resp: httpx.Response, *, limit: int = MAX_DUMP) -> Any:
    """Печатает статус + тело ответа, возвращает распарсенный JSON или None."""
    ctype = resp.headers.get("content-type", "?")
    print(f"HTTP {resp.status_code} | {ctype} | {len(resp.content)} bytes")
    for h in ("retry-after", "x-ratelimit-remaining", "x-ratelimit-limit"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    try:
        data = resp.json()
    except ValueError:
        body = resp.text
        print(body[:limit] + ("\n... [обрезано]" if len(body) > limit else ""))
        return None

    body = json.dumps(data, ensure_ascii=False, indent=2)
    print(body[:limit] + ("\n... [обрезано]" if len(body) > limit else ""))
    return data


def load_dotenv() -> None:
    """Минимальный разбор .env — без зависимости от pydantic-settings."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --------------------------------------------------------------------------- #
# 1. IsThereAnyDeal
# --------------------------------------------------------------------------- #
async def probe_itad(client: httpx.AsyncClient) -> None:
    header("1. IsThereAnyDeal API v2  (https://api.isthereanydeal.com)")

    key = os.getenv("ITAD_API_KEY", "").strip()
    if not key:
        print("ПРОПУЩЕНО: не задан ITAD_API_KEY.")
        print("Ключ бесплатный: https://isthereanydeal.com/apps/my/")
        print("Положи его в .env как ITAD_API_KEY=... и перезапусти пробу.")
        return

    base = "https://api.isthereanydeal.com"
    auth = {"key": key}

    step("GET /games/search/v1 — поиск по названию")
    r = await client.get(
        f"{base}/games/search/v1", params={**auth, "title": QUERY, "results": 5}
    )
    found = dump(r)

    game_id: str | None = None
    if isinstance(found, list) and found:
        game_id = found[0].get("id")
        print(
            f"\n>>> взят первый результат: id={game_id!r} title={found[0].get('title')!r}"
        )

    step("GET /games/lookup/v1 — поиск по Steam appid (1091500)")
    r = await client.get(f"{base}/games/lookup/v1", params={**auth, "appid": 1091500})
    lookup = dump(r)
    if game_id is None and isinstance(lookup, dict):
        game_id = (lookup.get("game") or {}).get("id")
        print(f"\n>>> id из lookup: {game_id!r}")

    if not game_id:
        print("\nНе удалось получить ID игры — остальные ITAD-пробы пропущены.")
        return

    # deals=true оставляет только магазины с активной скидкой — для карточки
    # нужны все, поэтому deals=false
    step(f"POST /games/prices/v3?country={COUNTRY}&deals=false — цены по магазинам")
    r = await client.post(
        f"{base}/games/prices/v3",
        params={**auth, "country": COUNTRY, "deals": "false", "capacity": 0},
        json=[game_id],
    )
    dump(r, limit=6000)

    # тело — голый массив ID; {"ids": [...]} даёт 400
    step(f"POST /games/overview/v2?country={COUNTRY} — обзор + исторический минимум")
    r = await client.post(
        f"{base}/games/overview/v2",
        params={**auth, "country": COUNTRY},
        json=[game_id],
    )
    dump(r, limit=6000)

    step(f"POST /games/historylow/v1?country={COUNTRY} — исторический минимум")
    r = await client.post(
        f"{base}/games/historylow/v1",
        params={**auth, "country": COUNTRY},
        json=[game_id],
    )
    dump(r)

    step("GET /games/info/v2 — метаданные (обложка, slug)")
    r = await client.get(f"{base}/games/info/v2", params={**auth, "id": game_id})
    dump(r)

    step(f"GET /deals/v2?country={COUNTRY}&limit=5&sort=-cut — актуальные скидки")
    r = await client.get(
        f"{base}/deals/v2",
        params={**auth, "country": COUNTRY, "limit": 5, "sort": "-cut"},
    )
    dump(r, limit=6000)

    step("GET /service/shops/v1 — справочник магазинов региона")
    r = await client.get(f"{base}/service/shops/v1", params={**auth, "country": COUNTRY})
    dump(r, limit=1500)


# --------------------------------------------------------------------------- #
# 2. CheapShark
# --------------------------------------------------------------------------- #
async def probe_cheapshark(client: httpx.AsyncClient) -> None:
    header("2. CheapShark API 1.0  (https://www.cheapshark.com/api/1.0)")
    base = "https://www.cheapshark.com/api/1.0"

    step("GET /games?title=... — поиск")
    r = await client.get(f"{base}/games", params={"title": QUERY, "limit": 5})
    games = dump(r)

    game_id = games[0].get("gameID") if isinstance(games, list) and games else None
    if game_id:
        print(f"\n>>> gameID={game_id!r}")
        step(f"GET /games?id={game_id} — детали + cheapestPriceEver")
        r = await client.get(f"{base}/games", params={"id": game_id})
        dump(r, limit=6000)

    step("GET /deals?upperPrice=15&sortBy=Savings — скидки")
    r = await client.get(
        f"{base}/deals", params={"upperPrice": 15, "sortBy": "Savings", "pageSize": 3}
    )
    dump(r, limit=4000)

    step("GET /stores — справочник магазинов")
    r = await client.get(f"{base}/stores")
    dump(r, limit=1500)


# --------------------------------------------------------------------------- #
# 3. Steam Store
# --------------------------------------------------------------------------- #
async def probe_steam(client: httpx.AsyncClient) -> None:
    header("3. Steam Store (неофициальный)")

    step("GET steamcommunity.com/actions/SearchApps/<query> — поиск appid")
    r = await client.get(f"https://steamcommunity.com/actions/SearchApps/{QUERY}")
    apps = dump(r, limit=2000)

    appid = apps[0].get("appid") if isinstance(apps, list) and apps else 1091500
    print(f"\n>>> appid={appid!r}")

    step(f"GET store.steampowered.com/api/appdetails?appids={appid}&cc=kz — цена в ₸")
    r = await client.get(
        "https://store.steampowered.com/api/appdetails",
        params={
            "appids": appid,
            "cc": "kz",
            "l": "russian",
            "filters": "price_overview,basic",
        },
    )
    dump(r, limit=4000)


# --------------------------------------------------------------------------- #
# 4. Epic Games
# --------------------------------------------------------------------------- #
async def probe_epic(client: httpx.AsyncClient) -> None:
    header("4. Epic Games — бесплатные раздачи")

    r = await client.get(
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions",
        params={"locale": "ru", "country": COUNTRY, "allowCountries": COUNTRY},
    )
    data = dump(r, limit=1200)

    # полный дамп слишком большой — печатаем выжимку по каждой игре
    if isinstance(data, dict):
        elements = (
            data.get("data", {})
            .get("Catalog", {})
            .get("searchStore", {})
            .get("elements", [])
        )
        print(f"\n>>> элементов в каталоге: {len(elements)}")
        for el in elements[:8]:
            price = el.get("price", {}).get("totalPrice", {})
            promos = el.get("promotions") or {}
            print(
                json.dumps(
                    {
                        "title": el.get("title"),
                        "productSlug": el.get("productSlug"),
                        "offerMappings": el.get("offerMappings"),
                        "discountPrice": price.get("discountPrice"),
                        "originalPrice": price.get("originalPrice"),
                        "currencyCode": price.get("currencyCode"),
                        "promotionalOffers": promos.get("promotionalOffers"),
                        "upcomingPromotionalOffers": promos.get(
                            "upcomingPromotionalOffers"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


# --------------------------------------------------------------------------- #
PROBES = {
    "itad": probe_itad,
    "cheapshark": probe_cheapshark,
    "steam": probe_steam,
    "epic": probe_epic,
}


async def main() -> int:
    load_dotenv()
    wanted = [a.lower() for a in sys.argv[1:]] or list(PROBES)
    unknown = [w for w in wanted if w not in PROBES]
    if unknown:
        print(f"Неизвестные источники: {unknown}. Доступно: {list(PROBES)}")
        return 2

    failures: list[str] = []
    headers = {"User-Agent": "gamedeals-bot/0.1 (api probe)"}
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers=headers, follow_redirects=True
    ) as client:
        for name in wanted:
            try:
                await PROBES[name](client)
            except Exception as exc:  # проба не должна падать целиком
                failures.append(name)
                print(f"\n!!! {name}: {type(exc).__name__}: {exc}")

    header("ИТОГ")
    for name in wanted:
        print(f"  {name:12} {'ОШИБКА' if name in failures else 'ответил'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
