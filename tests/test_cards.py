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

CYBERPUNK = Game(title="Cyberpunk 2077", itad_id="itad-1", steam_appid=1091500)


def offer(
    name: str,
    amount: str,
    currency: str = "USD",
    *,
    converted: str | None = None,
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
    )


class TestOfferLine:
    def test_native_price_has_no_approximation(self) -> None:
        line = cards.offer_line(offer("Steam", "17999", "KZT", source=STEAM))

        assert f"17{NBSP}999{NBSP}₸" in line
        assert "≈" not in line

    def test_converted_price_is_shown(self) -> None:
        line = cards.offer_line(offer("GOG", "17.99", converted="8231"))

        assert "$17.99" in line
        assert f"≈8{NBSP}231{NBSP}₸" in line

    def test_discount_and_old_price(self) -> None:
        line = cards.offer_line(offer("GOG", "17.99", cut=70, regular="59.99"))

        assert "−70%" in line
        assert "<s>$59.99</s>" in line

    def test_no_old_price_without_discount(self) -> None:
        assert "<s>" not in cards.offer_line(offer("Steam", "59.99"))

    def test_shop_name_is_a_link(self) -> None:
        line = cards.offer_line(offer("GOG", "17.99"))

        assert '<a href="https://shop.example/gog">GOG</a>' in line

    def test_medals_for_top_three(self) -> None:
        assert cards.offer_line(offer("GOG", "1"), 0).startswith("🥇")
        assert cards.offer_line(offer("GOG", "1"), 1).startswith("🥈")
        assert cards.offer_line(offer("GOG", "1"), 2).startswith("🥉")
        assert cards.offer_line(offer("GOG", "1"), 3).startswith("4.")

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
        assert "Где купить" in text
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

        assert "пересчёт по курсу" in cards.game_card(converted)
        assert "пересчёт по курсу" not in cards.game_card(native)

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
        assert text.index("Где купить") < text.index("Ключи у реселлеров")

    def test_empty_offers(self) -> None:
        text = cards.game_card(GameDetails(game=CYBERPUNK))

        assert "Цен по этой игре сейчас нет" in text
        assert "Где купить" not in text

    def test_title_is_escaped(self) -> None:
        details = GameDetails(game=Game(title="Tom & Jerry <b>"), offers=[])

        assert "Tom &amp; Jerry &lt;b&gt;" in cards.game_card(details)


class TestSearchResults:
    def test_found(self) -> None:
        assert "Выбери игру" in cards.search_results("Cyberpunk", 3)

    def test_not_found_suggests_english(self) -> None:
        text = cards.search_results("ведьмак", 0)

        assert "ничего не нашлось" in text
        assert "по-английски" in text

    def test_query_is_escaped(self) -> None:
        assert "&lt;b&gt;" in cards.search_results("<b>", 0)


class TestPriceHistory:
    def test_no_points(self) -> None:
        text = cards.price_history("Cyberpunk 2077", [], "KZT")

        assert "пока не накопил замеров" in text

    def test_draws_bars(self) -> None:
        base = datetime(2026, 8, 1, tzinfo=UTC)
        points = [
            (base, Decimal("20000"), "KZT"),
            (base + timedelta(days=1), Decimal("10000"), "KZT"),
        ]

        text = cards.price_history("Cyberpunk 2077", points, "KZT")

        assert "█" in text
        assert "01.08.2026" in text
        assert f"Минимум по моим замерам: <b>10{NBSP}000{NBSP}₸</b>" in text

    def test_shows_last_points_only(self) -> None:
        base = datetime(2026, 8, 1, tzinfo=UTC)
        points = [
            (base + timedelta(days=i), Decimal(10000 + i), "KZT") for i in range(30)
        ]

        text = cards.price_history("X", points, "KZT")

        assert text.count("█") > 0
        assert "01.08.2026" not in text  # старые точки обрезаны

    def test_flat_history_does_not_divide_by_zero(self) -> None:
        base = datetime(2026, 8, 1, tzinfo=UTC)
        points = [(base, Decimal("5000"), "KZT"), (base, Decimal("5000"), "KZT")]

        text = cards.price_history("X", points, "KZT")

        assert "█" in text


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


class TestFreeGames:
    def test_empty(self) -> None:
        assert "бесплатных раздач нет" in cards.free_games([])

    def test_active_before_upcoming(self) -> None:
        now = datetime.now(UTC)
        games = [
            FreeGame(title="Сейчас", ends_at=now + timedelta(days=2)),
            FreeGame(title="Потом", starts_at=now + timedelta(days=5), upcoming=True),
        ]

        text = cards.free_games(games)

        assert text.index("Сейчас") < text.index("Потом")
        assert "Забрать бесплатно прямо сейчас" in text
        assert "Скоро раздадут" in text

    def test_hours_left_for_soon_ending(self) -> None:
        now = datetime.now(UTC)
        games = [FreeGame(title="Уходит", ends_at=now + timedelta(hours=5))]

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
