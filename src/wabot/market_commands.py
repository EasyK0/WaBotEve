"""Market commands: administrative reports and the read-only analysis for everyone."""

import logging

import discord
from discord import app_commands

from wabot.commands import CommandHandlers
from wabot.market import MarketChannelError, MarketMonitor
from wabot.market_analysis import analysis_embed, overview_embed

log = logging.getLogger(__name__)
UNAVAILABLE = "Market data is unavailable right now. Try again in a few minutes."
UNKNOWN_ITEM = "Unknown item. Choose one of the watched items from the list."


class MarketCommands:
    def __init__(self, monitor: MarketMonitor, handlers: CommandHandlers):
        self.monitor = monitor
        self.handlers = handlers

    async def context(self, interaction: discord.Interaction) -> bool:
        if not await self.handlers.context(interaction, feature="market"):
            return False
        if not interaction.permissions.administrator:
            await self.handlers.private(
                interaction, "Only server administrators can use market commands."
            )
            return False
        return True

    async def analysis_embed(self, text: str | None) -> discord.Embed | str:
        """An embed, or a safe message explaining why there is none."""
        try:
            if text is None or not text.strip():
                results = [
                    (item.display_name, await self.monitor.analysis(item))
                    for item in self.monitor.config.enabled_items
                ]
                return overview_embed(results)
            item = self.monitor.find_item(text)
            if item is None:
                return UNKNOWN_ITEM
            return analysis_embed(item.display_name, await self.monitor.analysis(item))
        except Exception as exc:
            log.warning("Market analysis unavailable (%s)", type(exc).__name__)
            return UNAVAILABLE

    async def _send_analysis(self, interaction: discord.Interaction, text: str | None) -> None:
        result = await self.analysis_embed(text)
        if isinstance(result, str):
            await interaction.edit_original_response(content=result)
        else:
            await interaction.edit_original_response(
                embed=result, allowed_mentions=discord.AllowedMentions.none()
            )

    @app_commands.guild_only()
    @app_commands.describe(item="Watched item; leave empty for a summary of all items")
    async def market_analysis(
        self, interaction: discord.Interaction, item: str | None = None
    ) -> None:
        # Read-only, so every member may use it; the answer is posted in the channel.
        if not await self.handlers.context(interaction, feature="market"):
            return
        await interaction.response.defer(thinking=True)
        await self._send_analysis(interaction, item)

    async def item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.casefold().strip()
        return [
            app_commands.Choice(name=item.display_name[:100], value=str(item.type_id))
            for item in self.monitor.config.enabled_items
            if current in item.display_name.casefold()
        ][:25]

    async def analysis_button(self, interaction: discord.Interaction, type_id: int) -> None:
        """A report button: the analysis is shown only to the member who clicked."""
        if not await self.handlers.context(interaction, feature="market"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._send_analysis(interaction, str(type_id))

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def market_now(self, interaction: discord.Interaction) -> None:
        if not await self.context(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.monitor.run_once(guild_id=interaction.guild_id)
        await self.handlers.private(interaction, result.message)

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def market_status(self, interaction: discord.Interaction) -> None:
        if not await self.context(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.monitor.report_channel(interaction.guild_id)
        except MarketChannelError as exc:
            await self.handlers.private(interaction, str(exc))
            return
        except discord.HTTPException:
            await self.handlers.private(
                interaction, "The market channel is missing or inaccessible."
            )
            return
        await self.handlers.private(interaction, self.monitor.status_message(interaction.guild_id))


def register_market_commands(
    tree: app_commands.CommandTree, handlers: MarketCommands, guilds=None
) -> None:
    """Global commands by default; `guilds` limits them to servers with market reports."""
    if guilds is not None and not guilds:
        return
    for name, description, callback in (
        (
            "market-now",
            "Publish the combined Jita and Perimeter market report now.",
            handlers.market_now,
        ),
        (
            "market-status",
            "Show market data freshness and the next scheduled report.",
            handlers.market_status,
        ),
    ):
        tree.add_command(
            app_commands.Command(name=name, description=description, callback=callback),
            **({} if guilds is None else {"guilds": guilds}),
        )
    analysis = app_commands.Command(
        name="market-analysis",
        description="Price trend and whether now is a good time to sell a watched item.",
        callback=handlers.market_analysis,
    )
    analysis.autocomplete("item")(handlers.item_autocomplete)
    tree.add_command(analysis, **({} if guilds is None else {"guilds": guilds}))
