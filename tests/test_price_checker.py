"""Логика уведомлений: когда писать, а когда молчать."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.db.models import Watch
from bot.jobs.price_checker import next_watermark, notification_text, should_notify
from bot.services.models import STEAM, Game, Offer, Shop
from bot.utils.formatting import NBSP


def watch(
    target: str | None = None, last: str | None = None, currency: str = "KZT"
) -> Watch:
    return Watch(
        user_id=1,
        game_id=1,
        target_price=Decimal(target) if target else None,
        last_notified_price=Decimal(last) if last else None,
        currency=currency,
        notify_any_drop=target is None,
    )


class TestTargetPrice:
    """Отслеживание с целевой ценой."""

    def test_silent_while_above_target(self) -> None:
        assert not should_notify(watch(target="3000"), Decimal("5000"))

    def test_notifies_when_target_reached(self) -> None:
        assert should_notify(watch(target="3000"), Decimal("2500"))

    def test_notifies_exactly_at_target(self) -> None:
        assert should_notify(watch(target="3000"), Decimal("3000"))

    def test_does_not_repeat_same_price(self) -> None:
        """Главная защита от спама: цена та же — молчим."""
        assert not should_notify(watch(target="3000", last="2500"), Decimal("2500"))

    def test_notifies_when_drops_further(self) -> None:
        assert should_notify(watch(target="3000", last="2500"), Decimal("2000"))

    def test_silent_when_price_goes_back_up(self) -> None:
        assert not should_notify(watch(target="3000", last="2000"), Decimal("2800"))


class TestAnyDrop:
    """Отслеживание без цели — «сообщи, когда подешевеет»."""

    def test_first_check_only_records_baseline(self) -> None:
        """Первый замер не повод писать: сравнивать ещё не с чем."""
        assert not should_notify(watch(), Decimal("17999"))

    def test_notifies_on_drop(self) -> None:
        assert should_notify(watch(last="17999"), Decimal("9999"))

    def test_silent_without_change(self) -> None:
        assert not should_notify(watch(last="17999"), Decimal("17999"))

    def test_silent_when_price_rises(self) -> None:
        assert not should_notify(watch(last="9999"), Decimal("17999"))

    def test_notifies_again_after_price_recovered(self) -> None:
        """Цена упала, вернулась, снова упала — это новое событие.

        Ориентир идёт за ценой вверх, иначе после одной глубокой скидки
        бот замолчал бы навсегда.
        """
        w = watch(last="9999")
        # цена вернулась — ориентир поднимается
        assert next_watermark(w, Decimal("17999")) == Decimal("17999")
        w.last_notified_price = Decimal("17999")
        # новое снижение снова заметно
        assert should_notify(w, Decimal("14999"))


class TestWatermark:
    def test_any_drop_follows_price_both_ways(self) -> None:
        assert next_watermark(watch(last="9999"), Decimal("17999")) == Decimal("17999")
        assert next_watermark(watch(last="17999"), Decimal("9999")) == Decimal("9999")

    def test_target_keeps_the_lowest(self) -> None:
        assert next_watermark(
            watch(target="3000", last="2000"), Decimal("2800")
        ) == Decimal("2000")

    def test_target_not_set_until_reached(self) -> None:
        """Иначе первое достижение цели окажется «не ниже» и потеряется."""
        assert next_watermark(watch(target="3000"), Decimal("5000")) is None

    def test_target_set_once_reached(self) -> None:
        assert next_watermark(watch(target="3000"), Decimal("2500")) == Decimal("2500")


def test_reaching_target_first_time_is_not_swallowed() -> None:
    """Сквозной сценарий: цена долго выше цели, потом падает."""
    w = watch(target="3000")

    for price in ("5000", "4500", "3500"):
        assert not should_notify(w, Decimal(price))
        w.last_notified_price = next_watermark(w, Decimal(price))

    assert w.last_notified_price is None
    assert should_notify(w, Decimal("2900"))


class TestNotificationText:
    def test_includes_price_shop_and_goal(self) -> None:
        offer = Offer(
            shop=Shop(id="steam", name="Steam", source=STEAM),
            price=Decimal("9999"),
            currency="KZT",
            regular_price=Decimal("17999"),
            cut=44,
            url="https://store.steampowered.com/app/1091500/",
        )

        text = notification_text(
            Game(title="Cyberpunk 2077"), offer, watch(target="10000")
        )

        assert "Cyberpunk 2077" in text
        assert f"9{NBSP}999{NBSP}₸" in text
        assert "Steam" in text
        assert "−44%" in text or "44%" in text
        assert f"10{NBSP}000{NBSP}₸" in text

    def test_without_target(self) -> None:
        offer = Offer(
            shop=Shop(id="gog", name="GOG", source=STEAM),
            price=Decimal("5000"),
            currency="KZT",
        )

        text = notification_text(Game(title="Hades"), offer, watch())

        assert "Hades" in text
        assert "цель" not in text.lower()

    def test_title_is_escaped(self) -> None:
        offer = Offer(
            shop=Shop(id="s", name="Steam", source=STEAM),
            price=Decimal("100"),
            currency="KZT",
        )

        text = notification_text(Game(title="Tom & Jerry"), offer, watch())

        assert "Tom &amp; Jerry" in text


@pytest.mark.parametrize(
    ("target", "last", "price", "expected"),
    [
        # цель есть, цена падает ступеньками — пишем на каждой новой глубине
        ("3000", None, "2900", True),
        ("3000", "2900", "2800", True),
        ("3000", "2800", "2800", False),
        # цель есть, цена скачет вокруг — лишнего не пишем
        ("3000", "2500", "2900", False),
    ],
)
def test_target_scenarios(
    target: str, last: str | None, price: str, expected: bool
) -> None:
    assert should_notify(watch(target=target, last=last), Decimal(price)) is expected
