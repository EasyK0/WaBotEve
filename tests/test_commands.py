from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from wabot.bot import Wabot
from wabot.commands import CONTEXT_ERROR, PERMISSION_ERROR, SAVE_ERROR, CommandHandlers
from wabot.config import Settings
from wabot.formatting import rank_message
from wabot.models import RankEntry, RankPage
from wabot.queue import DeliveryQueue
from wabot.repository import Repository
from wabot.transport import Delivery, reminder_payload


def interaction(
    *,
    identity=100,
    guild=1,
    system_channel=discord.ChannelType.text,
    mention=True,
    send=True,
    now=None,
    name="Pilot",
):
    result = Mock(spec=discord.Interaction)
    result.id = identity
    result.guild_id = guild
    result.channel_id = 10
    result.guild = Mock(spec=discord.Guild) if guild else None
    if result.guild:
        result.guild.get_member.return_value = None
    result.channel = Mock(spec=discord.TextChannel)
    result.channel.type = system_channel
    result.app_permissions = SimpleNamespace(
        view_channel=True, send_messages=send, mention_everyone=mention
    )
    result.user = SimpleNamespace(id=20, display_name=name)
    result.created_at = now or datetime(2026, 9, 18, 20, 25, tzinfo=UTC)
    result.response = Mock()
    result.response.is_done.return_value = False

    async def defer(**kwargs):
        result.response.is_done.return_value = True

    result.response.defer = AsyncMock(side_effect=defer)
    result.response.send_message = AsyncMock()
    result.edit_original_response = AsyncMock()
    result.delete_original_response = AsyncMock()
    result.followup = SimpleNamespace(send=AsyncMock())
    return result


@pytest_asyncio.fixture
async def setup(tmp_path):
    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    settings = Settings("unused", ("1",), repo.path, tmp_path / "backups")
    try:
        yield repo, settings, CommandHandlers(repo, settings)
    finally:
        await repo.close()


async def test_committed_success_public_with_mentions_disabled(setup):
    repo, _, commands = setup
    call = interaction(name="**Pilot** @everyone <@123>")
    await commands.timer(call, "20:20", "kino")
    row = await repo.get_timer(1)
    assert row.created_at == int(call.created_at.timestamp())
    assert row.due_at - row.payout_at == 1380
    assert (await repo.stats("1"))["total"] == 1
    call.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    message = call.followup.send.call_args.args[0]
    assert "2026-09-18 20:43 EVE Time (UTC)" in message
    assert "Completed sites: +1" in message and "Timer ID: 1" in message
    assert "@everyone" not in message and "<@123>" not in message
    assert "\\*\\*Pilot\\*\\*" in message
    assert call.followup.send.call_args.kwargs["ephemeral"] is False
    assert call.followup.send.call_args.kwargs["allowed_mentions"].to_dict()["parse"] == []


async def test_timer_without_ping_does_not_require_mention_permission(setup):
    from dataclasses import replace

    from wabot.messages import MessageSettings

    repo, settings, commands = setup
    commands.settings = replace(
        settings,
        messages=MessageSettings({"timer_reminder": "Check the systems! Timer {timer_id}"}),
    )
    call = interaction(mention=False)
    await commands.timer(call, "20:20", "kino")
    assert (await repo.stats("1"))["total"] == 1
    call.followup.send.assert_awaited_once()


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"guild": None}, CONTEXT_ERROR),
        ({"guild": 2}, CONTEXT_ERROR),
        ({"system_channel": discord.ChannelType.public_thread}, CONTEXT_ERROR),
        ({"system_channel": discord.ChannelType.forum}, CONTEXT_ERROR),
        ({"system_channel": discord.ChannelType.news}, CONTEXT_ERROR),
        ({"mention": False}, PERMISSION_ERROR),
        ({"send": False}, PERMISSION_ERROR),
    ],
)
async def test_context_and_permission_rejections_save_nothing(setup, overrides, error):
    repo, _, commands = setup
    call = interaction(**overrides)
    await commands.timer(call, "20:20", "kino")
    assert (await repo.stats("1"))["total"] == 0
    call.response.send_message.assert_awaited_once()
    assert call.response.send_message.call_args.args[0] == error
    assert call.response.send_message.call_args.kwargs["ephemeral"] is True
    call.followup.send.assert_not_called()


@pytest.mark.parametrize(
    "value,system,error",
    [
        ("9:05", "kino", "Invalid payout time. Use HH:MM in EVE Time (UTC)."),
        ("20:00", "kino", "The respawn time has already passed. No completion was recorded."),
        ("20:20", "jita", "Choose Kino, Niarja or Archee."),
    ],
)
async def test_bad_arguments_do_not_record(setup, value, system, error):
    repo, _, commands = setup
    call = interaction()
    await commands.timer(call, value, system)
    assert call.response.send_message.call_args.args[0] == error
    assert (await repo.stats("1"))["total"] == 0


