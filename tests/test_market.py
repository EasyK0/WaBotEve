import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wabot.bot import Wabot
from wabot.commands import CommandHandlers
from wabot.config import Settings
from wabot.item_catalog import CatalogItem, change_watchlist
from wabot.market import MarketMonitor
from wabot.market_commands import MarketCommands
from wabot.market_config import MarketSettings
from wabot.market_esi import ESIError, FetchedOrders
from wabot.repository import Repository

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


@pytest.fixture
def market(tmp_path):
    config = MarketSettings(enabled=True, channel_id="10")
    settings = Settings("unused", ("1", "2"), tmp_path / "db", tmp_path / "backups", market=config)
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    channel.type = discord.ChannelType.text
    channel.guild = SimpleNamespace(id=1, unavailable=False, me=object())
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )
    channel.send = AsyncMock()
    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.return_value = channel
    client.fetch_channel = AsyncMock(return_value=channel)
    esi = SimpleNamespace(
        validate_items=AsyncMock(return_value={}),
        fetch_orders=AsyncMock(return_value=FetchedOrders([], NOW)),
        location_name=AsyncMock(return_value="Jita 4-4"),
        close=AsyncMock(),
    )
    clock = [NOW]
    monitor = MarketMonitor(client, settings, esi=esi, clock=lambda: clock[0])
    return monitor, channel, esi, clock


def message_text(channel):
    return "\n".join(
        str(embed.to_dict())
        for call in channel.send.call_args_list
        for embed in call.kwargs["embeds"]
    )


async def test_complete_cycle_validates_ids_once_and_sends_without_mentions(market):
    monitor, channel, esi, _ = market
    assert (await monitor.run_once(guild_id=1)).sent
    assert esi.fetch_orders.await_count == 6
    assert monitor.last_successful_read == NOW
    assert monitor.last_report_at == NOW
    assert monitor.last_state == "Live"
    assert "Jita" in message_text(channel)
    assert channel.send.await_count == 2
    assert "Datacasters" in channel.send.call_args_list[0].kwargs["embeds"][0].title
    assert "Other items" in channel.send.call_args_list[1].kwargs["embeds"][0].title
    for call in channel.send.call_args_list:
        assert call.kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert (await monitor.run_once()).sent
    esi.validate_items.assert_awaited_once()


async def test_catalog_item_is_analyzed_and_included_in_existing_report(market):
    monitor, channel, esi, _ = market
    config = change_watchlist(
        {"market": {"enabled": True, "channel_id": "10"}},
        34,
        item=CatalogItem(34, "Tritanium", True),
    )
    monitor.config = MarketSettings.from_config(config["market"], environ={})
    assert (await monitor.run_once(guild_id=1)).sent
    esi.fetch_orders.assert_any_await(10000002, 34)
    assert esi.fetch_orders.await_count == 7
    assert "Tritanium" in message_text(channel)


async def test_one_failed_item_uses_last_good_data_then_expires(market):
    monitor, channel, esi, clock = market
    await monitor.run_once()

    async def partial(region, type_id):
        if type_id == 55831:
            raise ESIError("unavailable")
        return FetchedOrders([], clock[0])

    esi.fetch_orders.side_effect = partial
    clock[0] = NOW + timedelta(minutes=10)
    channel.send.reset_mock()
    assert (await monitor.run_once()).sent
    assert monitor.last_state == "Cached"
    assert "Cached" in message_text(channel)
    assert monitor.last_successful_read == NOW
    clock[0] = NOW + timedelta(minutes=31)
    channel.send.reset_mock()
    assert (await monitor.run_once()).sent
    assert monitor.last_state == "Error"
    assert "DATA ERROR" in message_text(channel)
    assert "Data unavailable" in message_text(channel)
    assert "DATA ERROR" in monitor.status_message()


