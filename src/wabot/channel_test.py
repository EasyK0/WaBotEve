"""Explicit channel delivery checks without starting the bot or touching its database."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import discord

from wabot.pvp_config import PvpSettings
from wabot.servers import parse_servers
from wabot.transport import (
    DiscordReminderSender,
    PermanentDeliveryError,
    TransientDeliveryError,
    UnknownDeliveryError,
)


def saved_channels(config, *, pvp_only=False, guild_id=None):
    """Saved channels, optionally only those of one server."""
    channels = {}
    if "servers" not in config:
        # Single-server profile: every saved channel, also before monitoring is enabled.
        pvp = PvpSettings.from_config(config.get("pvp"))
        for system, channel in pvp.system_channels.items():
            channels.setdefault(channel, []).append(system)
        if pvp.pochven_channel:
            channels.setdefault(pvp.pochven_channel, []).append("All Pochven")
        if pvp.alliance_channel:
            channels.setdefault(pvp.alliance_channel, []).append("Alliance kills")
        if not pvp_only:
            channel = config.get("market", {}).get("channel_id")
            if channel:
                channels.setdefault(channel, []).append("Jita + Perimeter market")
        return channels
    for server in parse_servers(config["servers"]):
        if guild_id is not None and server.guild_id != guild_id:
            continue
        # Saved channels can be tested before their feature is switched on.
        for system, channel in server.pvp_settings.system_channels.items():
            channels.setdefault(channel, []).append(system)
        if server.pvp_settings.pochven_channel:
            channels.setdefault(server.pvp_settings.pochven_channel, []).append("All Pochven")
        if server.pvp_settings.alliance_channel:
            channels.setdefault(server.pvp_settings.alliance_channel, []).append("Alliance kills")
        if not pvp_only and server.market_channel_id:
            channels.setdefault(server.market_channel_id, []).append("Jita + Perimeter market")
    return channels


@dataclass(frozen=True)
class ChannelTestResult:
    channel_id: str
    success: bool
    detail: str


async def send_test_messages(token, guild_ids, channels, *, client=None, sender=None, report=None):
    """One visibly marked, unpinged message per unique saved channel; no automatic retries."""
    client = client or discord.Client(intents=discord.Intents.none())
    sender = sender or DiscordReminderSender(client, token, guild_ids)
    results = []

    def record(channel_id, success, detail):
        result = ChannelTestResult(channel_id, success, detail)
        results.append(result)
        if report is not None:
            report(result)

    try:
        # HTTP login only: no gateway or reminder/market/PvP background workers.
        try:
            async with asyncio.timeout(15):
                await client.login(token)
        except discord.LoginFailure:
            for channel_id in channels:
                record(channel_id, False, "Invalid bot token.")
            return results
        for channel_id, labels in channels.items():
            try:
                async with asyncio.timeout(15):
                    channel = await client.fetch_channel(int(channel_id))
                if (
                    not isinstance(channel, discord.TextChannel)
                    or channel.type != discord.ChannelType.text
                ):
                    record(channel_id, False, "A regular server text channel is required.")
                    continue
                if str(channel.guild.id) not in guild_ids:
                    record(channel_id, False, "The channel does not belong to a saved server.")
                    continue
                embed = discord.Embed(
                    title="Wabot — channel test",
                    description="Connection works.\nAssigned to: " + ", ".join(labels),
                    color=0x22C55E,
                    timestamp=datetime.now(UTC),
                )
                embed.set_footer(text="Test message from Wabot settings")
                await sender.post_message(
                    channel_id,
                    {
                        "content": "**WABOT TEST** — checking a saved channel.",
                        "embeds": [embed.to_dict()],
                        "allowed_mentions": discord.AllowedMentions.none().to_dict(),
                    },
                    retry_server_errors=False,
                )
                record(channel_id, True, "Test message sent.")
            except discord.Forbidden:
                record(channel_id, False, "The bot has no access to this channel.")
            except discord.NotFound:
                record(channel_id, False, "Channel not found — check the ID.")
            except PermanentDeliveryError:
                record(
                    channel_id,
                    False,
                    "Discord rejected the message — check the channel and permissions.",
                )
            except TransientDeliveryError:
                record(
                    channel_id, False, "Discord rate limit or connection problem. Try again later."
                )
            except (UnknownDeliveryError, TimeoutError):
                record(
                    channel_id,
                    False,
                    "Delivery was not confirmed. Check the channel before testing again.",
                )
            except Exception:
                record(
                    channel_id,
                    False,
                    "Could not check the channel. Check the connection to Discord.",
                )
        return results
    finally:
        try:
            await sender.close()
        finally:
            await client.close()
