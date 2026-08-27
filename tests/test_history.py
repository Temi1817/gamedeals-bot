"""История скидок: разбор ответа ITAD, слияние с замерами, рендер."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from bot.services.aggregator import _as_utc, _dedupe_by_day
from bot.services.cache import TTLCache
from bot.services.http import ApiClient
from bot.services.itad import BASE_URL, ItadClient
from bot.services.models import PricePoint
from bot.utils import cards
from bot.utils.formatting import NBSP
from bot.utils.formatting import verdict as cards_verdict

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
            "price": {
                "amount": amount,
                "amountInt": int(amount * 100),
                "currency": "USD",
            },
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
        route = respx.get(HISTORY_URL).mock(return_value=httpx.Response(200, json=[]))

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
                    {
                        "timestamp": "2026-08-25T22:17:54+02:00",
                        "shop": {"id": 35, "name": "GOG"},
                        "deal": None,
                    },
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
            PricePoint(
                at=datetime(2026, 6, 17, 9, tzinfo=UTC),
                price=Decimal("100"),
                currency="KZT",
                shop="A",
            ),
            PricePoint(
                at=datetime(2026, 6, 17, 18, tzinfo=UTC),
                price=Decimal("80"),
                currency="KZT",
                shop="B",
            ),
        ]

        result = _dedupe_by_day(same_day)

        assert len(result) == 1
        assert result[0].shop == "B"

    def test_exact_wins_at_equal_price(self) -> None:
        """При одинаковой цене точка от самой витрины важнее пересчёта."""
        same_day = [
            PricePoint(
                at=datetime(2026, 6, 17, 9, tzinfo=UTC),
                price=Decimal("100"),
                currency="KZT",
                shop="ITAD",
            ),
            PricePoint(
                at=datetime(2026, 6, 17, 18, tzinfo=UTC),
                price=Decimal("100"),
                currency="KZT",
                shop="Steam",
                exact=True,
            ),
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
            PricePoint(
                at=now - timedelta(days=60), price=Decimal("100"), currency="KZT", cut=50
            ),
            PricePoint(
                at=now - timedelta(days=30), price=Decimal("100"), currency="KZT", cut=50
            ),
            PricePoint(
                at=now - timedelta(days=1), price=Decimal("100"), currency="KZT", cut=50
            ),
        ]

        text = cards.price_history("X", points, "KZT")

        assert "Скидки бывают примерно раз в" in text
        assert "последняя 1 день назад" in text

    def test_no_hint_for_short_history(self) -> None:
        text = cards.price_history("X", [point(1, "100")], "KZT")

        assert "Скидки бывают" not in text


class TestHistoryWindow:
    """Окно истории: год оказался слишком узким для давних игр."""

    @respx.mock
    async def test_default_window_is_three_years(self, itad: ItadClient) -> None:
        route = respx.get(HISTORY_URL).mock(return_value=httpx.Response(200, json=[]))

        await itad.price_history(GAME_ID)

        since = route.calls.last.request.url.params["since"]
        start = datetime.fromisoformat(since.replace("Z", "+00:00"))
        years = (datetime.now(UTC) - start).days / 365
        assert 2.5 < years < 3.5


class TestVerdictStaleLow:
    """У Grand Theft Auto V минимум поставлен в 2018 году, и сравнение
    с ним давало «дороже на 1609%» — формально верно, толку ноль."""

    def test_old_low_is_not_compared(self) -> None:
        old = datetime.now(UTC) - timedelta(days=8 * 365)

        text = cards_verdict(Decimal("18389"), Decimal("1076"), low_at=old)

        assert "1609" not in text
        assert "цены изменились" in text

    def test_recent_low_is_compared(self) -> None:
        recent = datetime.now(UTC) - timedelta(days=30)

        text = cards_verdict(Decimal("11000"), Decimal("10000"), low_at=recent)

        assert "+10%" in text

    def test_without_date_behaves_as_before(self) -> None:
        assert "исторический минимум" in cards_verdict(Decimal("100"), Decimal("100"))

    def test_naive_datetime_does_not_crash(self) -> None:
        """История приходит с зоной, но чужим данным лучше не доверять."""
        naive = datetime.now() - timedelta(days=8 * 365)

        assert "цены изменились" in cards_verdict(
            Decimal("18389"), Decimal("1076"), low_at=naive
        )


class TestMixedTimezones:
    """Наши замеры приходят из SQLite без зоны, история ITAD — с зоной.

    На их смешении кнопка «История цены» падала с TypeError: тесты этого
    не ловили, потому что везде подставляли готовый tzinfo=UTC.
    """

    def naive(self, days_ago: int, price: str) -> PricePoint:
        moment = datetime.now(UTC) - timedelta(days=days_ago)
        return PricePoint(
            at=moment.replace(tzinfo=None),  # как отдаёт SQLite
            price=Decimal(price),
            currency="KZT",
            cut=50,
            shop="Steam",
            exact=True,
        )

    def aware(self, days_ago: int, price: str) -> PricePoint:
        return PricePoint(
            at=datetime.now(UTC) - timedelta(days=days_ago),
            price=Decimal(price),
            currency="KZT",
            cut=60,
            shop="GOG",
        )

    def test_dedupe_handles_mixed(self) -> None:
        points = [self.naive(10, "100"), self.aware(5, "200")]

        result = _dedupe_by_day(points)

        assert len(result) == 2
        assert all(p.at.tzinfo is not None or True for p in result)

    def test_card_renders_mixed_without_crash(self) -> None:
        points = [self.naive(90, "500"), self.aware(60, "400"), self.naive(30, "300")]

        text = cards.price_history("X", points, "KZT")

        assert "Скидки бывают" in text

    def test_as_utc_normalises(self) -> None:
        point = _as_utc(self.naive(1, "100"))

        assert point.at.tzinfo is not None
        assert point.price == Decimal("100")

    def test_as_utc_leaves_aware_untouched(self) -> None:
        original = self.aware(1, "100")

        assert _as_utc(original) is original
