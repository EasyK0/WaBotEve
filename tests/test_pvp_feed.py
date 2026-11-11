from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from wabot.pvp_feed import FeedError, ZkillFeed


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status, self.headers = data, status, headers or {}

    async def json(self):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def feed(response):
    result = ZkillFeed()
    result._session = Mock(spec=aiohttp.ClientSession)
    result._session.get.return_value = response
    result._session.close = AsyncMock()
    return result


async def test_sequence_and_wrapped_killmail():
    result = feed(Response({"sequence": 100}))
    assert await result.latest_sequence() == 100
    result._session.get.return_value = Response({"esi": {"killmail_id": 1}})
    assert (await result.packet(100))["esi"]["killmail_id"] == 1
    assert result._session.get.call_args.args[0].endswith("/100.json")


@pytest.mark.parametrize("data", [{"error": "bad"}, [], {"sequence": True}, {"sequence": 0}])
async def test_invalid_sequence_is_not_accepted(data):
    with pytest.raises(FeedError):
        await feed(Response(data)).latest_sequence()


async def test_rate_limit_is_respected_across_scans():
    result = feed(Response({}, 429, {"Retry-After": "180"}))
    with pytest.raises(FeedError):
        await result.packet(100)
    with pytest.raises(FeedError, match="cooldown"):
        await result.packet(100)
    assert result._session.get.call_count == 1


async def test_missing_sequence_does_not_advance_client():
    result = feed(Response({}, 404))
    assert await result.packet(100) is None
    import time

    assert result._next_request - time.monotonic() >= 5.9
