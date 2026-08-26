"""Клиент GOG: региональные цены, которых нет у ITAD."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from bot.services.cache import TTLCache
from bot.services.gog import CATALOG_URL, GogClient
from bot.services.http import ApiClient


@pytest.fixture
def gog(api: ApiClient, cache: TTLCache) -> GogClient:
    return GogClient(api, cache)


def product(
    title: str,
    final: str,
    base: str,
    *,
    discount: str = "",
    product_type: str = "pack",
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "id": "2093619782",
        "slug": title.lower().replace(" ", "_"),
        "title": title,
        "productType": product_type,
        "price": {
            "final": f"${final}",
            "base": f"${base}",
            "discount": discount,
            "finalMoney": {"amount": final, "currency": currency},
            "baseMoney": {"amount": base, "currency": currency},
        },
        "storeLink": "https://www.gog.com/en/game/cyberpunk_2077",
        "coverVertical": "https://images.gog-statics.com/cover.jpg",
    }


def catalog(*products: dict[str, Any]) -> dict[str, Any]:
    return {"products": list(products), "productCount": len(products)}


# Снято живым запросом: цена GOG для KZ вдвое ниже международной
KZ_CYBERPUNK = product("Cyberpunk 2077", "8.99", "29.99", discount="-70%")


@respx.mock
async def test_regional_price_is_parsed(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(200, json=catalog(KZ_CYBERPUNK))
    )

    found = await gog.offer_for("Cyberpunk 2077", country="KZ")

    assert found is not None
    _, offer = found
    assert offer.price == Decimal("8.99")
    assert offer.regular_price == Decimal("29.99")
    assert offer.cut == 70
    assert offer.currency == "USD"
    assert offer.shop.name == "GOG"
    assert offer.url == "https://www.gog.com/en/game/cyberpunk_2077"


@respx.mock
async def test_country_is_sent(gog: GogClient) -> None:
    route = respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(200, json=catalog())
    )

    await gog.offer_for("Cyberpunk 2077", country="KZ")

    params = route.calls.last.request.url.params
    assert params["countryCode"] == "KZ"
    assert params["query"] == "like:Cyberpunk 2077"


@respx.mock
async def test_only_exact_title_matches(gog: GogClient) -> None:
    """«Cyberpunk 2077» и «Ultimate Edition» — разные товары с разной ценой."""
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(
            200,
            json=catalog(
                product("Cyberpunk 2077: Ultimate Edition", "15.99", "39.99"),
                product("Cyberpunk 2077: Phantom Liberty", "8.99", "14.99",
                        product_type="dlc"),
                KZ_CYBERPUNK,
            ),
        )
    )

    found = await gog.offer_for("Cyberpunk 2077", country="KZ")

    assert found is not None
    assert found[0].title == "Cyberpunk 2077"
    assert found[1].price == Decimal("8.99")


@respx.mock
async def test_no_exact_match_returns_none(gog: GogClient) -> None:
    """Лучше оставить цену от ITAD, чем подставить чужой товар."""
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(
            200,
            json=catalog(product("Cyberpunk 2077: Ultimate Edition", "15.99", "39.99")),
        )
    )

    assert await gog.offer_for("Cyberpunk 2077", country="KZ") is None


@respx.mock
async def test_dlc_is_skipped_even_on_exact_title(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(
            200,
            json=catalog(product("Some Game", "5.00", "10.00", product_type="dlc")),
        )
    )

    assert await gog.offer_for("Some Game", country="KZ") is None


@respx.mock
async def test_title_match_ignores_case_and_spaces(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(
            200, json=catalog(product("CYBERPUNK 2077 ", "8.99", "29.99"))
        )
    )

    assert await gog.offer_for("cyberpunk 2077", country="KZ") is not None


@respx.mock
async def test_no_discount_hides_old_price(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(
            200, json=catalog(product("Hades", "24.99", "24.99"))
        )
    )

    found = await gog.offer_for("Hades", country="KZ")

    assert found is not None
    assert found[1].regular_price is None
    assert found[1].cut == 0


@respx.mock
async def test_empty_catalog(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=catalog()))

    assert await gog.offer_for("Такой игры нет", country="KZ") is None


@respx.mock
async def test_broken_payload_does_not_raise(gog: GogClient) -> None:
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(200, json={"products": "не список"})
    )

    assert await gog.offer_for("Cyberpunk 2077", country="KZ") is None


@respx.mock
async def test_missing_price_block(gog: GogClient) -> None:
    broken = {"title": "Cyberpunk 2077", "productType": "game"}
    respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(200, json=catalog(broken))
    )

    assert await gog.offer_for("Cyberpunk 2077", country="KZ") is None


@respx.mock
async def test_result_is_cached(gog: GogClient) -> None:
    route = respx.get(CATALOG_URL).mock(
        return_value=httpx.Response(200, json=catalog(KZ_CYBERPUNK))
    )

    await gog.offer_for("Cyberpunk 2077", country="KZ")
    await gog.offer_for("Cyberpunk 2077", country="KZ")

    assert route.call_count == 1
