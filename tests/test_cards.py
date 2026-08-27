"""Рендер карточек и списков."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bot.services.models import (
    CHEAPSHARK,
    ITAD,
    STEAM,
    Deal,
    FreeGame,
    Game,
    GameDetails,
    HistoricalLow,
    Offer,
    Shop,
)
from bot.utils import cards
from bot.utils.formatting import NBSP

GOG = "gog"

CYBERPUNK = Game(title="Cyberpunk 2077", itad_id="itad-1", steam_appid=1091500)


def offer(
    name: str,
    amount: str,
    currency: str = "USD",
    *,
    converted: str | None = None,
    converted_regular: str | None = None,
    cut: int = 0,
    regular: str | None = None,
    reseller: bool = False,
    source: str = ITAD,
) -> Offer:
    return Offer(
        shop=Shop(id=name.lower(), name=name, source=source),
        price=Decimal(amount),
        currency=currency,
        regular_price=Decimal(regular) if regular else None,
        cut=cut,
        url=f"https://shop.example/{name.lower()}",
        is_reseller=reseller,
        converted_price=Decimal(converted) if converted else None,
        converted_currency="KZT" if converted else None,
        converted_regular_price=(
            Decimal(converted_regular) if converted_regular else None
        ),
    )


class TestOfferLine:
    def test_native_price_has_no_approximation(self) -> None:
        line = cards.offer_line(offer("Steam", "17999", "KZT", source=STEAM))

        assert f"17{NBSP}999{NBSP}₸" in line
        assert "≈" not in line

    def test_exact_shop_shows_native_currency(self) -> None:
        """У точной цены в скобках видно, сколько спишет магазин."""
        line = cards.offer_line(offer("GOG", "17.99", converted="8231", source=GOG))

        assert f"<b>8{NBSP}231{NBSP}₸</b>" in line
        assert "<i>($17.99)</i>" in line
        assert "≈" not in line

    def test_international_price_is_marked_approximate(self) -> None:
        """У ITAD нет цен для KZ — такую сумму нельзя выдавать за точную."""
        line = cards.offer_line(offer("Humble Store", "59.99", converted="27448"))

        assert f"≈<b>27{NBSP}448{NBSP}₸</b>" in line
        # международную сумму в скобках не показываем: её никто не спишет
        assert "($59.99)" not in line

    def test_discount_and_old_price(self) -> None:
        line = cards.offer_line(offer("GOG", "17.99", cut=70, regular="59.99"))

        assert "−70%" in line
        assert "<s>$59.99</s>" in line

    def test_full_price_comes_before_sale_price(self) -> None:
        """Читается как «было столько — стало столько»."""
        line = cards.offer_line(
            offer("GOG", "17.99", cut=70, regular="59.99", source=GOG)
        )

        assert "<s>$59.99</s> → <b>$17.99</b>" in line
        assert line.index("59.99") < line.index("17.99")

    def test_no_old_price_without_discount(self) -> None:
        assert "<s>" not in cards.offer_line(offer("Steam", "59.99"))

    def test_shop_name_is_a_link(self) -> None:
        line = cards.offer_line(offer("GOG", "17.99"))

        assert '<a href="https://shop.example/gog">GOG</a>' in line

    def test_medals_for_top_three(self) -> None:
        assert cards.offer_line(offer("GOG", "1"), 0).startswith("🥇")
        assert cards.offer_line(offer("GOG", "1"), 1).startswith("🥈")
        assert cards.offer_line(offer("GOG", "1"), 2).startswith("🥉")
        assert cards.offer_line(offer("GOG", "1"), 3).startswith("▫️")

    def test_reseller_is_marked(self) -> None:
        assert "🔑" in cards.offer_line(offer("Humble", "9.99", reseller=True))


class TestGameCard:
    def test_full_card(self) -> None:
        details = GameDetails(
            game=CYBERPUNK,
            offers=[
                offer("GOG", "17.99", converted="8231", cut=70, regular="59.99"),
                offer("Steam", "17999", "KZT", source=STEAM),
            ],
            historical_low=HistoricalLow(
                price=Decimal("8231"),
                currency="KZT",
                at=datetime(2026, 6, 17, tzinfo=UTC),
                shop="GOG",
            ),
            sources=(ITAD, STEAM),
        )

        text = cards.game_card(details)

        assert "Cyberpunk 2077" in text
        assert "Лучшая цена" in text
        assert "Минимум за всё время" in text
        assert "17.06.2026" in text
        assert "GOG" in text

    def test_verdict_uses_converted_price(self) -> None:
        """Иначе доллары сравнятся с тенге и вердикт наврёт в разы."""
        details = GameDetails(
            game=CYBERPUNK,
            offers=[offer("GOG", "17.99", converted="8231")],
            historical_low=HistoricalLow(price=Decimal("8231"), currency="KZT"),
        )

        assert "исторический минимум" in cards.game_card(details)

    def test_currency_note_only_when_converted(self) -> None:
        converted = GameDetails(
            game=CYBERPUNK, offers=[offer("GOG", "17.99", converted="8231")]
        )
        native = GameDetails(
            game=CYBERPUNK, offers=[offer("Steam", "17999", "KZT", source=STEAM)]
        )

        assert "международный прайс" in cards.game_card(converted)
        assert "международный прайс" not in cards.game_card(native)

    def test_resellers_go_to_separate_block(self) -> None:
        details = GameDetails(
            game=CYBERPUNK,
            offers=[
                offer("Steam", "17999", "KZT", source=STEAM),
                offer("Ключи", "9.99", reseller=True, source=CHEAPSHARK),
            ],
        )

        text = cards.game_card(details)

        assert "Ключи у реселлеров" in text
        assert text.index("Лучшая цена") < text.index("Ключи у реселлеров")

    def test_empty_offers(self) -> None:
        text = cards.game_card(GameDetails(game=CYBERPUNK))

        assert "Цен по этой игре сейчас нет" in text
        assert "Лучшая цена" not in text

    def test_best_price_is_hoisted_to_the_top(self) -> None:
        """Ради лучшей цены карточку и открывают — она идёт первой."""
        details = GameDetails(
            game=CYBERPUNK,
            offers=[
                offer("Epic", "5184", "KZT", cut=60, regular="12960", source="epic"),
                offer("Steam", "6600", "KZT", source=STEAM),
            ],
        )

        text = cards.game_card(details)

        assert "Лучшая цена" in text
        assert text.index("Лучшая цена") < text.index("Другие магазины")
        # лучший магазин не дублируется в списке остальных
        assert text.count("Epic") == 1

    def test_long_shop_list_is_collapsed(self) -> None:
        """Полтора десятка магазинов сплошной стеной не читаются."""
        offers = [
            offer(f"Магазин {i}", str(1000 + i * 100), "KZT", source=STEAM)
            for i in range(12)
        ]
        details = GameDetails(game=CYBERPUNK, offers=offers)

        text = cards.game_card(details)

        # один в блоке лучшей цены плюс SHOPS_SHOWN в списке
        assert text.count("Магазин") == 1 + cards.SHOPS_SHOWN + 0
        assert "и ещё 6 магазинов" in text

    def test_collapsed_line_shows_cheapest_of_the_rest(self) -> None:
        offers = [
            offer("A", "100", "KZT", source=STEAM),
            *[
                offer(f"Ш{i}", str(500 + i), "KZT", source=STEAM)
                for i in range(cards.SHOPS_SHOWN + 3)
            ],
        ]
        details = GameDetails(game=CYBERPUNK, offers=offers)

        text = cards.game_card(details)

        assert f"от 505{NBSP}₸" in text

    def test_short_list_has_no_collapse_line(self) -> None:
        details = GameDetails(
            game=CYBERPUNK,
            offers=[
                offer("A", "100", "KZT", source=STEAM),
                offer("B", "200", "KZT", source=STEAM),
            ],
        )

        assert "и ещё" not in cards.game_card(details)

    def test_hero_shows_native_currency_for_exact_shop(self) -> None:
        details = GameDetails(
            game=CYBERPUNK,
            offers=[offer("GOG", "8.99", converted="4113", source=GOG)],
        )

        assert "спишут $8.99" in cards.game_card(details)

    def test_title_is_escaped(self) -> None:
        details = GameDetails(game=Game(title="Tom & Jerry <b>"), offers=[])

        assert "Tom &amp; Jerry &lt;b&gt;" in cards.game_card(details)


class TestSearchResults:
    def test_found(self) -> None:
        assert "Выбери игру" in cards.search_results("Cyberpunk", 3)

    def test_not_found_suggests_english(self) -> None:
        text = cards.search_results("ведьмак", 0)

        assert "ничего не нашлось" in text
        assert "на английском" in text

    def test_query_is_escaped(self) -> None:
        assert "&lt;b&gt;" in cards.search_results("<b>", 0)


class TestDealsList:
    def test_renders_deals(self) -> None:
        deals = [
            Deal(game=CYBERPUNK, offer=offer("GOG", "17.99", converted="8231", cut=70))
        ]

        text = cards.deals_list(deals, page=0, currency="KZT")

        assert "страница 1" in text
        assert "−70%" in text
        assert f"8{NBSP}231{NBSP}₸" in text

    def test_empty(self) -> None:
        assert "Ничего не нашлось" in cards.deals_list([], page=0, currency="KZT")

    def test_old_price_shown_in_display_currency(self) -> None:
        """Старая цена не должна остаться в долларах рядом с тенговой новой."""
        deals = [
            Deal(
                game=CYBERPUNK,
                offer=offer(
                    "GOG",
                    "17.99",
                    converted="8231",
                    converted_regular="27448",
                    cut=70,
                    regular="59.99",
                ),
            )
        ]

        text = cards.deals_list(deals, page=0, currency="KZT")

        assert "$" not in text
        assert f"27{NBSP}448{NBSP}₸" in text
        assert f"8{NBSP}231{NBSP}₸" in text

    def test_free_deal_keeps_old_price_in_display_currency(self) -> None:
        """При −100% цена равна нулю: пропорцией старую цену не вывести,
        поэтому она обязана прийти уже пересчитанной."""
        deals = [
            Deal(
                game=CYBERPUNK,
                offer=offer(
                    "Epic",
                    "0",
                    converted="0",
                    converted_regular="6858",
                    cut=100,
                    regular="14.99",
                ),
            )
        ]

        text = cards.deals_list(deals, page=0, currency="KZT")

        assert "$" not in text
        assert f"<s>6{NBSP}858{NBSP}₸</s>" in text
        assert "бесплатно" in text

    def test_no_arrow_without_discount(self) -> None:
        deals = [Deal(game=CYBERPUNK, offer=offer("Steam", "17999", "KZT"))]

        assert "→" not in cards.deals_list(deals, page=0, currency="KZT")


class TestFreeGames:
    def test_empty(self) -> None:
        assert "Сейчас ничего не раздают" in cards.free_games([])

    def test_active_before_upcoming(self) -> None:
        now = datetime.now(UTC)
        games = [
            FreeGame(title="Сейчас", ends_at=now + timedelta(days=2)),
            FreeGame(title="Потом", starts_at=now + timedelta(days=5), upcoming=True),
        ]

        text = cards.free_games(games)

        assert text.index("Сейчас") < text.index("Потом")
        assert "Забирай бесплатно" in text
        assert "Скоро раздадут" in text

    def test_hours_left_for_soon_ending(self) -> None:
        # ровно 5 часов брать нельзя: остаток считается от now() внутри
        # рендера, и любая задержка превращает 5 ч в 4 ч 59 мин
        now = datetime.now(UTC)
        games = [FreeGame(title="Уходит", ends_at=now + timedelta(hours=5, minutes=1))]

        assert "осталось 5 ч" in cards.free_games(games)

    def test_original_price_struck_through(self) -> None:
        games = [
            FreeGame(
                title="Cardpocalypse",
                original_price=Decimal("7540"),
                currency="KZT",
                ends_at=datetime.now(UTC) + timedelta(days=3),
            )
        ]

        assert f"<s>7{NBSP}540{NBSP}₸</s>" in cards.free_games(games)


class TestPlural:
    """Русские окончания: «1 магазин», «2 магазина», «11 магазинов»."""

    def test_forms(self) -> None:
        cases = {1: "магазин", 2: "магазина", 4: "магазина", 5: "магазинов",
                 11: "магазинов", 21: "магазин", 22: "магазина", 25: "магазинов",
                 111: "магазинов", 112: "магазинов"}
        for count, expected in cases.items():
            assert cards._plural(count, "магазин", "магазина", "магазинов") == expected
