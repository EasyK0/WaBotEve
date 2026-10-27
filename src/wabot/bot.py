import asyncio
import logging

import discord
from discord import app_commands

from wabot.backup import BackupManager
from wabot.commands import CommandHandlers, register_commands
from wabot.config import Settings
from wabot.market import MarketMonitor
from wabot.market_analysis import AnalysisButton
from wabot.market_commands import MarketCommands, register_market_commands
from wabot.pvp import PvpMonitor
from wabot.queue import DeliveryQueue
from wabot.repository import Repository
from wabot.transport import DiscordReminderSender

log = logging.getLogger(__name__)


class Wabot(discord.Client):
    def __init__(self, settings: Settings, repository: Repository):
        super().__init__(
            intents=discord.Intents(guilds=True), allowed_mentions=discord.AllowedMentions.none()
        )
        self.settings = settings
        self.repository = repository
        self.tree = app_commands.CommandTree(self)
        self.handlers = CommandHandlers(repository, settings)
        servers = settings.server_settings
        register_commands(self.tree, self.handlers, self._guilds("timers"))
        self.market = (
            MarketMonitor(self, settings)
            if any(server.market_active for server in servers)
            else None
        )
        self.sender = DiscordReminderSender(
            self, settings.token, settings.guild_ids, messages=settings.messages
        )
        self.pvp = (
            PvpMonitor(self, settings, repository, sender=self.sender)
            if any(server.pvp_active for server in servers)
            else None
        )
        if self.market is not None:
            self.market_handlers = MarketCommands(self.market, self.handlers)
            register_market_commands(self.tree, self.market_handlers, self._guilds("market"))
        # Each server gets only the commands of its enabled features. Commands are
        # registered remotely only by the explicit `sync` CLI command.
        self.queue = DeliveryQueue(repository, self.sender)
        self.backups = BackupManager(repository, settings.backup_dir, settings.offsite_backup_dir)
        self.background: list[asyncio.Task] = []
        self._closing = False
        self._close_lock = asyncio.Lock()
        self._queue_started = False
        self.background_failed = False
        self.tree.on_error = self.command_error

    def _guilds(self, feature: str) -> list[discord.Object]:
        return [discord.Object(id=int(guild)) for guild in self.settings.feature_guilds(feature)]

    async def setup_hook(self) -> None:
        # setup_hook runs once per login; on_ready may run after every reconnect.
        if self.background or self._closing:
            return
        self.background = [
            asyncio.create_task(self.run_queue(), name="reminder-queue"),
            asyncio.create_task(self.backups.run(), name="daily-backup"),
        ]
        if self.market is not None:
            # Buttons under earlier reports keep working after a restart.
            self.add_dynamic_items(AnalysisButton)
            self.background.append(asyncio.create_task(self.run_market(), name="market-reports"))
        if self.pvp is not None:
            self.background.append(asyncio.create_task(self.run_pvp(), name="pochven-pvp"))
        for task in self.background:
            task.add_done_callback(self.worker_finished)

    async def run_queue(self) -> None:
        await self.wait_until_ready()
        self._queue_started = True
        await self.queue.run()

    async def run_market(self) -> None:
        await self.wait_until_ready()
        await self.market.run()

    async def run_pvp(self) -> None:
        await self.wait_until_ready()
        await self.pvp.run()

    def worker_finished(self, task: asyncio.Task) -> None:
        if self._closing or task.cancelled():
            return
        error = task.exception()
        self.background_failed = True
        log.error(
            "Background worker stopped: %s (%s)",
            task.get_name(),
            type(error).__name__ if error else "unexpected exit",
        )
        asyncio.create_task(self.close())

    async def on_ready(self) -> None:
        log.info(
            "Connected to Discord as bot ID %s; configured guilds=%s",
            self.user.id,
            len(self.settings.guild_ids),
        )
        for guild_id in self.settings.guild_ids:
            if self.get_guild(int(guild_id)) is None:
                log.warning("Bot is not present in configured guild %s", guild_id)
        for server in self.settings.server_settings:
            if server.market and not server.market_active:
                log.warning("Market reports enabled without a channel; guild=%s", server.guild_id)
            if server.pvp and not server.pvp_active:
                log.warning("Pochven PvP enabled without channels; guild=%s", server.guild_id)
        log.info(
            "Features: market=%s pvp=%s",
            "on" if self.market is not None else "off",
            "on" if self.pvp is not None else "off",
        )

    async def on_disconnect(self) -> None:
        log.warning("Discord gateway disconnected; reconnect handled by discord.py")

    async def command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("Command failed; interaction=%s error=%s", interaction.id, type(error).__name__)
        try:
            await self.handlers.private(
                interaction, "The command could not be completed. Please try again."
            )
        except discord.HTTPException:
            log.warning("Could not respond to interaction %s", interaction.id)

    async def close(self) -> None:
        async with self._close_lock:
            if self._closing:
                return
            self._closing = True
            self.queue.stop()
            self.backups.stop()
            if self.pvp is not None:
                self.pvp.stop()
                pvp_tasks = [task for task in self.background if task.get_name() == "pochven-pvp"]
                for task in pvp_tasks:
                    task.cancel()
                await asyncio.gather(*pvp_tasks, return_exceptions=True)
                await self.pvp.close()
            if self.market is not None:
                self.market.stop()
                for task in self.background:
                    if task.get_name() == "market-reports":
                        task.cancel()
                await self.market.close()
            if not self._queue_started:
                for task in self.background:
                    if task.get_name() == "reminder-queue":
                        task.cancel()
            if self.background:
                await asyncio.gather(*self.background, return_exceptions=True)
            await self.sender.close()
            await super().close()
