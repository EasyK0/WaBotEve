import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import discord
import pytest

from wabot.models import Timer
from wabot.transport import (
    DiscordReminderSender,
    ExpiredDeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
    UnknownDeliveryError,
)


def make_timer():
    return Timer(42, "100", "10", "20", "30", "@everyone <@999> Pilot", "kino", 0, 1380, 1)


class Response:
    def __init__(self, status=200, body=None, error=None, headers=None):
        self.status = status
        self.body = {"id": "9876"} if body is None else body
        self.error = error
        self.headers = headers or {}

    async def json(self, **kwargs):
        if self.error:
            raise self.error
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def setup_sender(*, mention=True, view=True, send=True, response=None):
    channel = Mock(spec=discord.TextChannel)
    channel.type = discord.ChannelType.text
    channel.guild = SimpleNamespace(id=10, unavailable=False, me=object())
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=view, send_messages=send, mention_everyone=mention
    )
    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.return_value = channel
    client.fetch_channel = AsyncMock()
    session = Mock(spec=aiohttp.ClientSession)
    session.post.return_value = response or Response()
    return (
        DiscordReminderSender(
            client, "do-not-log-this-token", [10], session=session, clock=lambda: 1380
        ),
        session,
        channel,
    )


def test_exact_template_and_only_everyone_can_be_mentioned():
    async def scenario():
        sender, session, _ = setup_sender()
        delivery = await sender.send(make_timer(), delayed=False)
        assert delivery.message_id == "9876"
        assert delivery.warning is None
        args, kwargs = session.post.call_args
        assert args == ("https://discord.com/api/v10/channels/20/messages",)
        assert kwargs["allow_redirects"] is False
        assert kwargs["json"] == {
            "content": "@everyone — A new site is expected. Check Kino, Niarja and Archee! After completing it, submit /timer with the payout time in EVE Time (UTC) and the completion system. Timer ID: 42",
            "allowed_mentions": {
                "parse": ["everyone"],
                "users": [],
                "roles": [],
                "replied_user": False,
            },
        }

    asyncio.run(scenario())


async def test_custom_nonping_reminder_has_no_warning_or_allowed_mentions():
    from wabot.messages import MessageSettings

    sender, session, _ = setup_sender(mention=False)
    sender.messages = MessageSettings({"timer_reminder": "Check Kino! Timer {timer_id}"})
    result = await sender.send(make_timer())
    assert result.warning is None
    assert session.post.call_args.kwargs["json"]["allowed_mentions"]["parse"] == []


async def test_pvp_transport_does_not_retry_uncertain_server_error():
    sender, session, _ = setup_sender(response=Response(status=503))
    with pytest.raises(UnknownDeliveryError):
        await sender.post_message("20", {"content": "fight"}, retry_server_errors=False)
    assert session.post.call_count == 1


def test_lost_mention_permission_sends_plain_information_with_warning():
    async def scenario():
        sender, session, _ = setup_sender(mention=False)
        delivery = await sender.send(make_timer(), delayed=True)
        payload = session.post.call_args.kwargs["json"]
        assert payload["content"].startswith("Delayed reminder — A new site is expected.")
        assert "@" not in payload["content"]
        assert payload["allowed_mentions"]["parse"] == []
        assert delivery.warning and "Mention Everyone" in delivery.warning

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,error",
    [
        (403, PermanentDeliveryError),
        (404, PermanentDeliveryError),
        (401, PermanentDeliveryError),
        (500, TransientDeliveryError),
        (503, TransientDeliveryError),
    ],
)
def test_http_failure_classification_does_not_retry_inside_sender(status, error):
    async def scenario():
        sender, session, _ = setup_sender(response=Response(status=status))
        with pytest.raises(error) as caught:
            await sender.send(make_timer())
        assert session.post.call_count == 1
        assert "do-not-log-this-token" not in str(caught.value)

    asyncio.run(scenario())


def test_429_honors_retry_after_and_pauses_other_posts():
    async def scenario():
        sender, session, _ = setup_sender(response=Response(status=429, body={"retry_after": 42.5}))
        with pytest.raises(TransientDeliveryError) as caught:
            await sender.send(make_timer())
        assert caught.value.retry_after == 42.5
        with pytest.raises(TransientDeliveryError) as caught:
            await sender.send(make_timer())
        assert 0 < caught.value.retry_after <= 42.5
        assert session.post.call_count == 1

    asyncio.run(scenario())


def test_429_uses_header_when_body_is_unavailable():
    async def scenario():
        sender, _, _ = setup_sender(
            response=Response(status=429, error=TimeoutError(), headers={"Retry-After": "31"})
        )
        with pytest.raises(TransientDeliveryError) as caught:
            await sender.send(make_timer())
        assert caught.value.retry_after == 31

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("token must not leak"),
        aiohttp.ServerDisconnectedError("token must not leak"),
        OSError("token must not leak"),
    ],
)
def test_network_failure_after_post_is_unknown_and_never_retried(error):
    async def scenario():
        sender, session, _ = setup_sender()
        session.post.side_effect = error
        with pytest.raises(UnknownDeliveryError) as caught:
            await sender.send(make_timer())
        assert "token must not leak" not in str(caught.value)
        assert session.post.call_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [{}, {"id": None}, {"id": "invalid"}])
def test_accepted_response_without_message_id_is_unknown(body):
    async def scenario():
        sender, session, _ = setup_sender(response=Response(body=body))
        with pytest.raises(UnknownDeliveryError):
            await sender.send(make_timer())
        assert session.post.call_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("view,send", [(False, True), (True, False)])
def test_current_channel_permissions_checked_before_post(view, send):
    async def scenario():
        sender, session, _ = setup_sender(view=view, send=send)
        with pytest.raises(PermanentDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "channel_type",
    [
        discord.ChannelType.news,
        discord.ChannelType.public_thread,
        discord.ChannelType.forum,
        discord.ChannelType.private,
    ],
)
def test_only_regular_guild_text_channels_can_receive_reminders(channel_type):
    async def scenario():
        sender, session, channel = setup_sender()
        channel.type = channel_type
        with pytest.raises(PermanentDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())


def test_disconnected_gateway_defers_delivery():
    async def scenario():
        sender, session, _ = setup_sender()
        sender.client.is_ready.return_value = False
        with pytest.raises(TransientDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())


def test_removed_guild_is_terminal():
    async def scenario():
        sender, session, _ = setup_sender()
        sender.guild_ids = frozenset()
        with pytest.raises(PermanentDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())


def test_missing_channel_is_terminal():
    async def scenario():
        sender, session, _ = setup_sender()
        sender.client.get_channel.return_value = None
        sender.client.fetch_channel.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10003, "message": "Unknown Channel"},
        )
        with pytest.raises(PermanentDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())


def test_channel_lookup_cannot_send_after_delivery_window():
    async def scenario():
        sender, session, _ = setup_sender()
        sender._clock = lambda: 1681
        with pytest.raises(ExpiredDeliveryError):
            await sender.send(make_timer())
        session.post.assert_not_called()

    asyncio.run(scenario())