async def test_initial_item_error_preserves_other_items_and_name_validation_retries(market):
    monitor, channel, esi, _ = market
    esi.validate_items.side_effect = [{55831: "type validation unavailable"}, {}]
    assert (await monitor.run_once()).sent
    assert esi.fetch_orders.await_count == 5
    assert "Data unavailable" in message_text(channel)
    assert monitor.last_successful_read is None
    assert (await monitor.run_once()).sent
    pending = esi.validate_items.call_args.args[0]
    assert [item.type_id for item in pending] == [55831]
    assert monitor.last_successful_read == NOW


@pytest.mark.parametrize("permission", ["view_channel", "send_messages", "embed_links"])
async def test_missing_delivery_permission_prevents_esi_and_send(market, permission):
    monitor, channel, esi, _ = market
    setattr(channel.permissions_for.return_value, permission, False)
    result = await monitor.run_once()
    assert not result.sent and "Embed Links" in result.message
    esi.fetch_orders.assert_not_called()
    channel.send.assert_not_called()


async def test_manual_report_rejects_other_guild_and_wrong_channel_type(market):
    monitor, channel, esi, _ = market
    assert not (await monitor.run_once(guild_id=2)).sent
    channel.type = discord.ChannelType.news
    assert not (await monitor.run_once(guild_id=1)).sent
    esi.fetch_orders.assert_not_called()
    channel.send.assert_not_called()


async def test_overlapping_cycle_is_skipped_without_changing_schedule(market):
    monitor, channel, esi, _ = market
    entered, release = asyncio.Event(), asyncio.Event()
    scheduled = NOW + timedelta(minutes=10)
    monitor.next_report_at = scheduled

    async def slow_fetch(*args):
        entered.set()
        await release.wait()
        return FetchedOrders([], NOW)

    esi.fetch_orders.side_effect = slow_fetch
    task = asyncio.create_task(monitor.run_once())
    await entered.wait()
    try:
        result = await monitor.run_once(guild_id=1)
        assert not result.sent and "already in progress" in result.message
        assert monitor.next_report_at == scheduled
    finally:
        release.set()
        await task
    assert monitor.next_report_at == scheduled


async def test_discord_failure_does_not_stop_next_cycle(market):
    monitor, channel, _, _ = market
    channel.send.side_effect = RuntimeError("pretend network failure")
    result = await monitor.run_once()
    assert not result.sent
    assert monitor.last_report_at is None
    assert monitor.last_error
    channel.send.side_effect = None
    assert (await monitor.run_once()).sent
    assert monitor.last_error is None


async def test_stop_during_cycle_prevents_publication(market):
    monitor, channel, esi, _ = market

    async def stopping(*args):
        monitor.stop()
        return FetchedOrders([], NOW)

    esi.fetch_orders.side_effect = stopping
    assert not (await monitor.run_once()).sent
    channel.send.assert_not_called()


async def test_data_expiring_during_name_lookup_updates_status(market):
    monitor, channel, esi, clock = market

    async def slow_name(*args):
        clock[0] = NOW + timedelta(minutes=31)
        return "Jita 4-4"

    esi.location_name.side_effect = slow_name
    result = await monitor.run_once()
    assert result.sent and "(Error)" in result.message
    assert monitor.last_state == "Error"
    assert "DATA ERROR" in message_text(channel)


async def test_scheduler_only_one_loop_and_stop_interrupts_interval(market):
    monitor, _, _, _ = market
    done = asyncio.Event()

    async def cycle():
        done.set()

    monitor.run_once = AsyncMock(side_effect=cycle)
    task = asyncio.create_task(monitor.run())
    await done.wait()
    await asyncio.wait_for(monitor.run(), timeout=1)
    monitor.run_once.assert_awaited_once()
    assert monitor.next_report_at is not None
    assert (monitor.next_report_at - NOW).total_seconds() == pytest.approx(3600, abs=1)
    monitor.stop()
    await asyncio.wait_for(task, timeout=1)
    assert not monitor._running


