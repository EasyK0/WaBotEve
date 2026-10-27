import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wabot.bot import Wabot
from wabot.channel_test import saved_channels
from wabot.config import ConfigurationError, Settings
from wabot.desktop_support import validate_config
from wabot.market_config import MarketSettings
from wabot.pvp import PvpMonitor
from wabot.pvp_config import PvpSettings
from wabot.repository import Repository
from wabot.servers import ServerSettings, migrate_config, servers_from_config

LEGACY = {
    "guild_ids": ["1", "2"],
    "market": {"enabled": True, "channel_id": "10"},
    "pvp": {"enabled": True, "ping": "none", "role_id": None, "system_channels": {"Kino": "20"}},
}


def server(guild, **features):
    return {
        "id": guild,
        "name": f"Serwer {guild}",
        "features": {"timers": False, "market": False, "pvp": False, **features},
        "market": {"channel_id": None},
        "pvp": {"ping": "none", "role_id": None, "system_channels": {}},
    }


def test_legacy_profile_migrates_to_first_server():
    migrated = migrate_config(LEGACY)
    assert "pvp" not in migrated
    assert migrated["guild_ids"] == ["1", "2"]
    first, second = migrated["servers"]
    assert first["features"] == {"timers": True, "market": True, "pvp": True}
    assert first["market"]["channel_id"] == "10"
    assert first["pvp"]["system_channels"] == {"Kino": "20"}
    assert second["features"] == {"timers": True, "market": False, "pvp": False}
    assert validate_config(migrated)["servers"] == migrated["servers"]
    assert servers_from_config(migrated) == servers_from_config(LEGACY)


def test_server_validation_rejects_duplicates_and_bad_ids():
    with pytest.raises(ValueError):
        validate_config({"servers": [server("1"), server("1")]})
    with pytest.raises(ValueError):
        validate_config({"servers": [server("abc")]})
    with pytest.raises(ValueError):
        validate_config({"servers": []})
    bad_role = server("1", pvp=True)
    bad_role["pvp"]["ping"] = "role"
    with pytest.raises(ValueError):
        validate_config({"servers": [bad_role]})


