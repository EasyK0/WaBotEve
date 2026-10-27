import asyncio
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from wabot.bot import Wabot
from wabot.config import Settings
from wabot.pvp import PvpMonitor
from wabot.pvp_config import PvpSettings
from wabot.pvp_feed import FeedError
from wabot.repository import Repository
from wabot.transport import TransientDeliveryError, UnknownDeliveryError

NOW = datetime(2026, 9, 21, 1, 35, tzinfo=UTC).timestamp()
VALE = 30002702


def test_old_profile_migrates_shared_channel_and_removes_thresholds():
    settings = PvpSettings.from_config(
        {
            "enabled": True,
            "channel_id": "123",
            "min_kills": 3,
            "window_minutes": 5,
            "cooldown_minutes": 10,
        }
    )
    assert settings.system_channels == {"Kino": "123", "Niarja": "123", "Archee": "123"}
    assert set(asdict(settings)) == {
        "enabled",
        "ping",
        "role_id",
        "system_channels",
        "pochven_channel",
        "alliance_ids",
        "alliance_channel",
        "here_min_value",
        "alliance_here_loss_value",
    }


async def test_bad_record_is_skipped_and_duplicates_do_not_replay(monitor):
    worker, _, _ = monitor
    worker.feed = Feed([{"esi": None}, packet(1), packet(1)])
    await asyncio.gather(worker.run_once(), worker.run_once())
    worker.sender.post_message.assert_awaited_once()
    assert worker.state["sequence"] == 103


async def test_storage_failure_before_send_keeps_kill_pending(monitor):
    worker, _, _ = monitor
    worker.feed = Feed([packet(1)])
    original = worker.repository.save_pvp_state

    async def save(state):
        if state.get("killmail_deliveries", {}).get("1:2", {}).get("status") == "sending":
            raise OSError("disk full")
        await original(state)

    worker.repository.save_pvp_state = save
    await worker.run_once()
    worker.sender.post_message.assert_not_awaited()
    worker.repository.save_pvp_state = original
    await worker.run_once()
    worker.sender.post_message.assert_awaited_once()


async def test_channel_permissions_and_guild_boundaries(monitor):
    worker, channel, _ = monitor
    channel.guild.id = 999
    with pytest.raises(ValueError):
        await worker.alert_channel("2")
    channel.guild.id = 1
    channel.permissions_for.return_value.embed_links = False
    with pytest.raises(ValueError):
        await worker.alert_channel("2")


async def test_explicit_role_ping_only_mentions_selected_role(monitor):
    worker, channel, _ = monitor
    worker.servers = [("1", replace(worker.servers[0][1], ping="role", role_id="123"))]
    channel.guild.get_role = lambda _: SimpleNamespace(is_default=lambda: False, mentionable=True)
    worker.feed = Feed([packet(1)])
    await worker.run_once()
    payload = worker.sender.post_message.call_args.args[1]
    assert "<@&123>" in payload["content"]
    assert payload["allowed_mentions"]["roles"] == [123]
    assert payload["allowed_mentions"]["parse"] == []


@pytest.fixture
async def monitor(tmp_path):
    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    settings = Settings(
        "unused",
        ("1",),
        repo.path,
        tmp_path / "backups",
        pvp=PvpSettings(enabled=True, system_channels={"Kino": "2"}),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.type = discord.ChannelType.text
    channel.guild = SimpleNamespace(id=1, unavailable=False, me=object(), get_role=lambda _: None)
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True, mention_everyone=True
    )
    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.return_value = channel
    clock = [NOW]
    esi = SimpleNamespace(
        pochven_systems=AsyncMock(return_value={VALE}),
        name=AsyncMock(return_value="Kino"),
        close=AsyncMock(),
    )
    worker = PvpMonitor(
        client,
        settings,
        repo,
        feed=Feed([packet(i) for i in (1, 2, 3)]),
        esi=esi,
        clock=lambda: clock[0],
        sender=SimpleNamespace(post_message=AsyncMock()),
    )
    yield worker, channel, clock
    await worker.close()
    await repo.close()


def packet(kill_id, *, age=60, system=VALE, npc=False, capsule=False, corp=20):
    return {
        "esi": {
            "killmail_id": kill_id,
            "killmail_time": datetime.fromtimestamp(NOW - age, UTC).isoformat(),
            "solar_system_id": system,
            "victim": {
                "character_id": 1,
                "corporation_id": corp,
                "alliance_id": 21,
                "ship_type_id": 670 if capsule else 638,
            },
            "attackers": [
                {"corporation_id": 100, "final_blow": True},
                *(
                    []
                    if npc
                    else [
                        {"character_id": 2, "corporation_id": 10, "alliance_id": 11},
                        {"character_id": 3, "corporation_id": 10, "alliance_id": 11},
                    ]
                ),
            ],
        },
        "zkb": {"npc": npc},
    }


