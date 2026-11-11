import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from tests.test_pvp import NOW, VALE, Feed
from wabot.config import Settings
from wabot.pvp import ALLIANCE_MAX_ATTEMPTS, PvpMonitor, alliance_embed, parse_alliance_kill
from wabot.pvp_config import PvpSettings
from wabot.repository import Repository
from wabot.servers import ServerSettings
from wabot.transport import PermanentDeliveryError

OURS = 99000001
JITA = 30000142


def killmail(kill_id, *, victim_alliance=1, attackers=(OURS,), system=JITA, value=5e8, age=60):
    return {
        "esi": {
            "killmail_id": kill_id,
            "killmail_time": datetime.fromtimestamp(NOW - age, UTC).isoformat(),
            "solar_system_id": system,
            "victim": {
                "character_id": 11,
                "corporation_id": 12,
                "alliance_id": victim_alliance,
                "ship_type_id": 587,
            },
            "attackers": [
                {
                    "character_id": 20 + index,
                    "corporation_id": 30,
                    "alliance_id": alliance,
                    "ship_type_id": 670,
                    "final_blow": index == 0,
                }
                for index, alliance in enumerate(attackers)
            ],
        },
        "zkb": {"totalValue": value, "solo": len(attackers) == 1, "npc": False},
    }


def test_parse_kill_loss_and_unrelated():
    kill = parse_alliance_kill(killmail(1, attackers=(OURS, 5)), {OURS}, NOW)
    assert kill["side"] == "kill" and kill["ours"] == 1 and kill["attackers"] == 2
    assert kill["final_blow"]["character_id"] == 20 and kill["value"] == 5e8
    loss = parse_alliance_kill(killmail(2, victim_alliance=OURS, attackers=(5,)), {OURS}, NOW)
    assert loss["side"] == "loss" and loss["solo"]
    assert parse_alliance_kill(killmail(3, attackers=(5,)), {OURS}, NOW) is None
    # Same clock tolerance as Pochven; only clearly invalid future stamps are dropped.
    assert parse_alliance_kill(killmail(4, age=-120), {OURS}, NOW) is not None
    assert parse_alliance_kill(killmail(5, age=-3 * 86400), {OURS}, NOW) is None


def test_embed_kill_and_loss_layout():
    kill = parse_alliance_kill(killmail(7), {OURS}, NOW)
    names = {("character", 11): "Victim", ("type", 587): "Rifter", ("system", JITA): "Jita"}
    embed = alliance_embed(kill, names).to_dict()
    assert embed["title"] == "🟢 KILL" and embed["color"] == 0x22C55E
    assert embed["url"] == "https://zkillboard.com/kill/7/"
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert "[Victim](https://zkillboard.com/character/11/)" in fields["Victim"]
    assert fields["Ship"] == "Rifter"
    assert fields["Value"] == "500 000 000 ISK"
    assert fields["System"] == "[Jita](https://zkillboard.com/system/30000142/)"
    assert fields["Attackers"] == "1 (ours: 1) · solo"
    assert embed["thumbnail"]["url"] == "https://images.evetech.net/types/587/render?size=128"
    assert embed["footer"]["text"] == "Alliance kill"
    loss = parse_alliance_kill(killmail(8, victim_alliance=OURS, attackers=()), {OURS}, NOW)
    embed = alliance_embed(loss, {}).to_dict()
    assert embed["title"] == "🔴 LOSS" and embed["footer"]["text"] == "Alliance loss"
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Final blow"] == "NPC / no pilot"


@pytest.mark.parametrize(
    "ids, channel, message",
    [
        (["abc"], "5", "Invalid EVE alliance ID: abc"),
        (["1", "1"], "5", "The alliance list contains duplicate IDs."),
        (["1"], None, "Enter a Discord channel ID for alliance kills and losses."),
        ([str(i) for i in range(1, 52)], "5", "at most 50"),
    ],
)
def test_alliance_settings_validation(ids, channel, message):
    with pytest.raises(ValueError, match=message):
        PvpSettings(alliance_ids=ids, alliance_channel=channel)


def test_alliance_only_server_counts_as_active():
    config = PvpSettings(alliance_ids=[str(OURS)], alliance_channel="40")
    assert config.alliance_feed_active
    assert ServerSettings("1", pvp=True, pvp_settings=config).pvp_active
    assert not ServerSettings("1", pvp=False, pvp_settings=config).pvp_active