def test_settings_load_servers_and_feature_guilds(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"servers": [server("1", timers=True), server("2", market=True)]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    settings = Settings.from_env()
    assert settings.guild_ids == ("1", "2")
    assert settings.feature_guilds("timers") == ("1",)
    assert settings.feature_guilds("market") == ("2",)
    assert not settings.feature_enabled("2", "timers")
    path.write_text(json.dumps({"servers": [{"id": "1", "extra": 1}]}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_test_channels_are_limited_to_the_selected_server():
    first = server("1")
    first["market"]["channel_id"] = "10"
    second = server("2")
    second["pvp"]["system_channels"] = {"Kino": "20"}
    config = {"servers": [first, second]}
    assert saved_channels(config, guild_id="1") == {"10": ["Jita + Perimeter market"]}
    assert saved_channels(config, guild_id="2") == {"20": ["Kino"]}
    assert saved_channels(config, pvp_only=True, guild_id="1") == {}


async def test_commands_are_registered_only_on_servers_with_the_feature(tmp_path):
    repo = Repository(tmp_path / "db")
    settings = Settings(
        "unused",
        ("1", "2"),
        repo.path,
        tmp_path / "backups",
        servers=(
            ServerSettings("1", timers=True),
            ServerSettings("2", market=True, market_channel_id="10"),
        ),
    )
    bot = Wabot(settings, repo)
    try:
        names = {
            guild: {c.name for c in bot.tree.get_commands(guild=discord.Object(id=int(guild)))}
            for guild in ("1", "2")
        }
        assert names["1"] == {"timer", "stats", "rank"}
        assert names["2"] == {"market-now", "market-status", "market-analysis"}
        assert bot.pvp is None and bot.market is not None
    finally:
        await bot.close()


async def test_timer_command_is_refused_where_timers_are_off(tmp_path):
    repo = Repository(tmp_path / "db")
    settings = Settings(
        "unused",
        ("1", "2"),
        repo.path,
        tmp_path / "backups",
        servers=(ServerSettings("1", timers=True), ServerSettings("2")),
    )
    bot = Wabot(settings, repo)
    try:
        interaction = Mock()
        interaction.guild = object()
        interaction.guild_id = 2
        interaction.channel = Mock(spec=discord.TextChannel)
        interaction.channel.type = discord.ChannelType.text
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        assert not await bot.handlers.context(interaction, timer=True)
        message = interaction.response.send_message.call_args.args[0]
        assert "turned off" in message
    finally:
        await bot.close()


async def test_one_kill_goes_to_every_server_with_its_own_ping(tmp_path):
    from tests.test_pvp import NOW, VALE, Feed, packet

    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    settings = Settings(
        "unused",
        ("1", "2"),
        repo.path,
        tmp_path / "backups",
        servers=(
            ServerSettings("1", pvp=True, pvp_settings=PvpSettings(system_channels={"Kino": "21"})),
            ServerSettings(
                "2",
                pvp=True,
                pvp_settings=PvpSettings(ping="everyone", system_channels={"Kino": "22"}),
            ),
            # Off: never receives killmails even though a channel is saved.
            ServerSettings("3", pvp_settings=PvpSettings(system_channels={"Kino": "23"})),
        ),
    )
    guilds = {1: SimpleNamespace(id=1), 2: SimpleNamespace(id=2)}

    def channel(channel_id):
        result = Mock(spec=discord.TextChannel)
        result.type = discord.ChannelType.text
        guild = guilds[1 if channel_id == 21 else 2]
        result.guild = SimpleNamespace(id=guild.id, unavailable=False, me=object())
        result.permissions_for.return_value = SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True, mention_everyone=True
        )
        return result

    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.side_effect = channel
    worker = PvpMonitor(
        client,
        settings,
        repo,
        feed=Feed([packet(1)]),
        esi=SimpleNamespace(
            pochven_systems=AsyncMock(return_value={VALE}),
            name=AsyncMock(return_value="Kino"),
            close=AsyncMock(),
        ),
        clock=lambda: NOW,
        sender=SimpleNamespace(post_message=AsyncMock()),
    )
    try:
        await worker.run_once()
        calls = worker.sender.post_message.call_args_list
        assert [call.args[0] for call in calls] == ["21", "22"]
        assert not calls[0].args[1]["content"].startswith("@everyone")
        assert calls[1].args[1]["content"].startswith("@everyone")
        # A restart must not resend to either server.
        worker.state = None
        worker.feed.packets[101] = packet(1)
        await worker.run_once()
        assert worker.sender.post_message.await_count == 2
    finally:
        await worker.close()
        await repo.close()


async def test_kill_delivered_by_an_older_version_is_not_resent(tmp_path):
    from tests.test_pvp import NOW, VALE, Feed, packet

    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    await repo.save_pvp_state(
        {"sequence": 100, "killmail_deliveries": {"1": {"status": "sent"}}, "kills": {}}
    )
    settings = Settings(
        "unused",
        ("1",),
        repo.path,
        tmp_path / "backups",
        pvp=PvpSettings(enabled=True, system_channels={"Kino": "21"}),
    )
    worker = PvpMonitor(
        Mock(spec=discord.Client),
        settings,
        repo,
        feed=Feed([packet(1)]),
        esi=SimpleNamespace(
            pochven_systems=AsyncMock(return_value={VALE}),
            name=AsyncMock(return_value="Kino"),
            close=AsyncMock(),
        ),
        clock=lambda: NOW,
        sender=SimpleNamespace(post_message=AsyncMock()),
    )
    try:
        await worker.run_once()
        worker.sender.post_message.assert_not_awaited()
    finally:
        await worker.close()
        await repo.close()


async def test_market_report_goes_to_each_enabled_server(tmp_path):
    from wabot.market import MarketMonitor

    settings = Settings(
        "unused",
        ("1", "2"),
        tmp_path / "db",
        tmp_path / "backups",
        market=replace(MarketSettings(), items=MarketSettings().items[:1]),
        servers=(
            ServerSettings("1", market=True, market_channel_id="11"),
            ServerSettings("2", market=True, market_channel_id="12"),
        ),
    )
    channels = {}

    def channel(channel_id):
        result = Mock(spec=discord.TextChannel)
        result.id = channel_id
        result.type = discord.ChannelType.text
        result.guild = SimpleNamespace(id=channel_id - 10, unavailable=False, me=object())
        result.permissions_for.return_value = SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True
        )
        result.send = AsyncMock()
        return channels.setdefault(channel_id, result)

    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.side_effect = channel
    monitor = MarketMonitor(client, settings, esi=Mock())
    monitor._snapshots = AsyncMock(return_value=[])
    monitor.esi.location_name = AsyncMock(return_value="Jita")
    monitor.esi.close = AsyncMock()
    result = await monitor.run_once()
    assert result.sent
    assert channels[11].send.await_count and channels[12].send.await_count
    # /market-now in server 2 publishes only there.
    channels[11].send.reset_mock()
    await monitor.run_once(guild_id=2)
    channels[11].send.assert_not_awaited()
    await monitor.close()
