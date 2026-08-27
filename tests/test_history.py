"""История скидок: разбор ответа ITAD, слияние с замерами, рендер."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from bot.services.aggregator import _dedupe_by_day
from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.itad import BASE_URL, ItadClient
from bot.services.models import PricePoint
from bot.utils import cards
from bot.utils.formatting import NBSP

HISTORY_URL = f"{BASE_URL}/games/history/v2"
GAME_ID = "018d937f-2997-7131-b8b9-7c8af4825fa8"


@pytest.fixture
def itad(api: ApiClient, cache: TTLCache) -> ItadClient:
    return ItadClient(api, cache, "test-key")


def entry(
    timestamp: str, amount: float, cut: int, shop: str = "GOG"
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "shop": {"id": 35, "name": shop},
        "deal": {
            "price": {"amount": amount, "amountInt": int(amount * 100),
                      "currency": "USD"},
            "regular": {"amount": 59.99, "amountInt": 5999, "currency": "USD"},
            "cut": cut,
        },
    }


def point(day: int, price: str, cut: int = 50, exact: bool = False) -> PricePoint:
    return PricePoint(
        at=datetime(2026, 6, day, tzinfo=UTC),
        price=Decimal(price),
        currency="KZT",
        cut=cut,
        shop="Steam",
        exact=exact,
    )


class TestItadHistory:
    @respx.mock
    async def test_parses_points(self, itad: ItadClient) -> None:
        respx.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    entry("2026-08-25T22:17:54+02:00", 17.99, 70),
                    entry("2026-06-17T14:44:42+02:00", 20.99, 65, shop="Steam"),
                ],
            )
        )

        points = await itad.price_history(GAME_ID, country="KZ")

        assert len(points) == 2
        # отсортировано по возрастанию даты
        assert points[0].at < points[1].at
        assert points[1].price == Decimal("17.99")
        assert points[1].cut == 70
        assert points[1].shop == "GOG"
        assert points[1].currency == "USD"
        assert points[1].exact is False

    @respx.mock
    async def test_since_is_always_sent(self, itad: ItadClient) -> None:
        """Без since ITAD отдаёт только последние три месяца."""
        route = respx.get(HISTORY_URL).mock(
            return_value=httpx.Response(200, json=[])
        )

        await itad.price_history(GAME_ID, country="KZ", days=365)

        params = route.calls.last.request.url.params
        assert "since" in params
        assert params["country"] == "KZ"

    @respx.mock
    async def test_delisted_entries_are_skipped(self, itad: ItadClient) -> None:
        """deal == null означает, что игру сняли с продажи."""
        respx.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"timestamp": "2026-08-25T22:17:54+02:00",
                     "shop": {"id": 35, "name": "GOG"}, "deal": None},
                    entry("2026-08-26T10:00:00+02:00", 17.99, 70),
                ],
            )
        )

        points = await itad.price_history(GAME_ID)

        assert len(points) == 1

    @respx.mock
    async def test_full_price_entries_are_skipped(self, itad: ItadClient) -> None:
        """Возврат к полной цене превратил бы график в пилу."""
        respx.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    entry("2026-08-12T09:13:27+02:00", 59.99, 0),
                    entry("2026-08-25T22:17:54+02:00", 17.99, 70),
                ],
            )
        )

        points = await itad.price_history(GAME_ID)

        assert [p.cut for p in points] == [70]

    @respx.mock
    async def test_empty_history(self, itad: ItadClient) -> None:
        respx.get(HISTORY_URL).mock(return_value=httpx.Response(200, json=[]))

        assert await itad.price_history(GAME_ID) == []


class TestDedupeByDay:
    """В один день скидка приходит сразу в несколько магазинов."""

    def test_keeps_cheapest_of_the_day(self) -> None:
        same_day = [
            PricePoint(at=datetime(2026, 6, 17, 9, tzinfo=UTC),
                       price=Decimal("100"), currency="KZT", shop="A"),
            PricePoint(at=datetime(2026, 6, 17, 18, tzinfo=UTC),
                       price=Decimal("80"), currency="KZT", shop="B"),
        ]

        result = _dedupe_by_day(same_day)

        assert len(result) == 1
        assert result[0].shop == "B"

    def test_exact_wins_at_equal_price(self) -> None:
        """При одинаковой цене точка от самой витрины важнее пересчёта."""
        same_day = [
            PricePoint(at=datetime(2026, 6, 17, 9, tzinfo=UTC),
                       price=Decimal("100"), currency="KZT", shop="ITAD"),
            PricePoint(at=datetime(2026, 6, 17, 18, tzinfo=UTC),
                       price=Decimal("100"), currency="KZT", shop="Steam",
                       exact=True),
        ]

        assert _dedupe_by_day(same_day)[0].exact is True

    def test_sorted_by_date(self) -> None:
        points = [point(20, "100"), point(1, "200"), point(10, "150")]

        assert [p.at.day for p in _dedupe_by_day(points)] == [1, 10, 20]

    def test_empty(self) -> None:
        assert _dedupe_by_day([]) == []


class TestHistoryCard:
    def test_no_points(self) -> None:
        text = cards.price_history("Hades", [], "KZT")

        assert "скидок пока не было" in text
        assert "🔔" in text

    def test_shows_price_percent_and_shop(self) -> None:
        text = cards.price_history("Hades", [point(1, "2869", cut=75)], "KZT")

        assert f"2{NBSP}869{NBSP}₸" in text
        assert "−75%" in text
        assert "Steam" in text
        assert "01.06.26" in text

    def test_marks_lowest_points(self) -> None:
        points = [point(1, "5000"), point(2, "3000"), point(3, "5000")]

        text = cards.price_history("X", points, "KZT")

        assert text.count("🔻") == 2  # точка на минимуме плюс строка итога

    def test_approximate_prices_are_marked(self) -> None:
        text = cards.price_history("X", [point(1, "5000", exact=False)], "KZT")

        assert "≈" in text
        assert "международный прайс" in text

    def test_exact_prices_have_no_marker(self) -> None:
        text = cards.price_history("X", [point(1, "5000", exact=True)], "KZT")

        assert "≈" not in text
        assert "международный прайс" not in text

    def test_bars_are_drawn(self) -> None:
        points = [point(1, "5000"), point(2, "1000")]

        text = cards.price_history("X", points, "KZT")

        assert "█" in text
        assert "░" in text

    def test_flat_history_does_not_divide_by_zero(self) -> None:
        points = [point(1, "5000"), point(2, "5000")]

        assert "█" in cards.price_history("X", points, "KZT")

    def test_frequency_hint(self) -> None:
        """Ради этой строки всё и затевалось: когда ждать следующую скидку."""
        now = datetime.now(UTC)
        points = [
            PricePoint(at=now - timedelta(days=60), price=Decimal("100"),
                       currency="KZT", cut=50),
            PricePoint(at=now - timedelta(days=30), price=Decimal("100"),
                       currency="KZT", cut=50),
            PricePoint(at=now - timedelta(days=1), price=Decimal("100"),
                       currency="KZT", cut=50),
        ]

        text = cards.price_history("X", points, "KZT")

        assert "Скидки бывают примерно раз в" in text
        assert "последняя 1 день назад" in text

    def test_no_hint_for_short_history(self) -> None:
        text = cards.price_history("X", [point(1, "100")], "KZT")

        assert "Скидки бывают" not in text
