"""Что показывает `/list` в строке отслеживания.

Список жил своей жизнью: цену он брал минимальную по всем магазинам, а
подписывал её магазином из подписки. Так рядом с надписью «Steam»
оказывалась цена Epic — вместе со ссылкой на Epic. Отбор вынесен в
`pick_snapshot`, рендер — в `price_text`, и проверяются они отдельно.
"""

from __future__ import annotations

from decimal import Decimal

from bot.db.models import PriceSnapshot, Shop, User, Watch
from bot.handlers.watchlist import pick_snapshot, price_text
from bot.services.models import CHEAPSHARK, ITAD, STEAM

STEAM_URL = "https://store.steampowered.com/app/1817070"
EPIC_URL = "https://store.epicgames.com/en-US/p/marvels-spider-man-remastered"


def snapshot(
    name: str,
    price: str,
    *,
    source: str = ITAD,
    cut: int = 0,
    regular: str | None = None,
    url: str | None = None,
) -> PriceSnapshot:
    row = PriceSnapshot(
        game_id=1,
        shop_id=1,
        price=Decimal(price),
        regular_price=Decimal(regular) if regular else None,
        cut=cut,
        currency="KZT",
        url=url,
    )
    row.shop = Shop(source=source, external_id=name.lower(), name=name)
    return row


def watch_for(shop_key: str = "") -> Watch:
    watch = Watch(
        user_id=1,
        game_id=1,
        target_price=None,
        currency="KZT",
        notify_any_drop=True,
        shop_key=shop_key,
    )
    watch.user = User(tg_id=1, country="KZ", preferred_shops="")
    return watch


ROWS = [
    snapshot("Epic Games", "9400", source="epic", url=EPIC_URL),
    snapshot("Steam", "14000", source=STEAM, url=STEAM_URL),
    snapshot("GOG", "11000"),
]


class TestPickSnapshot:
    def test_watched_shop_wins_over_cheaper_one(self) -> None:
        """Тот самый баг: подписан на Steam, а в списке была цена Epic."""
        picked = pick_snapshot(ROWS, watch_for("steam"), set())

        assert picked is not None
        assert picked.shop.name == "Steam"
        assert picked.price == Decimal("14000")
        assert picked.url == STEAM_URL

    def test_no_price_in_watched_shop_is_not_substituted(self) -> None:
        """Молчаливая подмена соседним магазином и создала путаницу."""
        rows = [r for r in ROWS if r.shop.name != "Steam"]

        assert pick_snapshot(rows, watch_for("steam"), set()) is None

    def test_any_shop_takes_cheapest(self) -> None:
        picked = pick_snapshot(ROWS, watch_for(), set())

        assert picked is not None
        assert picked.shop.name == "Epic Games"

    def test_any_shop_respects_settings_filter(self) -> None:
        picked = pick_snapshot(ROWS, watch_for(), {"steam", "gog"})

        assert picked is not None
        assert picked.shop.name == "GOG"  # дешевле Steam из выбранных

    def test_settings_filter_without_matches_falls_back(self) -> None:
        """Пустой список хуже цены не из любимого магазина."""
        picked = pick_snapshot(ROWS, watch_for(), {"humble"})

        assert picked is not None
        assert picked.shop.name == "Epic Games"

    def test_reseller_key_is_not_a_shop_price(self) -> None:
        rows = [*ROWS, snapshot("Ключи", "500", source=CHEAPSHARK)]

        picked = pick_snapshot(rows, watch_for(), set())

        assert picked is not None
        assert picked.shop.name == "Epic Games"

    def test_same_shop_from_two_sources_takes_cheaper(self) -> None:
        rows = [*ROWS, snapshot("Steam", "12000", source=ITAD)]

        picked = pick_snapshot(rows, watch_for("steam"), set())

        assert picked is not None
        assert picked.price == Decimal("12000")

    def test_no_snapshots_at_all(self) -> None:
        assert pick_snapshot([], watch_for("steam"), set()) is None


class TestPriceText:
    def test_discount_shows_percent_and_old_price(self) -> None:
        row = snapshot("Steam", "7000", cut=50, regular="14000", url=STEAM_URL)

        text = price_text(row, watch_for("steam"), "Steam")

        assert "−50%" in text
        assert "было" in text
        assert STEAM_URL in text

    def test_discount_without_regular_price_still_shows_percent(self) -> None:
        row = snapshot("Steam", "7000", cut=50, url=STEAM_URL)

        text = price_text(row, watch_for("steam"), "Steam")

        assert "−50%" in text
        assert "было" not in text

    def test_full_price_says_there_is_no_discount(self) -> None:
        row = snapshot("Steam", "14000", url=STEAM_URL)

        text = price_text(row, watch_for("steam"), "Steam")

        assert "скидки пока нет" in text
        assert "%" not in text

    def test_missing_price_names_the_watched_shop(self) -> None:
        text = price_text(None, watch_for("steam"), "Steam")

        assert text == "в Steam цены нет"

    def test_missing_price_without_watched_shop(self) -> None:
        text = price_text(None, watch_for(), "любой магазин")

        assert text == "цена ещё не замерена"