class Feed:
    def __init__(self, packets):
        self.packets = dict(enumerate(packets, start=100))
        self.reads = []

    async def latest_sequence(self):
        return 100

    async def packet(self, sequence):
        self.reads.append(sequence)
        item = self.packets.get(sequence)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        pass


async def configure_routes(worker, routes):
    worker.servers = [("1", replace(worker.servers[0][1], ping="none", system_channels=routes))]
    worker.esi.pochven_systems.return_value = {VALE, 300001, 300002, 300003}
    worker.esi.name = AsyncMock(
        side_effect=lambda kind, entity: {
            VALE: "Kino",
            300001: "Niarja",
            300002: "Archee",
            300003: "Vale",
        }[entity]
    )
    worker.sender.post_message = AsyncMock()


async def test_routes_forward_every_kill_and_allow_shared_channel(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22", "Niarja": "22", "Archee": "33"})
    worker.feed = Feed(
        [
            packet(1, capsule=True),
            packet(2, system=300001, npc=True, age=1800),
            packet(3, system=300002),
            packet(4, system=300003),
        ]
    )
    await worker.run_once()
    calls = worker.sender.post_message.call_args_list
    assert [call.args[0] for call in calls] == ["22", "22", "33"]
    for call, system, kill in zip(calls, ("Kino", "Niarja", "Archee"), (1, 2, 3), strict=True):
        assert system in call.args[1]["content"]
        assert f"https://zkillboard.com/kill/{kill}/" in call.args[1]["content"]
        assert call.args[1]["allowed_mentions"]["parse"] == []
    worker.feed.packets[104] = packet(1)
    worker.state = None  # Reload durable cursor and deduplication as after a restart.
    await worker.run_once()
    assert worker.sender.post_message.await_count == 3


async def test_route_failure_retries_without_blocking_other_channels(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22", "Archee": "33"})
    worker.feed = Feed([packet(1), packet(2, system=300002)])
    worker.sender.post_message.side_effect = [TransientDeliveryError("offline"), None, None]
    await worker.run_once()
    assert "1" in worker.state["kills"]
    assert "2" not in worker.state["kills"]
    await worker.run_once()
    assert [c.args[0] for c in worker.sender.post_message.call_args_list] == ["22", "33", "22"]


async def test_route_uncertain_delivery_is_not_duplicated(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    worker.feed = Feed([packet(1)])
    worker.sender.post_message.side_effect = UnknownDeliveryError("timeout")
    await worker.run_once()
    worker.state = None
    worker.feed.packets[101] = packet(1)
    await worker.run_once()
    worker.sender.post_message.assert_awaited_once()


async def test_single_kill_is_sent_before_reading_the_next_feed_record(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})

    async def read(sequence):
        if sequence == 100:
            return packet(1, age=0)
        worker.sender.post_message.assert_awaited_once()
        saved = await worker.repository.load_pvp_state()
        assert saved["sequence"] == 101
        assert saved["killmail_deliveries"]["1:22"]["status"] == "sent"
        return None

    worker.feed.packet = read
    worker.feed.latest_sequence = AsyncMock(return_value=100)
    await worker.run_once()
    # Reaching the live edge should not make an extra pointer request and wait again.
    worker.feed.latest_sequence.assert_awaited_once()


async def test_busy_feed_preserves_cursor_for_the_next_scheduled_batch(monitor, monkeypatch):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    monkeypatch.setattr("wabot.pvp.FEED_BATCH_RECORDS", 1)

    await worker.run_once()
    assert worker.state["sequence"] == 101
    worker.sender.post_message.assert_awaited_once()
    await worker.run_once()
    assert worker.state["sequence"] == 102
    assert worker.sender.post_message.await_count == 2


@pytest.mark.parametrize("scan_duration, expected_wait", [(4, 26), (7, 23), (35, 25)])
async def test_scheduler_uses_fixed_thirty_second_slots(
    monitor, monkeypatch, scan_duration, expected_wait
):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    waits = []
    clock = [0]

    async def scan():
        clock[0] += scan_duration

    async def wait(coroutine, delay):
        coroutine.close()
        waits.append(delay)
        worker.stop()
        raise TimeoutError

    worker.run_once = scan
    monkeypatch.setattr(
        "wabot.pvp.asyncio.get_running_loop", lambda: SimpleNamespace(time=lambda: clock[0])
    )
    monkeypatch.setattr("wabot.pvp.asyncio.wait_for", wait)
    await worker.run()
    assert waits == [expected_wait]


async def test_failed_pending_kill_is_retried_before_reading_more_feed(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    worker.feed = Feed([packet(1)])
    worker.sender.post_message.side_effect = TransientDeliveryError("offline")
    await worker.run_once()
    worker.sender.post_message.side_effect = None

    async def slow_feed(sequence):
        assert worker.sender.post_message.await_count == 2
        raise FeedError("upstream unavailable")

    worker.feed.packet = slow_feed
    await worker.run_once()
    assert worker.state["killmail_deliveries"]["1:22"]["status"] == "sent"


async def test_immediate_send_survives_shutdown_without_duplicate(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    worker.feed = Feed([packet(1)])
    started = asyncio.Event()

    async def send(*args, **kwargs):
        saved = await worker.repository.load_pvp_state()
        assert saved["sequence"] == 101
        assert saved["killmail_deliveries"]["1:22"]["status"] == "sending"
        started.set()
        await asyncio.Event().wait()

    worker.sender.post_message.side_effect = send
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    worker.state = None
    worker.sender.post_message.side_effect = None
    worker.feed.packets[101] = packet(1)
    await worker.run_once()
    worker.sender.post_message.assert_awaited_once()


@pytest.mark.parametrize("routes", [[], {"Jita": "123"}, {"Kino": ""}, {"Kino": "-1"}])
def test_invalid_route_settings(routes):
    with pytest.raises(ValueError):
        PvpSettings(system_channels=routes)


async def test_pvp_worker_lifecycle_is_single_and_closes_sessions(tmp_path):
    repo = Repository(tmp_path / "db")
    settings = Settings(
        "unused",
        ("1",),
        repo.path,
        tmp_path / "backups",
        pvp=PvpSettings(enabled=True, system_channels={"Kino": "2"}),
    )
    client = Wabot(settings, repo)
    client.wait_until_ready = AsyncMock(side_effect=asyncio.Event().wait)
    client.backups.run = AsyncMock()
    client.pvp.close = AsyncMock()
    async with client:
        await client.setup_hook()
        await client.setup_hook()
        assert sum(t.get_name() == "pochven-pvp" for t in client.background) == 1
        await client.close()
        client.pvp.close.assert_awaited_once()


async def test_fresh_kill_is_forwarded_when_local_clock_is_behind(monitor):
    worker, _, _ = monitor
    # Killmail stamped two minutes "in the future" relative to a slow PC clock.
    worker.feed = Feed([packet(1, age=-120)])
    await worker.run_once()
    worker.sender.post_message.assert_awaited_once()


async def test_kills_older_than_an_hour_are_skipped_after_downtime(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22"})
    worker.feed = Feed([packet(1, age=2 * 3600), packet(2, age=30 * 60)])
    await worker.run_once()
    calls = worker.sender.post_message.call_args_list
    assert len(calls) == 1
    assert "https://zkillboard.com/kill/2/" in calls[0].args[1]["content"]


async def test_all_pochven_channel_gets_every_pochven_kill_once(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {"Kino": "22", "Niarja": "44"})
    worker.servers = [("1", replace(worker.servers[0][1], pochven_channel="44"))]
    worker.feed = Feed(
        [
            packet(1),
            packet(2, system=300001),
            packet(3, system=300003),
            packet(4, system=30000142),
        ]
    )
    await worker.run_once()
    sent = [
        (call.args[0], call.args[1]["content"].split("/kill/")[1].rstrip("/"))
        for call in worker.sender.post_message.call_args_list
    ]
    # Kino: own channel and All Pochven; Niarja shares its channel; Vale only All Pochven.
    assert sent == [("22", "1"), ("44", "1"), ("44", "2"), ("44", "3")]


async def test_all_pochven_channel_works_without_system_channels(monitor):
    worker, _, _ = monitor
    await configure_routes(worker, {})
    worker.servers = [("1", replace(worker.servers[0][1], pochven_channel="44"))]
    worker.feed = Feed([packet(1, system=300003)])
    await worker.run_once()
    assert worker.sender.post_message.call_args.args[0] == "44"
    assert "**Vale**" in worker.sender.post_message.call_args.args[1]["content"]


def test_all_pochven_channel_settings():
    settings = PvpSettings(enabled=True, pochven_channel="44", system_channels={"Kino": "44"})
    assert settings.pochven_active and settings.channels_for("Kino") == ["44"]
    assert settings.channels_for("Vale") == ["44"]
    with pytest.raises(ValueError):
        PvpSettings(pochven_channel="abc")
