"""Выбор предложения в джобе уведомлений.

Экран настроек обещает, что невыбранные магазины пропадут «из карточек,
скидок и уведомлений». Первая версия джобы фильтр игнорировала — вся
логика отбора собрана здесь и проверяется отдельно.
"""

from __future__ import annotations

from decimal import Decimal

from bot.db.models import User, Watch
from bot.jobs.price_checker import pick_offer
from bot.services.models import CHEAPSHARK, ITAD, STEAM, Game, GameDetails, Offer, Shop


def offer(name: str, price: str, source: str = ITAD, reseller: bool = False) -> Offer:
    return Offer(
        shop=Shop(id=name.lower(), name=name, source=source),
        price=Decimal(price),
        currency="KZT",
        is_reseller=reseller,
    )


def pick(offers: list[Offer], preferred: str = "", watch_shop: str = "") -> Offer | None:
    """Отбор, который делает джоба: магазин отслеживания, затем настройки."""
    watch = Watch(
        user_id=1,
        game_id=1,
        target_price=None,
        currency="KZT",
        notify_any_drop=True,
        shop_key=watch_shop,
    )
    watch.user = User(tg_id=1, country="KZ", preferred_shops=preferred)
    details = GameDetails(game=Game(title="X"), offers=offers)
    return pick_offer(details, watch)


OFFERS = [
    offer("GOG", "4000"),
    offer("Steam", "6000", source=STEAM),
    offer("Fanatical", "3000"),
]


class TestShopFilterInNotifications:
    def test_without_filter_takes_cheapest(self) -> None:
        assert pick(OFFERS, "").shop.name == "Fanatical"

    def test_filter_limits_to_chosen_shop(self) -> None:
        """Ради этого всё и делалось: выбрал Steam — жди цену Steam."""
        picked = pick(OFFERS, "steam")

        assert picked is not None
        assert picked.shop.name == "Steam"
        assert picked.price == Decimal("6000")

    def test_several_chosen_shops(self) -> None:
        picked = pick(OFFERS, "steam,gog")

        assert picked is not None
        assert picked.shop.name == "GOG"  # дешевле Steam

    def test_reseller_never_triggers_alert(self) -> None:
        """Ключ реселлера дешевле всех, но это не покупка в магазине."""
        offers = [*OFFERS, offer("Ключи", "500", CHEAPSHARK, reseller=True)]

        picked = pick(offers, "")

        assert picked is not None
        assert picked.is_reseller is False

    def test_only_resellers_means_no_alert(self) -> None:
        offers = [offer("Ключи", "500", CHEAPSHARK, reseller=True)]

        assert pick(offers, "") is None

    def test_unknown_shop_falls_back_to_all(self) -> None:
        """Молчать из-за того, что любимого магазина нет в выдаче, хуже,
        чем предупредить о цене в другом."""
        picked = pick(OFFERS, "microsoft")

        assert picked is not None
        assert picked.shop.name == "Fanatical"

    def test_empty_offers(self) -> None:
        assert pick([], "steam") is None


class TestPerWatchShop:
    """Магазин, выбранный при подписке, важнее общего фильтра настроек."""

    def test_watch_shop_wins_over_cheapest(self) -> None:
        picked = pick(OFFERS, watch_shop="steam")

        assert picked is not None
        assert picked.shop.name == "Steam"
        assert picked.price == Decimal("6000")

    def test_watch_shop_wins_over_settings(self) -> None:
        """В настройках GOG, но на эту игру подписался в Steam."""
        picked = pick(OFFERS, preferred="gog", watch_shop="steam")

        assert picked is not None
        assert picked.shop.name == "Steam"

    def test_falls_back_when_shop_absent(self) -> None:
        """Магазина в выдаче нет — молчать нельзя, иначе бот замолкает
        навсегда и пользователь не понимает почему."""
        picked = pick(OFFERS, watch_shop="microsoft")

        assert picked is not None
        assert picked.shop.name == "Fanatical"

    def test_empty_watch_shop_uses_settings(self) -> None:
        picked = pick(OFFERS, preferred="steam", watch_shop="")

        assert picked is not None
        assert picked.shop.name == "Steam"

    def test_reseller_not_picked_even_if_chosen(self) -> None:
        offers = [*OFFERS, offer("Ключи", "100", CHEAPSHARK, reseller=True)]

        picked = pick(offers, watch_shop="")

        assert picked is not None
        assert picked.is_reseller is False