@pytest.fixture
async def monitor(tmp_path):
    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    config = PvpSettings(
        system_channels={"Kino": "21"},
        alliance_ids=[str(OURS)],
        alliance_channel="40",
    )
    settings = Settings(
        "unused",
        ("1",),
        repo.path,
        tmp_path / "backups",
        servers=(ServerSettings("1", pvp=True, pvp_settings=config),),
    )
    channel = Mock(spec=discord.TextChannel)
    channel.type = discord.ChannelType.text
    channel.guild = SimpleNamespace(id=1, unavailable=False, me=object())
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True, mention_everyone=True
    )
    client = Mock(spec=discord.Client)
    client.is_ready.return_value = True
    client.get_channel.return_value = channel
    names = {"system": "Kino", "character": "Pilot", "type": "Rifter"}
    esi = SimpleNamespace(
        pochven_systems=AsyncMock(return_value={VALE}),
        name=AsyncMock(side_effect=lambda kind, entity: names.get(kind, f"{kind}-{entity}")),
        close=AsyncMock(),
    )
    worker = PvpMonitor(
        client,
        settings,
        repo,
        feed=Feed([]),
        esi=esi,
        clock=lambda: NOW,
        sender=SimpleNamespace(post_message=AsyncMock()),
    )
    yield worker
    await worker.close()
    await repo.close()


async def test_alliance_kill_goes_to_its_channel_once(monitor):
    monitor.feed = Feed([killmail(1), killmail(2, attackers=(5,))])
    await monitor.run_once()
    calls = monitor.sender.post_message.call_args_list
    assert [call.args[0] for call in calls] == ["40"]
    payload = calls[0].args[1]
    assert payload["embeds"][0]["title"] == "🟢 KILL"
    assert payload["content"] == "" and payload["allowed_mentions"]["parse"] == []
    assert monitor.state["killmail_deliveries"]["a:1:40"]["status"] == "sent"
    monitor.state = None  # Restart: the same kill is not sent again.
    monitor.feed.packets[102] = killmail(1)
    await monitor.run_once()
    assert monitor.sender.post_message.await_count == 1


async def test_pochven_kill_of_alliance_reaches_both_channels(monitor):
    monitor.feed = Feed([killmail(1, system=VALE)])
    await monitor.run_once()
    assert sorted(c.args[0] for c in monitor.sender.post_message.call_args_list) == ["21", "40"]


@pytest.mark.parametrize(
    "packet, here",
    [
        (killmail(1, value=1_000_000_000), True),
        (killmail(2, value=999_999_999), False),
        (killmail(3, victim_alliance=OURS, attackers=(5,), value=1_500_000_000), False),
        (killmail(4, victim_alliance=OURS, attackers=(5,), value=2_000_000_000), True),
    ],
)
async def test_here_thresholds_for_kills_and_losses(monitor, packet, here):
    monitor.feed = Feed([packet])
    await monitor.run_once()
    payload = monitor.sender.post_message.call_args.args[1]
    assert (payload["content"] == "@here") is here
    assert ("everyone" in payload["allowed_mentions"]["parse"]) is here


async def test_esi_failure_does_not_block_delivery(monitor):
    async def name(kind, entity):
        if kind == "system":
            return "Kino"
        raise RuntimeError("ESI down")

    monitor.esi.name = name
    monitor.feed = Feed([killmail(1)])
    await monitor.run_once()
    payload = monitor.sender.post_message.call_args.args[1]
    fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
    assert "11" in fields["Victim"]


async def test_refused_alliance_kill_is_dropped_after_retries(monitor):
    monitor.sender.post_message.side_effect = PermanentDeliveryError("missing access")
    monitor.feed = Feed([killmail(1)])
    for _ in range(ALLIANCE_MAX_ATTEMPTS):
        await monitor.run_once()
    assert monitor.state["alliance_kills"] == {}
    count = monitor.sender.post_message.await_count
    await monitor.run_once()
    assert monitor.sender.post_message.await_count == count
    assert ALLIANCE_MAX_ATTEMPTS == count


async def test_alliance_send_cancel_is_not_duplicated(monitor):
    started = asyncio.Event()

    async def send(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monitor.sender.post_message.side_effect = send
    monitor.feed = Feed([killmail(1)])
    task = asyncio.create_task(monitor.run_once())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monitor.state = None
    monitor.sender.post_message.side_effect = None
    monitor.feed.packets[101] = killmail(1)
    await monitor.run_once()
    monitor.sender.post_message.assert_awaited_once()


def test_old_alliance_kill_is_skipped():
    assert parse_alliance_kill(killmail(1, age=2 * 3600), {OURS}, NOW) is None
    assert parse_alliance_kill(killmail(2, age=30 * 60), {OURS}, NOW) is not None
