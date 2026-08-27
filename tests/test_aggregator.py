"""Сведение источников в единый список предложений.

Клиенты здесь подменены заглушками: проверяем логику агрегации, а разбор
ответов каждого API покрыт в своих файлах.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bot.services.aggregator import (
    STEAM_SHOP_NAMES,
    Aggregator,
    _currency_for,
    _merge,
    _replace_shop,
)
from bot.services.models import (
    CHEAPSHARK,
    ITAD,
    STEAM,
    Deal,
    FreeGame,
    Game,
    HistoricalLow,
    Offer,
    Shop,
)

GAME_ID = "itad-1"
APPID = 1091500

CYBERPUNK = Game(title="Cyberpunk 2077", itad_id=GAME_ID, steam_appid=APPID)

# 1 USD = 500 KZT — круглый курс, чтобы ожидания читались глазами
RATES = {"USD": Decimal(1), "KZT": Decimal(500), "EUR": Decimal("0.5")}


def itad_offer(name: str, amount: str, cut: int = 0) -> Offer:
    return Offer(
        shop=Shop(id=name.lower(), name=name, source=ITAD),
        price=Decimal(amount),
        currency="USD",
        cut=cut,
        url=f"https://itad.link/{name.lower()}/",
    )


def steam_offer(amount: str) -> Offer:
    return Offer(
        shop=Shop(id="steam", name="Steam", source=STEAM),
        price=Decimal(amount),
        currency="KZT",
        url="https://store.steampowered.com/app/1091500/",
    )


def reseller_offer(amount: str) -> Offer:
    return Offer(
        shop=Shop(id="11", name="Humble Store", source=CHEAPSHARK),
        price=Decimal(amount),
        currency="USD",
        is_reseller=True,
        approximate=True,
    )


# --------------------------------------------------------------------------- #
# заглушки клиентов
# --------------------------------------------------------------------------- #
class FakeItad:
    def __init__(
        self,
        offers: list[Offer] | None = None,
        low: HistoricalLow | None = None,
        games: list[Game] | None = None,
        fail: bool = False,
    ) -> None:
        self._offers = offers or []
        self._low = low
        self._games = games if games is not None else [CYBERPUNK]
        self.fail = fail
        self.deals_calls: list[dict[str, Any]] = []

    async def search(self, query: str, limit: int = 5) -> list[Game]:
        if self.fail:
            raise RuntimeError("ITAD лежит")
        return self._games

    async def lookup_by_appid(self, appid: int) -> Game | None:
        return CYBERPUNK

    async def info(self, game_id: str) -> Game | None:
        return CYBERPUNK

    async def prices(
        self, ids: list[str], country: str = "KZ", *, only_deals: bool = False
    ) -> dict[str, list[Offer]]:
        if self.fail:
            raise RuntimeError("ITAD лежит")
        return {ids[0]: list(self._offers)}

    async def history_lows(
        self, ids: list[str], country: str = "KZ"
    ) -> dict[str, HistoricalLow]:
        if self.fail:
            raise RuntimeError("ITAD лежит")
        return {ids[0]: self._low} if self._low else {}

    async def deals(self, country: str = "KZ", **kwargs: Any) -> Any:
        if self.fail:
            raise RuntimeError("ITAD лежит")
        self.deals_calls.append(kwargs)
        return [Deal(game=CYBERPUNK, offer=itad_offer("GOG", "17.99", 70))], 10


class FakeSteam:
    def __init__(self, offer: Offer | None = None) -> None:
        self._offer = offer

    async def search(self, query: str, limit: int = 5) -> list[Game]:
        return [Game(title="Из Steam", steam_appid=APPID)]

    async def prices(self, appids: list[int], country: str = "KZ") -> dict[int, Offer]:
        return {appids[0]: self._offer} if self._offer else {}


class FakeCheapShark:
    def __init__(
        self, offers: list[Offer] | None = None, low: HistoricalLow | None = None
    ) -> None:
        self._offers = offers or []
        self._low = low

    async def search(self, query: str, limit: int = 5) -> list[Game]:
        return [Game(title="Из CheapShark", cheapshark_id="cs-1")]

    async def offers(self, game_id: str) -> Any:
        return None, list(self._offers), self._low

    async def deals(self, **kwargs: Any) -> list[Deal]:
        return [Deal(game=CYBERPUNK, offer=reseller_offer("9.99"))]


class FakeEpic:
    def __init__(self, games: list[FreeGame] | None = None) -> None:
        self._games = games or []

    async def free_games(self, country: str = "KZ", **kwargs: Any) -> list[FreeGame]:
        return list(self._games)


class FakeRates:
    def __init__(self, available: bool = True) -> None:
        self.available = available

    async def convert(self, amount: Decimal, source: str, target: str) -> Decimal | None:
        source, target = source.upper(), target.upper()
        if source == target:
            return amount
        if not self.available:
            return None
        rate_from, rate_to = RATES.get(source), RATES.get(target)
        if not rate_from or not rate_to:
            return None
        return (amount / rate_from * rate_to).quantize(Decimal("0.01"))


def build(
    *,
    itad: Any = None,
    steam: Any = None,
    cheapshark: Any = None,
    epic: Any = None,
    rates: Any = None,
) -> Aggregator:
    return Aggregator(
        itad=itad,
        steam=steam or FakeSteam(),
        cheapshark=cheapshark or FakeCheapShark(),
        epic=epic or FakeEpic(),
        rates=rates or FakeRates(),
    )


# --------------------------------------------------------------------------- #
# карточка игры
# --------------------------------------------------------------------------- #
class TestGameDetails:
    async def test_steam_price_replaces_itad_dollars(self) -> None:
        """Главное правило: у ITAD нет цен для KZ, у Steam есть настоящие.

        ITAD для Cyberpunk отдаёт $59.99 (≈30 000 ₸ по курсу теста), а Steam
        в Казахстане реально просит 17 999 ₸. В карточке должна остаться
        родная цена Steam, а не пересчитанная долларовая.
        """
        agg = build(
            itad=FakeItad([itad_offer("Steam", "59.99"), itad_offer("GOG", "17.99")]),
            steam=FakeSteam(steam_offer("17999")),
        )

        details = await agg.game_details(CYBERPUNK)

        steam = next(o for o in details.offers if o.shop.name == "Steam")
        assert steam.price == Decimal("17999")
        assert steam.currency == "KZT"
        assert steam.converted_price is None  # это и есть валюта региона
        # долларовой записи Steam от ITAD остаться не должно
        assert sum(o.shop.name == "Steam" for o in details.offers) == 1

    async def test_offers_sorted_across_currencies(self) -> None:
        """Сортировка идёт по цене в валюте региона, а не по сырым числам."""
        agg = build(
            itad=FakeItad(
                [itad_offer("Humble Store", "59.99"), itad_offer("GOG", "17.99")]
            ),
            steam=FakeSteam(steam_offer("17999")),
        )

        details = await agg.game_details(CYBERPUNK)

        # GOG $17.99 = 8 995 ₸ < Steam 17 999 ₸ < Humble $59.99 = 29 995 ₸
        assert [o.shop.name for o in details.offers] == [
            "GOG",
            "Steam",
            "Humble Store",
        ]

    async def test_converted_price_is_filled_and_marked(self) -> None:
        agg = build(itad=FakeItad([itad_offer("GOG", "17.99", 70)]))

        details = await agg.game_details(CYBERPUNK)

        gog = details.offers[0]
        assert gog.price == Decimal("17.99")
        assert gog.currency == "USD"
        assert gog.converted_price == Decimal("8995.00")
        assert gog.converted_currency == "KZT"
        assert gog.approximate is True

    async def test_historical_low_converted_to_region_currency(self) -> None:
        """Иначе вердикт сравнит тенге с долларами и наврёт в разы."""
        agg = build(
            itad=FakeItad(
                [itad_offer("GOG", "17.99", 70)],
                low=HistoricalLow(price=Decimal("17.99"), currency="USD", shop="GOG"),
            ),
            steam=FakeSteam(steam_offer("17999")),
        )

        details = await agg.game_details(CYBERPUNK)

        assert details.historical_low is not None
        assert details.historical_low.currency == "KZT"
        assert details.historical_low.price == Decimal("8995.00")
        assert details.historical_low.shop == "GOG"

    async def test_sources_are_reported(self) -> None:
        agg = build(
            itad=FakeItad([itad_offer("GOG", "17.99")]),
            steam=FakeSteam(steam_offer("17999")),
        )

        details = await agg.game_details(CYBERPUNK)

        assert set(details.sources) == {ITAD, STEAM}

    async def test_falls_back_to_cheapshark_when_itad_down(self) -> None:
        agg = build(
            itad=FakeItad(fail=True),
            cheapshark=FakeCheapShark(
                [reseller_offer("29.99")],
                low=HistoricalLow(price=Decimal("17.99"), currency="USD"),
            ),
        )

        details = await agg.game_details(CYBERPUNK)

        assert CHEAPSHARK in details.sources
        assert ITAD not in details.sources
        assert len(details.offers) == 1
        assert details.offers[0].is_reseller

    async def test_works_without_itad_at_all(self) -> None:
        """Без ключа ITAD бот обязан оставаться живым."""
        agg = build(itad=None, steam=FakeSteam(steam_offer("17999")))

        details = await agg.game_details(CYBERPUNK)

        assert [o.shop.name for o in details.offers] == ["Steam"]
        assert details.sources == (STEAM,)

    async def test_reseller_never_wins_best_offer(self) -> None:
        """Ключ реселлера за $9.99 дешевле всех, но лучшей ценой не считается."""
        agg = build(
            itad=FakeItad([itad_offer("GOG", "17.99")]),
            steam=FakeSteam(steam_offer("17999")),
        )
        details = await agg.game_details(CYBERPUNK)
        with_reseller = [*details.offers, reseller_offer("1.00")]
        details = type(details)(
            game=details.game,
            offers=with_reseller,
            historical_low=details.historical_low,
            sources=details.sources,
        )

        best = details.best_offer

        assert best is not None
        assert best.is_reseller is False
        assert best.shop.name == "GOG"

    async def test_missing_rates_keep_native_price(self) -> None:
        """Курс недоступен — показываем как есть, но не падаем."""
        agg = build(
            itad=FakeItad([itad_offer("GOG", "17.99")]), rates=FakeRates(available=False)
        )

        details = await agg.game_details(CYBERPUNK)

        assert details.offers[0].converted_price is None
        assert details.offers[0].price == Decimal("17.99")

    async def test_steam_price_absent_leaves_itad_entry(self) -> None:
        agg = build(itad=FakeItad([itad_offer("Steam", "59.99")]), steam=FakeSteam(None))

        details = await agg.game_details(CYBERPUNK)

        assert details.offers[0].shop.source == ITAD
        assert details.offers[0].currency == "USD"


# --------------------------------------------------------------------------- #
# поиск
# --------------------------------------------------------------------------- #
class TestSearch:
    async def test_itad_is_primary(self) -> None:
        agg = build(itad=FakeItad())

        assert (await agg.search("Cyberpunk"))[0].title == "Cyberpunk 2077"

    async def test_falls_back_to_steam(self) -> None:
        agg = build(itad=FakeItad(fail=True))

        assert (await agg.search("Cyberpunk"))[0].title == "Из Steam"

    async def test_empty_itad_result_falls_through(self) -> None:
        agg = build(itad=FakeItad(games=[]))

        assert (await agg.search("Cyberpunk"))[0].title == "Из Steam"


# --------------------------------------------------------------------------- #
# скидки
# --------------------------------------------------------------------------- #
class TestDeals:
    async def test_max_price_converted_to_source_currency(self) -> None:
        """Пользователь пишет 5000 ₸, а ITAD фильтрует в долларах."""
        itad = FakeItad()
        agg = build(itad=itad)

        await agg.deals("KZ", max_price=Decimal("5000"))

        assert itad.deals_calls[0]["max_price"] == Decimal("10.00")

    async def test_deals_are_converted_for_display(self) -> None:
        agg = build(itad=FakeItad())

        deals, next_offset = await agg.deals("KZ")

        assert deals[0].offer.converted_price == Decimal("8995.00")
        assert next_offset == 10

    async def test_falls_back_to_cheapshark(self) -> None:
        agg = build(itad=FakeItad(fail=True))

        deals, next_offset = await agg.deals("KZ")

        assert deals[0].offer.is_reseller
        assert next_offset is None


# --------------------------------------------------------------------------- #
# помощники
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("country", "expected"),
    [("KZ", "KZT"), ("kz", "KZT"), ("US", "USD"), ("PL", "PLN"), ("XX", "KZT")],
)
def test_currency_for_country(country: str, expected: str) -> None:
    assert _currency_for(country, "KZT") == expected


def test_merge_fills_missing_ids() -> None:
    base = Game(title="Cyberpunk 2077", steam_appid=APPID)
    extra = Game(title="Другое имя", itad_id=GAME_ID, image_url="https://img")

    merged = _merge(base, extra)

    assert merged.title == "Cyberpunk 2077"  # своё название не затираем
    assert merged.steam_appid == APPID
    assert merged.itad_id == GAME_ID
    assert merged.image_url == "https://img"


def test_replace_shop_keeps_other_shops() -> None:
    offers = [itad_offer("GOG", "17.99"), itad_offer("Steam", "59.99")]

    result = _replace_shop(offers, steam_offer("17999"), STEAM_SHOP_NAMES)

    assert [o.shop.name for o in result] == ["GOG", "Steam"]
    assert result[-1].currency == "KZT"


def test_replace_shop_is_case_insensitive() -> None:
    offers = [itad_offer("STEAM", "59.99")]

    result = _replace_shop(offers, steam_offer("17999"), STEAM_SHOP_NAMES)

    assert len(result) == 1
    assert result[0].currency == "KZT"
