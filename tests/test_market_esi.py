import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from wabot import market_esi
from wabot.market_esi import ESIClient, ESIError


def order(order_id=1):
    return {
        "order_id": order_id,
        "type_id": 55831,
        "location_id": 60003760,
        "system_id": 30000142,
        "is_buy_order": True,
        "price": 499999.99,
        "volume_remain": 7,
        "volume_total": 200,
    }


class Response:
    def __init__(self, data=None, *, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.raw = json.dumps(data) if raw is None else raw

    async def text(self):
        return self.raw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def client_with(*responses):
    session = Mock(spec=aiohttp.ClientSession)
    session.get.side_effect = responses
    session.close = AsyncMock()
    client = ESIClient()
    client._session = session
    return client, session


@pytest.fixture
def retry_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0, sleeps=[])
    monkeypatch.setattr(market_esi, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(market_esi.random, "uniform", lambda *_: 0)

    async def sleep(delay):
        clock.sleeps.append(delay)
        clock.now += delay

    monkeypatch.setattr(market_esi.asyncio, "sleep", sleep)
    return clock


async def test_all_pages_are_combined_without_losing_decimal_precision():
    headers = {"X-Pages": "2", "Last-Modified": "Wed, 16 Sep 2026 10:00:00 GMT"}
    client, session = client_with(
        Response([order()], headers=headers), Response([order(2)], headers=headers)
    )
    result = await client.fetch_orders(10000002, 55831)
    assert len(result.orders) == 2
    assert result.orders[0]["price"] == Decimal("499999.99")
    assert result.orders[0]["volume_remain"] == 7
    assert result.fetched_at.tzinfo == UTC
    assert not result.cached
    assert [call.kwargs["params"]["page"] for call in session.get.call_args_list] == ["1", "2"]
    assert all(call.kwargs["params"]["order_type"] == "all" for call in session.get.call_args_list)


@pytest.mark.parametrize(
    "second_headers,second_order,error",
    [
        ({"X-Pages": "3", "Last-Modified": "old"}, 2, "page count"),
        ({"X-Pages": "2", "Last-Modified": "new"}, 2, "snapshot"),
        ({"X-Pages": "2", "Last-Modified": "old"}, 1, "duplicate"),
    ],
)
async def test_inconsistent_pagination_never_returns_partial_snapshot(
    second_headers, second_order, error
):
    client, _ = client_with(
        Response([order()], headers={"X-Pages": "2", "Last-Modified": "old"}),
        Response([order(second_order)], headers=second_headers),
    )
    with pytest.raises(ESIError, match=error):
        await client.fetch_orders(10000002, 55831)


async def test_failed_second_page_never_returns_first_page(retry_clock):
    client, session = client_with(
        Response([order()], headers={"X-Pages": "2", "Last-Modified": "old"}),
        *[Response(status=503) for _ in range(3)],
    )
    with pytest.raises(ESIError, match="503"):
        await client.fetch_orders(10000002, 55831)
    assert session.get.call_count == 4
    assert retry_clock.sleeps == [1, 2]


async def test_expiry_reuses_data_and_etag_revalidates_after_expiry(retry_clock):
    client, session = client_with(
        Response([order()], headers={"Cache-Control": "max-age=30", "ETag": '"v1"'}),
        Response(status=304, headers={"Cache-Control": "max-age=30"}),
    )
    first = await client.fetch_orders(10000002, 55831)
    cached = await client.fetch_orders(10000002, 55831)
    assert cached.cached and cached.fetched_at == first.fetched_at
    assert session.get.call_count == 1
    retry_clock.now += 31
    revalidated = await client.fetch_orders(10000002, 55831)
    assert revalidated.cached and revalidated.orders == first.orders
    assert revalidated.fetched_at >= first.fetched_at
    assert session.get.call_args.kwargs["headers"] == {"If-None-Match": '"v1"'}


async def test_expires_header_prevents_early_requests():
    expires = format_datetime(datetime.now(UTC) + timedelta(minutes=5), usegmt=True)
    client, session = client_with(Response([], headers={"Expires": expires}))
    await client.fetch_orders(10000002, 55831)
    await client.fetch_orders(10000002, 55831)
    assert session.get.call_count == 1


async def test_expired_cache_does_not_turn_failure_into_success(retry_clock):
    client, _ = client_with(
        Response([order()], headers={"Cache-Control": "max-age=1"}),
        *[Response(status=502) for _ in range(3)],
    )
    await client.fetch_orders(10000002, 55831)
    retry_clock.now += 2
    with pytest.raises(ESIError):
        await client.fetch_orders(10000002, 55831)


@pytest.mark.parametrize("status", [420, 429, 503])
async def test_retry_after_is_respected_before_retry(status, retry_clock):
    client, session = client_with(
        Response(status=status, headers={"Retry-After": "7"}),
        Response([order()]),
    )
    result = await client.fetch_orders(10000002, 55831)
    assert len(result.orders) == 1
    assert session.get.call_count == 2
    assert retry_clock.sleeps == [7]


async def test_long_retry_after_stops_cycle_and_blocks_other_items(retry_clock):
    client, session = client_with(Response(status=429, headers={"Retry-After": "120"}))
    with pytest.raises(ESIError, match="429"):
        await client.fetch_orders(10000002, 55831)
    with pytest.raises(ESIError, match="cooldown"):
        await client.fetch_orders(10000002, 52244)
    assert session.get.call_count == 1
    assert retry_clock.sleeps == []


async def test_low_error_budget_stops_next_request_until_reset(retry_clock):
    client, session = client_with(
        Response([], headers={"X-ESI-Error-Limit-Remain": "8", "X-ESI-Error-Limit-Reset": "60"}),
        Response([]),
    )
    await client.fetch_orders(10000002, 55831)
    with pytest.raises(ESIError, match="cooldown"):
        await client.fetch_orders(10000002, 52244)
    assert session.get.call_count == 1
    retry_clock.now += 61
    await client.fetch_orders(10000002, 52244)
    assert session.get.call_count == 2


async def test_network_failures_retry_with_bounded_backoff(retry_clock):
    client, session = client_with(*[aiohttp.ClientConnectionError("redacted") for _ in range(3)])
    with pytest.raises(ESIError, match="network"):
        await client.fetch_orders(10000002, 55831)
    assert session.get.call_count == 3
    assert retry_clock.sleeps == [1, 2]


async def test_exact_type_names_validated_and_disabled_types_skipped():
    client, session = client_with(
        Response({"name": "Correct"}),
        Response({"name": "Capitalization"}),
        Response(status=404),
    )
    items = [
        SimpleNamespace(type_id=1, display_name="Correct", enabled=True),
        SimpleNamespace(type_id=2, display_name="capitalization", enabled=True),
        SimpleNamespace(type_id=3, display_name="Missing", enabled=True),
        SimpleNamespace(type_id=4, display_name="Ignored", enabled=False),
    ]
    errors = await client.validate_items(items)
    assert set(errors) == {2, 3}
    assert "exactly match" in errors[2]
    assert "404" in errors[3]
    assert session.get.call_count == 3


async def test_station_lookup_and_structure_fallback_require_no_sso():
    client, session = client_with(Response({"name": "Jita IV - Moon 4"}))
    assert await client.location_name(60003760) == "Jita IV - Moon 4"
    assert await client.location_name(1030000000000) == "Structure 1030000000000"
    assert session.get.call_count == 1
    assert "Authorization" not in session.get.call_args.kwargs["headers"]
    await client.close()
    await client.close()
    session.close.assert_awaited_once()


@pytest.mark.parametrize("raw", ['{"error": "wrong shape"}', '[{"order_id": 1}]', "not JSON"])
async def test_invalid_payload_is_unavailable(raw):
    client, _ = client_with(Response(raw=raw))
    with pytest.raises(ESIError):
        await client.fetch_orders(10000002, 55831)


async def test_304_without_cached_body_is_unavailable():
    client, _ = client_with(Response(status=304))
    with pytest.raises(ESIError, match="without a cached"):
        await client.fetch_orders(10000002, 55831)
