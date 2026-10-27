from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wabot.channel_test import saved_channels, send_test_messages
from wabot.transport import UnknownDeliveryError


def fake_client():
    channel = Mock(spec=discord.TextChannel)
    channel.type = discord.ChannelType.text
    channel.guild = SimpleNamespace(id=1)
    return SimpleNamespace(
        login=AsyncMock(), fetch_channel=AsyncMock(return_value=channel), close=AsyncMock()
    )


def test_channel_list_deduplicates_systems_and_market():
    config = {
        "pvp": {"system_channels": {"Kino": "2", "Niarja": "2", "Archee": "3"}},
        "market": {"channel_id": "2"},
    }
    assert saved_channels(config) == {
        "2": ["Kino", "Niarja", "Jita + Perimeter market"],
        "3": ["Archee"],
    }
    assert saved_channels(config, pvp_only=True)["2"] == ["Kino", "Niarja"]


async def test_sends_one_explicit_test_per_channel_without_mentions():
    client = fake_client()
    sender = SimpleNamespace(post_message=AsyncMock(), close=AsyncMock())
    report = Mock()
    results = await send_test_messages(
        "token",
        ("1",),
        {"2": ["Kino", "Niarja"], "3": ["Archee"]},
        client=client,
        sender=sender,
        report=report,
    )
    assert len(results) == 2 and all(result.success for result in results)
    assert report.call_count == 2
    calls = sender.post_message.call_args_list
    assert [call.args[0] for call in calls] == ["2", "3"]
    payload = calls[0].args[1]
    assert "WABOT TEST" in payload["content"]
    assert "Kino, Niarja" in payload["embeds"][0]["description"]
    assert payload["allowed_mentions"]["parse"] == []
    assert calls[0].kwargs == {"retry_server_errors": False}
    client.close.assert_awaited_once()
    sender.close.assert_awaited_once()


async def test_one_bad_channel_does_not_block_the_others():
    client = fake_client()
    good = client.fetch_channel.return_value
    client.fetch_channel.side_effect = [
        discord.Forbidden(Mock(status=403, reason="Forbidden"), "private"),
        good,
    ]
    sender = SimpleNamespace(post_message=AsyncMock(), close=AsyncMock())
    results = await send_test_messages(
        "secret-token", ("1",), {"2": ["Kino"], "3": ["Archee"]}, client=client, sender=sender
    )
    assert [result.success for result in results] == [False, True]
    assert sender.post_message.call_args.args[0] == "3"
    assert "private" not in str(results) and "secret-token" not in str(results)


async def test_wrong_guild_never_receives_test():
    client = fake_client()
    sender = SimpleNamespace(post_message=AsyncMock(), close=AsyncMock())
    results = await send_test_messages(
        "token", ("99",), {"2": ["Kino"]}, client=client, sender=sender
    )
    assert not results[0].success
    sender.post_message.assert_not_awaited()


async def test_bad_token_reports_failure_without_exposing_token():
    client = fake_client()
    client.login.side_effect = discord.LoginFailure("secret-token")
    sender = SimpleNamespace(post_message=AsyncMock(), close=AsyncMock())
    results = await send_test_messages(
        "secret-token", ("1",), {"2": ["Kino"]}, client=client, sender=sender
    )
    assert not results[0].success and "secret-token" not in str(results)
    sender.post_message.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("error", [UnknownDeliveryError("secret"), TimeoutError("secret")])
async def test_uncertain_send_is_not_retried(error):
    client = fake_client()
    sender = SimpleNamespace(post_message=AsyncMock(side_effect=error), close=AsyncMock())
    results = await send_test_messages(
        "token", ("1",), {"2": ["Kino"]}, client=client, sender=sender
    )
    assert not results[0].success and "secret" not in str(results)
    sender.post_message.assert_awaited_once()