async def test_close_cancels_inflight_manual_request_and_closes_http(market):
    monitor, _, esi, _ = market
    entered = asyncio.Event()

    async def stalled(*args):
        entered.set()
        await asyncio.Event().wait()

    esi.fetch_orders.side_effect = stalled
    task = asyncio.create_task(monitor.run_once())
    await entered.wait()
    await asyncio.wait_for(monitor.close(), timeout=1)
    assert task.cancelled()
    assert not monitor._lock.locked()
    esi.close.assert_awaited_once()


async def test_market_worker_waits_for_ready_and_is_not_duplicated(tmp_path):
    repository = Repository(tmp_path / "db")
    config = MarketSettings(enabled=True, channel_id="10")
    settings = Settings("unused", ("1",), repository.path, tmp_path / "backups", market=config)
    client = Wabot(settings, repository)
    ready = asyncio.Event()
    client.wait_until_ready = AsyncMock(side_effect=ready.wait)
    client.backups.run = AsyncMock()
    client.queue.run = AsyncMock()
    client.market.run = AsyncMock()
    # The mock workers intentionally exit, so disable the production crash callback.
    client.worker_finished = Mock()
    async with client:
        await client.setup_hook()
        original = list(client.background)
        await client.setup_hook()
        await asyncio.sleep(0)
        assert client.background == original
        client.market.run.assert_not_called()
        ready.set()
        await asyncio.sleep(0)
        client.market.run.assert_awaited_once()


def admin_interaction(*, administrator=True, guild=1):
    interaction = Mock(spec=discord.Interaction)
    interaction.guild = Mock(spec=discord.Guild)
    interaction.guild_id = guild
    interaction.channel = Mock(spec=discord.TextChannel)
    interaction.channel.type = discord.ChannelType.text
    interaction.permissions = SimpleNamespace(administrator=administrator)
    interaction.app_permissions = SimpleNamespace(view_channel=True, send_messages=True)
    interaction.response = Mock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    async def defer(**kwargs):
        interaction.response.is_done.return_value = True

    interaction.response.defer = AsyncMock(side_effect=defer)
    interaction.edit_original_response = AsyncMock()
    return interaction


async def test_market_now_requires_admin_and_publishes_to_configured_channel(market):
    monitor, channel, _, _ = market
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    denied = admin_interaction(administrator=False)
    await handlers.market_now(denied)
    assert "administrators" in denied.response.send_message.call_args.args[0]
    channel.send.assert_not_called()
    accepted = admin_interaction()
    await handlers.market_now(accepted)
    accepted.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert "<#10>" in accepted.edit_original_response.call_args.kwargs["content"]
    assert channel.send.called


async def test_market_status_does_not_fetch_orders_or_publish_report(market):
    monitor, channel, esi, _ = market
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    interaction = admin_interaction()
    await handlers.market_status(interaction)
    assert (
        "Last complete successful read"
        in interaction.edit_original_response.call_args.kwargs["content"]
    )
    esi.fetch_orders.assert_not_called()
    channel.send.assert_not_called()


async def test_enabled_command_schema_preserves_existing_commands(market):
    monitor, _, _, _ = market
    client = Wabot(monitor.settings, Repository(monitor.settings.database_path))
    try:
        commands = {
            c.name: c.to_dict(client.tree)
            for c in client.tree.get_commands(guild=discord.Object(id=1))
        }
        assert set(commands) == {
            "timer",
            "stats",
            "rank",
            "market-now",
            "market-status",
            "market-analysis",
        }
        for name in ("market-now", "market-status"):
            assert int(commands[name]["default_member_permissions"]) == 8
        analysis = commands["market-analysis"]
        assert analysis.get("default_member_permissions") is None
        assert analysis["options"][0]["name"] == "item"
        assert analysis["options"][0]["autocomplete"] is True
        assert not analysis["options"][0].get("required", False)
    finally:
        await client.close()
