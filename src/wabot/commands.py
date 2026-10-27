import logging

import discord
from discord import app_commands

from wabot.config import Settings
from wabot.formatting import NO_MENTIONS, rank_message, stats_message, timer_message
from wabot.models import SYSTEMS
from wabot.repository import Repository
from wabot.timekeeping import resolve_payout

log = logging.getLogger(__name__)
CONTEXT_ERROR = "Use this command in a configured server text channel."
PERMISSION_ERROR = (
    "I need Send Messages and Mention Everyone in this channel. No completion was recorded."
)
SAVE_ERROR = "Could not save the timer. No site was recorded."
FEATURE_ERROR = "This feature is turned off for this server in the Wabot launcher."


class CommandHandlers:
    def __init__(self, repository: Repository, settings: Settings):
        self.repository = repository
        self.settings = settings

    async def private(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.send_message(
                message, ephemeral=True, allowed_mentions=NO_MENTIONS
            )

    async def context(
        self, interaction: discord.Interaction, *, timer: bool = False, feature: str = "timers"
    ) -> bool:
        if (
            interaction.guild is None
            or str(interaction.guild_id) not in self.settings.guild_ids
            or not isinstance(interaction.channel, discord.TextChannel)
            or interaction.channel.type != discord.ChannelType.text
        ):
            await self.private(interaction, CONTEXT_ERROR)
            return False
        if not self.settings.feature_enabled(interaction.guild_id, feature):
            await self.private(interaction, FEATURE_ERROR)
            return False
        permissions = interaction.app_permissions
        if (
            not permissions.view_channel
            or not permissions.send_messages
            or (
                timer
                and self.settings.messages.timer_mentions_everyone()
                and not permissions.mention_everyone
            )
        ):
            await self.private(
                interaction,
                PERMISSION_ERROR
                if timer
                else "I need View Channel and Send Messages in this channel.",
            )
            return False
        return True

    async def public_result(self, interaction: discord.Interaction, message: str) -> None:
        # A deferred ephemeral interaction's first followup inherits its privacy.
        # Complete it first, then post a separate public followup. Errors stay private.
        try:
            await interaction.edit_original_response(content=message, allowed_mentions=NO_MENTIONS)
            await interaction.followup.send(message, ephemeral=False, allowed_mentions=NO_MENTIONS)
            await interaction.delete_original_response()
        except discord.HTTPException:
            log.warning(
                "Command result delivery unavailable; interaction=%s. A committed timer remains recorded.",
                interaction.id,
            )

    @app_commands.guild_only()
    @app_commands.describe(
        time="Payout time as HH:MM in EVE Time (UTC)",
        system="System where the previous site was completed",
    )
    @app_commands.choices(
        system=[app_commands.Choice(name=name, value=key) for key, name in SYSTEMS.items()]
    )
    async def timer(self, interaction: discord.Interaction, time: str, system: str) -> None:
        if not await self.context(interaction, timer=True):
            return
        if system not in SYSTEMS:
            await self.private(interaction, "Choose Kino, Niarja or Archee.")
            return
        try:
            payout_at, due_at = resolve_payout(time, interaction.created_at)
        except ValueError as exc:
            await self.private(interaction, str(exc))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            timer, inserted = await self.repository.record_timer(
                interaction_id=str(interaction.id),
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                author_id=str(interaction.user.id),
                author_name=interaction.user.display_name,
                system=system,
                payout_at=payout_at,
                due_at=due_at,
                created_at=int(interaction.created_at.timestamp()),
            )
        except Exception as exc:
            log.error(
                "Timer insert failed; interaction=%s error=%s", interaction.id, type(exc).__name__
            )
            await self.private(interaction, SAVE_ERROR)
            return
        log.info("Timer ID: %s accepted (inserted=%s) guild=%s", timer.id, inserted, timer.guild_id)
        await self.public_result(
            interaction, timer_message(timer, inserted=inserted, messages=self.settings.messages)
        )

    @app_commands.guild_only()
    async def stats(self, interaction: discord.Interaction) -> None:
        if not await self.context(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            counts = await self.repository.stats(str(interaction.guild_id))
        except Exception as exc:
            log.error("Stats query failed: %s", type(exc).__name__)
            await self.private(
                interaction, "Could not load completion statistics. Please try again."
            )
            return
        await self.public_result(interaction, stats_message(counts, self.settings.messages))

    @app_commands.guild_only()
    @app_commands.describe(page="Leaderboard page (10 people per page; default 1)")
    async def rank(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1
    ) -> None:
        if not await self.context(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.repository.leaderboard(str(interaction.guild_id), page)
        except ValueError as exc:
            await self.private(interaction, str(exc))
            return
        except Exception as exc:
            log.error("Leaderboard query failed: %s", type(exc).__name__)
            await self.private(interaction, "Could not load the leaderboard. Please try again.")
            return
        if not result.entries:
            await self.private(interaction, "No sites recorded yet.")
            return
        await self.public_result(
            interaction, rank_message(result, interaction.guild, self.settings.messages)
        )


def register_commands(
    tree: app_commands.CommandTree, handlers: CommandHandlers, guilds=None
) -> None:
    """Global commands by default; `guilds` limits them to servers with timers enabled."""
    if guilds is not None and not guilds:
        return
    for name, description in (
        ("timer", "Record a completed site and remind everyone 23 minutes after its payout."),
        ("stats", "Show reported site completions in this server, for all time."),
        ("rank", "Show the all-time completion reports leaderboard for this server."),
    ):
        tree.add_command(
            app_commands.Command(
                name=name, description=description, callback=getattr(handlers, name)
            ),
            **({} if guilds is None else {"guilds": guilds}),
        )
