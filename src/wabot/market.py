"""Independent market worker, whole-item cache and Discord delivery."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import discord

from wabot.config import Settings
from wabot.market_analysis import PriceAnalysis, analysis_view, analyze
from wabot.market_config import MarketItem
from wabot.market_esi import ESIClient, FetchedOrders, HistoryDay
from wabot.market_report import (
    ItemSnapshot,
    analyze_orders,
    build_report,
    group_orders,
    snapshot_state,
)

log = logging.getLogger(__name__)
# Analysis on demand reuses report orders this fresh instead of asking ESI again.
ANALYSIS_ORDER_AGE = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class MarketResult:
    sent: bool
    message: str


class MarketChannelError(ValueError):
    """A safe explanation of unavailable report delivery."""


class MarketMonitor:
    def __init__(
        self,
        client: discord.Client,
        settings: Settings,
        *,
        esi: ESIClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.settings = settings
        self.config = settings.market
        self.esi = esi or ESIClient(timeout_seconds=self.config.request_timeout_seconds)
        self.clock = clock
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._running = False
        self._cycle_task: asyncio.Task | None = None
        self._validated: set[int] = set()
        self._cache: dict[int, FetchedOrders] = {}
        self.last_successful_read: datetime | None = None
        self.last_report_at: datetime | None = None
        self.next_report_at: datetime | None = None
        self.last_state = "Not read yet"
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        self.stop()
        task = self._cycle_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.esi.close()

    async def run(self) -> None:
        # setup_hook owns this worker; repeated ready events never create one.
        if self._running:
            return
        self._running = True
        loop = asyncio.get_running_loop()
        interval = self.config.interval_minutes * 60
        deadline = loop.time()
        self.next_report_at = self.clock()
        try:
            while not self._stop.is_set():
                await self.run_once()
                deadline += interval
                now = loop.time()
                if deadline <= now:
                    deadline += (int((now - deadline) // interval) + 1) * interval
                delay = max(0, deadline - loop.time())
                self.next_report_at = self.clock() + timedelta(seconds=delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            self.next_report_at = None
            self._running = False

    def targets(self, guild_id: int | None = None) -> list[tuple[str | None, str]]:
        """(server, channel) pairs receiving reports; server None is a migrated legacy channel."""
        result = []
        for server in self.settings.server_settings:
            if not server.market_active:
                continue
            if guild_id is not None and server.guild_id not in (None, str(guild_id)):
                continue
            result.append((server.guild_id, server.market_channel_id))
        return result

    async def report_channel(self, guild_id: int | None = None) -> discord.TextChannel:
        targets = self.targets(guild_id)
        if not targets:
            raise MarketChannelError(
                "Market reports are disabled. Configure the market channel first."
            )
        return await self.channel(*targets[0], guild_id=guild_id)

    async def channel(
        self, server_id: str | None, channel_id: str, *, guild_id: int | None = None
    ) -> discord.TextChannel:
        if not self.client.is_ready():
            raise MarketChannelError("Discord is reconnecting. Try again after the bot is ready.")
        channel = self.client.get_channel(int(channel_id))
        if channel is None:
            channel = await self.client.fetch_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel) or channel.type != discord.ChannelType.text:
            raise MarketChannelError("The market channel must be a normal server text channel.")
        if str(channel.guild.id) not in self.settings.guild_ids or (
            server_id is not None and str(channel.guild.id) != server_id
        ):
            raise MarketChannelError("The market channel is outside the configured servers.")
        if guild_id is not None and channel.guild.id != guild_id:
            raise MarketChannelError(
                "Use this command in the server containing the market channel."
            )
        if channel.guild.unavailable or channel.guild.me is None:
            raise MarketChannelError("The market server is temporarily unavailable.")
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise MarketChannelError(
                "I need View Channel, Send Messages and Embed Links in the market channel."
            )
        return channel

    async def _snapshots(self) -> list[ItemSnapshot]:
        items = tuple(item for item in self.config.items if item.enabled)
        pending = tuple(item for item in items if item.type_id not in self._validated)
        validation_errors = {}
        if pending:
            validation_errors = await self.esi.validate_items(pending)
            self._validated.update(
                item.type_id for item in pending if item.type_id not in validation_errors
            )
        snapshots = []
        all_successful = True
        for item in items:
            if item.type_id in validation_errors:
                log.warning(
                    "Market type validation unavailable or mismatched; type_id=%s", item.type_id
                )
                snapshots.append(ItemSnapshot(item, [], None, "Error"))
                all_successful = False
                continue
            try:
                fetched = await self.esi.fetch_orders(self.config.region_id, item.type_id)
                self._cache[item.type_id] = fetched
                snapshots.append(
                    ItemSnapshot(
                        item,
                        fetched.orders,
                        fetched.fetched_at,
                        "Cached" if fetched.cached else "Live",
                    )
                )
            except Exception as exc:
                # Per-item failures cannot discard the other five items.
                all_successful = False
                log.warning(
                    "Market data unavailable; type_id=%s error=%s", item.type_id, type(exc).__name__
                )
                cached = self._cache.get(item.type_id)
                if cached is None:
                    snapshots.append(ItemSnapshot(item, [], None, "Error"))
                else:
                    state = (
                        "Cached"
                        if self.clock() - cached.fetched_at <= timedelta(minutes=30)
                        else "Error"
                    )
                    snapshots.append(ItemSnapshot(item, cached.orders, cached.fetched_at, state))
        snapshots = self._refresh_state(snapshots, self.clock())
        if all_successful and snapshots and all(s.state != "Error" for s in snapshots):
            self.last_successful_read = min(
                s.fetched_at for s in snapshots if s.fetched_at is not None
            )
        return snapshots

    def _refresh_state(self, snapshots: list[ItemSnapshot], now: datetime) -> list[ItemSnapshot]:
        # Data can also expire while station names are being resolved.
        snapshots = [
            ItemSnapshot(s.item, s.orders, s.fetched_at, "Error")
            if s.fetched_at is not None and now - s.fetched_at > timedelta(minutes=30)
            else s
            for s in snapshots
        ]
        self.last_state = (
            "Error"
            if any(s.state == "Error" for s in snapshots)
            else "Cached"
            if any(s.state == "Cached" for s in snapshots)
            else "Live"
        )
        return snapshots

    def find_item(self, text: str) -> MarketItem | None:
        text = " ".join(str(text).split()).casefold()
        for item in self.config.enabled_items:
            if text in (str(item.type_id), item.display_name.casefold()):
                return item
        return None

    def _analyze(self, history: list[HistoryDay], orders: list[dict]) -> PriceAnalysis | None:
        groups = group_orders(orders, self.config)
        quotes = analyze_orders([order for local in groups.values() for order in local])
        return analyze(
            history,
            quotes.best_buy,
            quotes.best_sell,
            today=self.clock().date(),
            sales_tax=self.config.sales_tax_percent,
            broker_fee=self.config.broker_fee_percent,
        )

    async def analysis(self, item: MarketItem) -> PriceAnalysis | None:
        """Raises ESIError when history or current orders cannot be read."""
        cached = self._cache.get(item.type_id)
        if cached is None or self.clock() - cached.fetched_at > ANALYSIS_ORDER_AGE:
            cached = await self.esi.fetch_orders(self.config.region_id, item.type_id)
            self._cache[item.type_id] = cached
        history = await self.esi.fetch_history(self.config.region_id, item.type_id)
        return self._analyze(history, cached.orders)

    async def _report_analyses(self, snapshots: list[ItemSnapshot]) -> dict[int, PriceAnalysis]:
        result = {}
        for snapshot in snapshots:
            if snapshot_state(snapshot, self.clock()) == "Error":
                continue
            try:
                history = await self.esi.fetch_history(self.config.region_id, snapshot.item.type_id)
                analysis = self._analyze(history, snapshot.orders)
            except Exception as exc:
                # The verdict line is optional; the report itself never waits on it.
                log.warning(
                    "Market history unavailable; type_id=%s error=%s",
                    snapshot.item.type_id,
                    type(exc).__name__,
                )
                continue
            if analysis is not None:
                result[snapshot.item.type_id] = analysis
        return result

    async def run_once(self, *, guild_id: int | None = None) -> MarketResult:
        if self._stop.is_set():
            return MarketResult(False, "Market monitoring is stopping.")
        # No await between testing and acquiring an unlocked asyncio.Lock.
        if self._lock.locked():
            return MarketResult(False, "A market report is already in progress.")
        async with self._lock:
            self._cycle_task = asyncio.current_task()
            started = time.monotonic()
            try:
                targets = self.targets(guild_id)
                if not targets:
                    raise MarketChannelError(
                        "Market reports are disabled. Configure the market channel first."
                    )
                # Reject unusable channels before reading ESI; one bad server does not
                # stop reports for the others.
                ready = []
                first_error = None
                for target in targets:
                    try:
                        await self.channel(*target, guild_id=guild_id)
                    except MarketChannelError as exc:
                        first_error = first_error or exc
                        log.warning("Market channel rejected; channel=%s", target[1])
                    else:
                        ready.append(target)
                if not ready:
                    raise first_error
                snapshots = await self._snapshots()
                analyses = await self._report_analyses(snapshots)
                locations = {self.config.jita_location_id}
                for snapshot in snapshots:
                    locations.update(group_orders(snapshot.orders, self.config))
                names = {}
                for location_id in sorted(locations):
                    try:
                        names[location_id] = await self.esi.location_name(location_id)
                    except Exception as exc:
                        log.warning(
                            "Market location name unavailable; location_id=%s error=%s",
                            location_id,
                            type(exc).__name__,
                        )
                        names[location_id] = (
                            f"Structure {location_id}"
                            if location_id >= 10**12
                            else f"Station {location_id}"
                        )
                now = self.clock()
                snapshots = self._refresh_state(snapshots, now)
                batches = build_report(
                    self.config, snapshots, names, now, self.settings.messages, analyses
                )
                sent = []
                for target in ready:
                    # Permissions may change while fetching a paginated ESI snapshot.
                    try:
                        channel = await self.channel(*target, guild_id=guild_id)
                    except MarketChannelError as exc:
                        first_error = first_error or exc
                        log.warning("Market channel rejected; channel=%s", target[1])
                        continue
                    for number, embeds in enumerate(batches, start=1):
                        if self._stop.is_set():
                            return MarketResult(False, "Market monitoring is stopping.")
                        # Analysis buttons go under the last message of the report.
                        view = (
                            analysis_view(self.config.enabled_items)
                            if number == len(batches)
                            else None
                        )
                        await channel.send(
                            embeds=embeds,
                            allowed_mentions=discord.AllowedMentions.none(),
                            **({"view": view} if view is not None else {}),
                        )
                    sent.append(f"<#{channel.id}>")
                if not sent:
                    raise first_error
                self.last_report_at = now
                self.last_error = None
                return MarketResult(
                    True, f"Market report sent to {', '.join(sent)} ({self.last_state})."
                )
            except MarketChannelError as exc:
                self.last_error = str(exc)
                log.warning("Market cycle rejected (%s)", type(exc).__name__)
                return MarketResult(False, self.last_error)
            except Exception as exc:
                self.last_error = "The market report could not be completed. Check channel permissions and the bot log."
                log.warning("Market cycle failed (%s)", type(exc).__name__)
                return MarketResult(False, self.last_error)
            finally:
                log.info(
                    "Market cycle completed; elapsed_seconds=%.3f state=%s",
                    time.monotonic() - started,
                    self.last_state,
                )
                self._cycle_task = None

    def status_message(self, guild_id: int | None = None) -> str:
        def stamp(value: datetime | None) -> str:
            return value.strftime("%Y-%m-%d %H:%M:%S EVE Time (UTC)") if value else "Never"

        state = self.last_state
        if self.last_successful_read and self.clock() - self.last_successful_read > timedelta(
            minutes=30
        ):
            state = "DATA ERROR (last complete read is over 30 minutes old)"
        lines = [
            "Jita + Perimeter market monitoring",
            "Channel: " + (", ".join(f"<#{c}>" for _, c in self.targets(guild_id)) or "none"),
            f"Data: {state}",
            f"Last complete successful read: {stamp(self.last_successful_read)}",
            f"Last report: {stamp(self.last_report_at)}",
            f"Next scheduled report: {stamp(self.next_report_at)}",
        ]
        if self.last_error:
            lines.append(self.last_error)
        return "\n".join(lines)
