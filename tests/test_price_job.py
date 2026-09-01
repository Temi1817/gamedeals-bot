"""Выбор предложения в джобе уведомлений.

Экран настроек обещает, что невыбранные магазины пропадут «из карточек,
скидок и уведомлений». Первая версия джобы фильтр игнорировала — вся
логика отбора собрана здесь и проверяется отдельно.
"""

from __future__ import annotations

from decimal import Decimal

from bot.services.models import CHEAPSHARK, ITAD, STEAM, Offer, Shop
from bot.services.shops import filter_offers, parse_selection


def offer(name: str, price: str, source: str = ITAD, reseller: bool = False) -> Offer:
    return Offer(
        shop=Shop(id=name.lower(), name=name, source=source),
        price=Decimal(price),
        currency="KZT",
        is_reseller=reseller,
    )


def pick(offers: list[Offer], preferred: str) -> Offer | None:
    """Тот же отбор, что делает джоба: фильтр магазинов, затем минимум."""
    chosen = filter_offers(offers, parse_selection(preferred))
    shops = [o for o in chosen if not o.is_reseller]
    return min(shops, key=lambda o: o.sort_key) if shops else None


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
