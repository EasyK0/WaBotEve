"""Individual killmail forwarding every 30 seconds with durable deduplication."""

import asyncio
import logging
import math
from dataclasses import replace
from datetime import UTC, datetime

import discord

from wabot.pvp_feed import PvpESI, ZkillFeed
from wabot.transport import DiscordReminderSender, PermanentDeliveryError, TransientDeliveryError

log = logging.getLogger(__name__)
POLL_SECONDS = 30
FEED_BATCH_SECONDS = 20
FEED_BATCH_RECORDS = 450
GAP_CHECK_SECONDS = 60
# Killmail times come from the EVE server, but `now` is the local PC clock. A slow
# Windows clock must not discard fresh kills; only reject clearly invalid timestamps.
FUTURE_TOLERANCE_SECONDS = 24 * 60 * 60
# After downtime the feed cursor replays every missed killmail; older ones are skipped
# so a restart does not flood Discord with stale kills.
MAX_KILL_AGE_SECONDS = 60 * 60
# An alliance killmail Discord keeps refusing is dropped after this many attempts.
ALLIANCE_MAX_ATTEMPTS = 5


def parse_kill(packet, systems, now):
    if not isinstance(packet, dict):
        raise ValueError("Invalid killmail envelope")
    data = packet.get("esi", packet)
    if not isinstance(data, dict):
        raise ValueError("Invalid killmail body")
    system = data["solar_system_id"]
    kill_id = data["killmail_id"]
    if any(type(value) is not int or value <= 0 for value in (system, kill_id)):
        raise ValueError("Invalid killmail ID or system")
    if system not in systems:
        return None
    stamp = datetime.fromisoformat(data["killmail_time"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Killmail timestamp must include a timezone")
    when = stamp.timestamp()
    if when > now + FUTURE_TOLERANCE_SECONDS or when < now - MAX_KILL_AGE_SECONDS:
        return None
    return {"id": kill_id, "system": system, "time": when}


def _party(entry):
    return {
        name: entry.get(name)
        for name in ("character_id", "corporation_id", "alliance_id", "ship_type_id")
    }


def parse_alliance_kill(packet, alliances, now):
    """Kill or loss of a listed alliance anywhere in EVE; None for other killmails."""
    if not isinstance(packet, dict):
        raise ValueError("Invalid killmail envelope")
    data = packet.get("esi", packet)
    if not isinstance(data, dict):
        raise ValueError("Invalid killmail body")
    kill_id = data["killmail_id"]
    system = data["solar_system_id"]
    if any(type(value) is not int or value <= 0 for value in (system, kill_id)):
        raise ValueError("Invalid killmail ID or system")
    victim = data["victim"]
    attackers = data.get("attackers") or []
    if victim.get("alliance_id") in alliances:
        side = "loss"
    elif any(attacker.get("alliance_id") in alliances for attacker in attackers):
        side = "kill"
    else:
        return None
    stamp = datetime.fromisoformat(data["killmail_time"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Killmail timestamp must include a timezone")
    when = stamp.timestamp()
    if when > now + FUTURE_TOLERANCE_SECONDS or when < now - MAX_KILL_AGE_SECONDS:
        return None
    zkb = packet.get("zkb") or {}
    final = next((a for a in attackers if a.get("final_blow")), attackers[0] if attackers else {})
    value = zkb.get("totalValue") or 0
    return {
        "id": kill_id,
        "system": system,
        "time": when,
        "side": side,
        "victim": _party(victim),
        "final_blow": _party(final),
        "attackers": len(attackers),
        "ours": sum(attacker.get("alliance_id") in alliances for attacker in attackers),
        "value": float(value) if isinstance(value, (int, float)) else 0.0,
        "solo": bool(zkb.get("solo")),
        "npc": bool(zkb.get("npc")),
    }


def _isk(value):
    return f"{value:,.0f}".replace(",", " ") + " ISK"


def alliance_embed(kill, names):
    """Discord embed for an alliance kill or loss; `names` maps (kind, id) to a name."""

    def name(kind, entity):
        return names.get((kind, entity)) or str(entity)

    def pilot(party):
        if not party.get("character_id"):
            return "NPC / no pilot"
        character = party["character_id"]
        lines = [f"[{name('character', character)}](https://zkillboard.com/character/{character}/)"]
        if party.get("corporation_id"):
            lines.append(name("corporation", party["corporation_id"]))
        if party.get("alliance_id"):
            lines.append(name("alliance", party["alliance_id"]))
        return "\n".join(lines)

    loss = kill["side"] == "loss"
    victim = kill["victim"]
    embed = discord.Embed(
        title="🔴 LOSS" if loss else "🟢 KILL",
        url=f"https://zkillboard.com/kill/{kill['id']}/",
        color=0xDC2626 if loss else 0x22C55E,
        timestamp=datetime.fromtimestamp(kill["time"], UTC),
    )
    embed.add_field(name="Victim", value=pilot(victim), inline=True)
    ship = victim.get("ship_type_id")
    embed.add_field(name="Ship", value=name("type", ship) if ship else "?", inline=True)
    embed.add_field(name="Value", value=_isk(kill["value"]), inline=True)
    embed.add_field(name="Final blow", value=pilot(kill["final_blow"]), inline=True)
    embed.add_field(
        name="System",
        value=f"[{name('system', kill['system'])}](https://zkillboard.com/system/{kill['system']}/)",
        inline=True,
    )
    embed.add_field(
        name="Attackers",
        value=f"{kill['attackers']} (ours: {kill['ours']})" + (" · solo" if kill["solo"] else ""),
        inline=True,
    )
    if ship:
        embed.set_thumbnail(url=f"https://images.evetech.net/types/{ship}/render?size=128")
    embed.set_footer(text="Alliance loss" if loss else "Alliance kill")
    return embed


class PvpMonitor:
    def __init__(
        self, client, settings, repository, *, feed=None, esi=None, clock=None, sender=None
    ):
        self.client = client
        self.settings = settings
        # (server ID, that server's settings) for every server with Pochven PvP on.
        self.servers = [
            (server.guild_id, server.pvp_settings)
            for server in settings.server_settings
            if server.pvp_active
        ]
        self.alliance_servers = [
            (guild, config) for guild, config in self.servers if config.alliance_feed_active
        ]
        self._names = {}
        self.repository = repository
        self.feed = feed or ZkillFeed()
        self.esi = esi or PvpESI()
        self.clock = clock or (lambda: datetime.now(UTC).timestamp())
        self.state = None
        self.systems = None
        self.routes = {}
        self._stop = asyncio.Event()
        self.sender = sender or DiscordReminderSender(client, settings.token, settings.guild_ids)
        self._owns_sender = sender is None
        self._lock = asyncio.Lock()
        self._next_gap_check = 0.0

    def stop(self):
        self._stop.set()

    async def close(self):
        self.stop()
        await self.feed.close()
        await self.esi.close()
        if self._owns_sender:
            await self.sender.close()

    async def run(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                log.warning("Pochven monitor cycle failed (%s)", type(exc).__name__)
            interval = POLL_SECONDS
            # Anchor scans to a fixed cadence, rather than adding 30s after each scan.
            # Slow network operations may skip a slot, but never start overlapping scans.
            deadline += interval
            now = loop.time()
            if deadline < now:
                deadline += math.ceil((now - deadline) / interval) * interval
            try:
                await asyncio.wait_for(self._stop.wait(), max(0, deadline - now))
            except TimeoutError:
                pass

    async def alert_channel(self, channel_id, config=None):
        config = config or self.servers[0][1]
        if not self.client.is_ready():
            raise ValueError("Discord is reconnecting")
        channel = self.client.get_channel(int(channel_id))
        if channel is None:
            channel = await self.client.fetch_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel) or channel.type != discord.ChannelType.text:
            raise ValueError("PvP alerts require a normal server text channel")
        if (
            str(channel.guild.id) not in self.settings.guild_ids
            or channel.guild.unavailable
            or channel.guild.me is None
        ):
            raise ValueError("PvP channel server is unavailable or not configured")
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise ValueError("PvP channel requires View Channel, Send Messages and Embed Links")
        if config.ping == "everyone" and not permissions.mention_everyone:
            raise ValueError("PvP everyone ping requires Mention Everyone permission")
        if config.ping == "role":
            role = channel.guild.get_role(int(config.role_id))
            if (
                role is None
                or role.is_default()
                or not (role.mentionable or permissions.mention_everyone)
            ):
                raise ValueError("PvP role is missing or cannot be mentioned")
        return channel

    async def _save(self):
        await self.repository.save_pvp_state(self.state)

    async def run_once(self):
        async with self._lock:
            if not self._stop.is_set():
                await self._scan()

    async def _scan(self):
        if self.state is None:
            self.state = await self.repository.load_pvp_state()
        # Each system maps to every server channel that wants its killmails.
        wanted = {name for _, config in self.servers for name in config.system_channels}
        everything = any(config.pochven_channel for _, config in self.servers)
        if (wanted or everything) and self.systems is None:
            self.systems = await self.esi.pochven_systems()
        if (wanted or everything) and not self.routes:
            routes = {}
            found = set()
            for system in self.systems:
                name = await self.esi.name("system", system)
                found.add(name)
                targets = [
                    (channel, config)
                    for _, config in self.servers
                    for channel in config.channels_for(name)
                ]
                if targets:
                    routes[system] = (name, targets)
                if not everything and wanted <= found:
                    break
            if not wanted <= found:
                raise ValueError("Could not resolve all configured killmail systems")
            self.routes = routes
        if not self.state.get("sequence"):
            self.state["sequence"] = await self.feed.latest_sequence()
        # Upgrade persisted state without replaying killmails from old fight alerts.
        delivered = self.state.setdefault("killmail_deliveries", {})
        for alert in self.state.pop("alerts", {}).values():
            for kill_id in alert.get("ids", []):
                delivered.setdefault(str(kill_id), {"status": "sent"})
        self.state["kills"] = kills = {
            key: {name: kill[name] for name in ("id", "system", "time")}
            for key, kill in self.state.get("kills", {}).items()
            if kill["system"] in self.routes
        }
        read = 0
        attempted = set(kills)
        # Retry pending messages before a slow or unavailable feed read.
        await self._forward_killmails()
        await self._forward_alliance()
        loop = asyncio.get_running_loop()
        batch_deadline = loop.time() + FEED_BATCH_SECONDS
        try:
            # Check the budget BETWEEN records: cancelling an in-flight Discord POST
            # at the batch boundary would unnecessarily make its delivery uncertain.
            for _ in range(FEED_BATCH_RECORDS):
                if self._stop.is_set():
                    return
                if loop.time() >= batch_deadline:
                    break
                packet = await self.feed.packet(self.state["sequence"])
                if packet is None:
                    # Normal live-edge 404: retry this sequence on the next scheduled scan.
                    # A pointer lookup on every 404 would add another six-second wait.
                    if read:
                        self._next_gap_check = loop.time() + GAP_CHECK_SECONDS
                    elif loop.time() >= self._next_gap_check:
                        self._next_gap_check = loop.time() + GAP_CHECK_SECONDS
                        latest = await self.feed.latest_sequence()
                        if latest > self.state["sequence"]:
                            log.warning("PvP feed gap; resuming at current sequence")
                            self.state["sequence"] = latest
                    break
                try:
                    kill = parse_kill(packet, self.routes, self.clock())
                except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                    log.warning(
                        "Skipping malformed PvP feed record; sequence=%s",
                        self.state["sequence"],
                    )
                    kill = None
                if kill is not None:
                    kills[str(kill["id"])] = kill
                alliance_keys = self._queue_alliance(packet)
                self.state["sequence"] += 1
                self.state["updated_at"] = self.clock()
                read += 1
                if kill is not None:
                    key = str(kill["id"])
                    if key not in attempted:
                        attempted.add(key)
                        # Save cursor and delivery marker, then POST immediately.
                        await self._forward_killmails(keys=(key,))
                if alliance_keys:
                    await self._forward_alliance(keys=alliance_keys)
        except Exception as exc:
            # Preserve successfully processed packets and still evaluate available kills.
            log.warning("PvP feed read interrupted (%s)", type(exc).__name__)
        finally:
            await self._save()

    async def _forward_killmails(self, *, keys=None):
        delivered = self.state.setdefault("killmail_deliveries", {})
        kills = self.state["kills"]
        for key in list(kills) if keys is None else keys:
            kill = kills[key]
            if self._stop.is_set():
                break
            # Older versions marked a kill delivered by its ID alone (one channel per kill).
            if key in delivered:
                del kills[key]
                continue
            name, targets = self.routes[kill["system"]]
            done = True
            for channel_id, config in targets:
                if self._stop.is_set():
                    done = False
                    break
                delivery = f"{key}:{channel_id}"
                if delivery in delivered:
                    continue
                if not await self._deliver(kill, name, channel_id, config, delivery):
                    done = False
            if done:
                del kills[key]
                await self._save()
        await self._save()

    async def _deliver(self, kill, name, channel_id, config, delivery):
        try:
            await self.alert_channel(channel_id, config)
            mention = ""
            mentions = discord.AllowedMentions.none()
            if config.ping == "everyone":
                mention = "@everyone "
                mentions = discord.AllowedMentions(everyone=True, users=False, roles=False)
            elif config.ping == "role":
                role = discord.Object(id=int(config.role_id))
                mention = f"<@&{role.id}> "
                mentions = discord.AllowedMentions(everyone=False, users=False, roles=[role])
            stamp = datetime.fromtimestamp(kill["time"], UTC)
            payload = {
                "content": f"{mention}**{name}** · {stamp:%Y-%m-%d %H:%M:%S} UTC\n"
                f"https://zkillboard.com/kill/{kill['id']}/",
                "allowed_mentions": mentions.to_dict(),
            }
            await self._post(delivery, channel_id, payload, kill["id"])
            return True
        except Exception as exc:
            log.warning(
                "Killmail forwarding failed; kill=%s channel=%s (%s)",
                kill["id"],
                channel_id,
                type(exc).__name__,
            )
            return False

    async def _post(self, delivery, channel_id, payload, kill_id):
        """Mark, save, then POST once; a failed POST is retried, an uncertain one never."""
        delivered = self.state["killmail_deliveries"]
        delivered[delivery] = {"channel_id": channel_id, "status": "sending"}
        try:
            await self._save()
        except BaseException:
            delivered.pop(delivery, None)
            raise
        try:
            await self.sender.post_message(channel_id, payload, retry_server_errors=False)
        except (PermanentDeliveryError, TransientDeliveryError):
            delivered.pop(delivery, None)
            await self._save()
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            delivered[delivery]["status"] = "delivery_unknown"
            log.warning("Killmail delivery uncertain; kill=%s channel=%s", kill_id, channel_id)
        else:
            delivered[delivery]["status"] = "sent"
        await self._save()

    # Alliance killmails ---------------------------------------------------

    def _queue_alliance(self, packet):
        """Queue this packet for every server watching one of its alliances."""
        keys = []
        if not self.alliance_servers:
            return keys
        pending = self.state.setdefault("alliance_kills", {})
        delivered = self.state["killmail_deliveries"]
        for guild, config in self.alliance_servers:
            alliances = {int(alliance) for alliance in config.alliance_ids}
            try:
                kill = parse_alliance_kill(packet, alliances, self.clock())
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                return keys  # Already reported as a malformed record.
            if kill is None:
                continue
            key = f"a:{kill['id']}:{config.alliance_channel}"
            if key in delivered or key in pending:
                continue
            pending[key] = {"guild": guild, "kill": kill, "attempts": 0}
            keys.append(key)
        return keys

    async def _name(self, kind, entity):
        if not entity:
            return None
        key = (kind, entity)
        if key not in self._names:
            try:
                self._names[key] = await self.esi.name(kind, entity)
            except Exception as exc:
                # A missing name never blocks the killmail; the embed shows the ID.
                log.warning("EVE name lookup failed; %s=%s (%s)", kind, entity, type(exc).__name__)
                return None
        return self._names[key]

    async def alliance_payload(self, kill, config):
        names = {}
        lookups = [("system", kill["system"])]
        for party in (kill["victim"], kill["final_blow"]):
            lookups += [
                ("character", party.get("character_id")),
                ("corporation", party.get("corporation_id")),
                ("alliance", party.get("alliance_id")),
            ]
        lookups.append(("type", kill["victim"].get("ship_type_id")))
        for kind, entity in lookups:
            if entity and (found := await self._name(kind, entity)):
                names[(kind, entity)] = found
        threshold = (
            config.alliance_here_loss_value if kill["side"] == "loss" else config.here_min_value
        )
        here = kill["value"] >= threshold
        return {
            "content": "@here" if here else "",
            "embeds": [alliance_embed(kill, names).to_dict()],
            "allowed_mentions": (
                discord.AllowedMentions(everyone=True, users=False, roles=False)
                if here
                else discord.AllowedMentions.none()
            ).to_dict(),
        }

    async def _forward_alliance(self, *, keys=None):
        pending = self.state.get("alliance_kills", {})
        delivered = self.state["killmail_deliveries"]
        configs = dict(self.alliance_servers)
        for key in list(pending) if keys is None else keys:
            if self._stop.is_set():
                break
            entry = pending.get(key)
            config = configs.get(entry["guild"]) if entry else None
            if entry is None or key in delivered or config is None:
                pending.pop(key, None)
                continue
            channel_id = config.alliance_channel
            kill = entry["kill"]
            try:
                await self.alert_channel(channel_id, replace(config, ping="none"))
                payload = await self.alliance_payload(kill, config)
                await self._post(key, channel_id, payload, kill["id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                entry["attempts"] += 1
                log.warning(
                    "Alliance killmail forwarding failed; kill=%s channel=%s (%s)",
                    kill["id"],
                    channel_id,
                    type(exc).__name__,
                )
                if entry["attempts"] >= ALLIANCE_MAX_ATTEMPTS:
                    log.warning(
                        "Giving up alliance killmail after %s attempts; kill=%s channel=%s",
                        entry["attempts"],
                        kill["id"],
                        channel_id,
                    )
                    pending.pop(key, None)
                await self._save()
                continue
            pending.pop(key, None)
            await self._save()
