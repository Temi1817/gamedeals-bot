"""HTTP-слой: повторы, backoff, Retry-After."""

from __future__ import annotations

import httpx
import pytest
import respx

from bot.services.http import ApiClient, ApiError

URL = "https://api.example/data"


@respx.mock
async def test_returns_json(api: ApiClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    assert await api.get_json(URL) == {"ok": True}


@respx.mock
async def test_empty_body_is_none(api: ApiClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(204))

    assert await api.get_json(URL) is None


@respx.mock
async def test_retries_on_500_then_succeeds(
    api: ApiClient, no_sleep: list[float]
) -> None:
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"ok": True}),
    ]

    assert await api.get_json(URL) == {"ok": True}
    assert route.call_count == 2
    assert len(no_sleep) == 1


@respx.mock
async def test_honours_retry_after(api: ApiClient, no_sleep: list[float]) -> None:
    """Если сервер назвал паузу — ждём именно её, а не свой backoff."""
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"ok": True}),
    ]

    await api.get_json(URL)

    assert no_sleep == [7.0]


@respx.mock
async def test_ignores_unparsable_retry_after(
    api: ApiClient, no_sleep: list[float]
) -> None:
    """Формат с датой не разбираем — откатываемся на собственный backoff."""
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(200, json={"ok": True}),
    ]

    await api.get_json(URL)

    assert len(no_sleep) == 1
    assert no_sleep[0] != 0


@respx.mock
async def test_backoff_grows(api: ApiClient, no_sleep: list[float]) -> None:
    respx.get(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ApiError):
        await api.get_json(URL)

    assert len(no_sleep) == 2
    assert no_sleep[1] > no_sleep[0]


@respx.mock
async def test_gives_up_after_max_retries(api: ApiClient, no_sleep: list[float]) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ApiError) as exc_info:
        await api.get_json(URL)

    assert route.call_count == 3  # первая попытка + два повтора
    assert exc_info.value.status == 503
    assert exc_info.value.source == "test"


@respx.mock
async def test_client_error_is_not_retried(api: ApiClient) -> None:
    """404 повторять бессмысленно — ответ не изменится."""
    route = respx.get(URL).mock(return_value=httpx.Response(404))

    with pytest.raises(ApiError) as exc_info:
        await api.get_json(URL)

    assert route.call_count == 1
    assert exc_info.value.status == 404


@respx.mock
async def test_retries_on_timeout(api: ApiClient, no_sleep: list[float]) -> None:
    route = respx.get(URL)
    route.side_effect = [
        httpx.TimeoutException("слишком долго"),
        httpx.Response(200, json={"ok": True}),
    ]

    assert await api.get_json(URL) == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_network_error_exhausts_retries(
    api: ApiClient, no_sleep: list[float]
) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("сеть недоступна"))

    with pytest.raises(ApiError, match="сетевая ошибка"):
        await api.get_json(URL)


@respx.mock
async def test_non_json_body_raises(api: ApiClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>ошибка</html>"))

    with pytest.raises(ApiError, match="не JSON"):
        await api.get_json(URL)


@respx.mock
async def test_post_sends_body(api: ApiClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=[]))

    await api.post_json(URL, params={"country": "KZ"}, json=["id-1"])

    request = route.calls.last.request
    assert request.url.params["country"] == "KZ"
    assert request.content == b'["id-1"]'