async def test_failed_save_only_reports_private_failure(setup):
    repo, _, commands = setup
    commands.repository = SimpleNamespace(record_timer=AsyncMock(side_effect=OSError("secret")))
    call = interaction()
    await commands.timer(call, "20:20", "kino")
    assert call.edit_original_response.call_args.kwargs["content"] == SAVE_ERROR
    call.followup.send.assert_not_called()
    assert (await repo.stats("1"))["total"] == 0


async def test_duplicate_and_identical_new_interaction(setup):
    repo, _, commands = setup
    await commands.timer(interaction(), "20:20", "kino")
    duplicate = interaction()
    await commands.timer(duplicate, "20:20", "kino")
    assert "Completed sites: +0" in duplicate.followup.send.call_args.args[0]
    await commands.timer(interaction(identity=101), "20:20", "kino")
    assert (await repo.stats("1"))["total"] == 2


async def test_empty_stats_and_private_empty_ranking(setup):
    _, _, commands = setup
    stats = interaction()
    await commands.stats(stats)
    message = stats.followup.send.call_args.args[0]
    for text in ["Kino: 0", "Niarja: 0", "Archee: 0", "Total: 0"]:
        assert text in message
    rank = interaction()
    await commands.rank(rank)
    assert rank.edit_original_response.call_args.kwargs["content"] == "No sites recorded yet."
    rank.followup.send.assert_not_called()


async def test_invalid_ranking_page_private(setup):
    _, _, commands = setup
    await commands.timer(interaction(), "20:20", "kino")
    call = interaction()
    await commands.rank(call, 2)
    assert (
        call.edit_original_response.call_args.kwargs["content"]
        == "Invalid page. Choose a page between 1 and 1."
    )
    call.followup.send.assert_not_called()


def test_leaderboard_name_fallbacks():
    guild = Mock()
    guild.get_member.side_effect = [SimpleNamespace(display_name="New **name**"), None, None]
    page = RankPage(
        [RankEntry("1", "Old", 3, 1), RankEntry("2", "Saved", 2, 2), RankEntry("3", "", 1, 3)],
        1,
        1,
        3,
    )
    text = rank_message(page, guild)
    assert "New \\*\\*name\\*\\*" in text and "Saved" in text and "User 3" in text
    assert "Old" not in text


async def test_complete_payout_to_respawn_to_archee_cycle_with_restart(setup):
    repo, settings, commands = setup
    await commands.timer(interaction(), "20:20", "kino")
    now = [int(datetime(2026, 9, 18, 20, 42, 59, tzinfo=UTC).timestamp())]
    payloads = []

    class Sender:
        async def send(self, timer, *, delayed):
            payloads.append(reminder_payload(timer, delayed=delayed, mention_everyone=True))
            return Delivery(str(1000 + len(payloads)))

    await repo.close()
    await repo.initialize()
    worker = DeliveryQueue(repo, Sender(), clock=lambda: now[0])
    assert not await worker.run_once()
    now[0] += 1
    assert await worker.run_once()
    assert (
        "@everyone" in payloads[0]["content"]
        and "Kino, Niarja and Archee" in payloads[0]["content"]
    )
    assert (await repo.stats("1"))["total"] == 1
    await commands.timer(
        interaction(identity=101, now=datetime(2026, 9, 18, 20, 50, tzinfo=UTC)), "20:50", "archee"
    )
    assert await repo.stats("1") == {"kino": 1, "niarja": 0, "archee": 1, "total": 2}
    now[0] = int(datetime(2026, 9, 18, 21, 13, tzinfo=UTC).timestamp())
    assert await worker.run_once()
    assert len(payloads) == 2
    assert not await worker.run_once()
    assert (await repo.leaderboard("1")).entries[0].count == 2


async def test_command_schema_and_no_privileged_intents(setup):
    repo, settings, _ = setup
    bot = Wabot(settings, repo)
    try:
        guild = discord.Object(id=1)
        commands = {
            command.name: command.to_dict(bot.tree)
            for command in bot.tree.get_commands(guild=guild)
        }
        assert set(commands) == {"timer", "stats", "rank"}
        options = commands["timer"]["options"]
        assert all(option["required"] for option in options)
        assert options[1]["choices"] == [
            {"name": "Kino", "value": "kino"},
            {"name": "Niarja", "value": "niarja"},
            {"name": "Archee", "value": "archee"},
        ]
        assert not bot.intents.members and not bot.intents.message_content
        assert bot.intents.guilds
    finally:
        await bot.close()
