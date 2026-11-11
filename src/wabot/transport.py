"""Discord reminder transport without automatic retries of ambiguous POST requests."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import aiohttp
import discord

from .formatting import timer_values
from .messages import MessageSettings
from .models import GRACE_SECONDS, Timer

MISSING_MENTION_WARNING = "Mention Everyone permission was lost; reminder sent without a mention."


@dataclass(frozen=True, slots=True)
class Delivery:
    message_id: str
    warning: str | None = None


class PermanentDeliveryError(Exception):
    """A safe, credential-free explanation of a terminal delivery failure."""


class ExpiredDeliveryError(PermanentDeliveryError):
    """The five-minute delivery window ended during a read-only channel lookup."""


class TransientDeliveryError(Exception):
    """A request known not to have delivered a message and safe to retry."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UnknownDeliveryError(Exception):
    """Discord may have accepted the message, so another POST is unsafe."""


def reminder_payload(
    timer: Timer, *, delayed: bool, mention_everyone: bool, messages: MessageSettings | None = None
) -> dict:
    messages = messages or MessageSettings()
    mention_everyone = mention_everyone and messages.timer_mentions_everyone()
    return {
        "content": messages.render(
            "timer_reminder",
            **timer_values(timer),
            title="Completion recorded",
            increment="+1",
            delayed=messages.render("timer_delayed_prefix") if delayed else "",
            mention="@everyone — " if mention_everyone else "",
        ),
        "allowed_mentions": {
            "parse": ["everyone"] if mention_everyone else [],
            "users": [],
            "roles": [],
            "replied_user": False,
        },
    }


class DiscordReminderSender:
    def __init__(
        self,
        client: discord.Client,
        token: str,
        guild_ids: Iterable[int | str],
        *,
        session: aiohttp.ClientSession | None = None,
        clock: Callable[[], float] = time.time,
        messages: MessageSettings | None = None,
    ) -> None:
        self.client = client
        self._token = token
        self.guild_ids = frozenset(str(guild_id) for guild_id in guild_ids)
        self._session = session
        self._owns_session = session is None
        self._clock = clock
        self.messages = messages or MessageSettings()
        self._rate_limited_until = 0.0

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def send(self, timer: Timer, *, delayed: bool = False) -> Delivery:
        if timer.guild_id not in self.guild_ids:
            raise PermanentDeliveryError("The timer's server is no longer configured.")
        if not self.client.is_ready():
            raise TransientDeliveryError("Discord connection is not ready.")
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            raise TransientDeliveryError("Discord rate limit is active.", retry_after=remaining)

        channel = self.client.get_channel(int(timer.channel_id))
        if channel is None:
            try:
                channel = await self.client.fetch_channel(int(timer.channel_id))
            except (discord.NotFound, discord.Forbidden):
                raise PermanentDeliveryError(
                    "The reminder channel is missing or inaccessible."
                ) from None
            except (discord.HTTPException, aiohttp.ClientError, TimeoutError):
                # Fetching a channel is read-only, so failures here are safe to retry.
                raise TransientDeliveryError("Could not look up the reminder channel.") from None

        if not isinstance(channel, discord.TextChannel) or channel.type != discord.ChannelType.text:
            raise PermanentDeliveryError(
                "The reminder channel is not a normal server text channel."
            )
        if str(channel.guild.id) != timer.guild_id:
            raise PermanentDeliveryError(
                "The reminder channel does not belong to the timer's server."
            )
        if channel.guild.unavailable:
            raise TransientDeliveryError("The reminder server is temporarily unavailable.")
        member = channel.guild.me
        if member is None:
            raise TransientDeliveryError("Bot membership is not available yet.")
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise PermanentDeliveryError(
                "The bot cannot view or send messages in the reminder channel."
            )

        # Channel lookup may take time. Recheck the window immediately before POST.
        late_by = self._clock() - timer.due_at
        if late_by > GRACE_SECONDS:
            raise ExpiredDeliveryError("Reminder expired before the send request could start.")
        payload = reminder_payload(
            timer,
            delayed=delayed or late_by >= 30,
            mention_everyone=permissions.mention_everyone,
            messages=self.messages,
        )
        delivery = await self.post_message(timer.channel_id, payload)
        warning = (
            MISSING_MENTION_WARNING
            if self.messages.timer_mentions_everyone() and not permissions.mention_everyone
            else None
        )
        return Delivery(message_id=delivery.message_id, warning=warning)

    async def post_message(
        self, channel_id: str, payload: dict, *, retry_server_errors: bool = True
    ) -> Delivery:
        """One POST, with no hidden retries. The caller owns durable delivery state."""
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            raise TransientDeliveryError("Discord rate limit is active.", retry_after=remaining)
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=5),
                headers={"Authorization": f"Bot {self._token}", "User-Agent": "Wabot/1.0"},
            )

        # aiohttp does not automatically retry POST. Do not use channel.send():
        # discord.py retries selected network errors, risking duplicate @everyone pings.
        try:
            async with self._session.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                json=payload,
                allow_redirects=False,
            ) as response:
                if response.status == 429:
                    retry_after = 5.0
                    try:
                        candidate = float(response.headers.get("Retry-After", 5))
                        if math.isfinite(candidate) and candidate >= 0:
                            retry_after = candidate
                    except (ValueError, TypeError):
                        pass
                    try:
                        body = await response.json(content_type=None)
                        candidate = float(body.get("retry_after", retry_after))
                        if math.isfinite(candidate) and candidate >= 0:
                            retry_after = candidate
                    except (
                        ValueError,
                        TypeError,
                        AttributeError,
                        aiohttp.ClientError,
                        TimeoutError,
                    ):
                        pass
                    # Serial sends share a rate-limit pause, including other due timers.
                    self._rate_limited_until = time.monotonic() + retry_after
                    raise TransientDeliveryError(
                        "Discord rate limited the request.", retry_after=retry_after
                    )
                if response.status >= 500:
                    if not retry_server_errors:
                        raise UnknownDeliveryError(
                            "Discord returned a server error; delivery is uncertain."
                        )
                    raise TransientDeliveryError("Discord returned a server error.")
                if response.status in (403, 404):
                    raise PermanentDeliveryError(
                        "The reminder channel is missing or sending is forbidden."
                    )
                if response.status != 200:
                    raise PermanentDeliveryError(
                        f"Discord rejected the reminder (HTTP {response.status})."
                    )
                try:
                    body = await response.json(content_type=None)
                    message_id = body["id"]
                    if not isinstance(message_id, str) or not message_id.isdigit():
                        raise ValueError("Missing message identifier")
                except (ValueError, KeyError, TypeError):
                    raise UnknownDeliveryError(
                        "Discord accepted the request but returned no valid message ID."
                    ) from None
        except aiohttp.ClientConnectorError:
            raise TransientDeliveryError("Could not connect to Discord.") from None
        except (TimeoutError, aiohttp.ClientError, OSError):
            # Even a socket reset can arrive after the POST was accepted.
            raise UnknownDeliveryError(
                "Discord request was interrupted; delivery could not be confirmed."
            ) from None

        return Delivery(message_id=message_id)
