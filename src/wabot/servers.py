"""Per-server feature switches and settings, including migration of single-server profiles."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace

from wabot.market_config import MarketSettings
from wabot.pvp_config import PvpSettings

FEATURES = ("timers", "market", "pvp")
FEATURE_LABELS = {
    "timers": "Respawn timers",
    "market": "Jita + Perimeter market report",
    "pvp": "PvP: Pochven and alliances",
}
FEATURE_DESCRIPTIONS = {
    "timers": "/timer, /stats and /rank commands, plus a reminder 23 minutes after the payout.",
    "market": "Regular price and volume report for selected items on a server channel.",
    "pvp": "Killmails from Kino, Niarja and Archee, plus kills and losses of chosen alliances across EVE.",
}
FEATURE_TABS = {"timers": "Timers", "market": "Market", "pvp": "PvP"}
MAX_NAME_LENGTH = 60


def _snowflake(value) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
        and int(value) < 2**64
    )


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """One Discord server. `guild_id=None` is only used for migrated legacy market routing."""

    guild_id: str | None
    name: str = ""
    timers: bool = False
    market: bool = False
    market_channel_id: str | None = None
    pvp: bool = False
    pvp_settings: PvpSettings = field(default_factory=PvpSettings)

    def __post_init__(self):
        if self.guild_id is not None and not _snowflake(self.guild_id):
            raise ValueError("The server ID must be a valid Discord ID.")
        if not isinstance(self.name, str) or len(self.name) > MAX_NAME_LENGTH:
            raise ValueError(f"The server name can have at most {MAX_NAME_LENGTH} characters.")
        for name in FEATURES:
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Switch {name} must be true or false.")
        if self.market_channel_id is not None and not _snowflake(self.market_channel_id):
            raise ValueError("The market channel ID must be a valid Discord ID.")
        if not isinstance(self.pvp_settings, PvpSettings):
            raise ValueError("Invalid server PvP settings.")

    @property
    def label(self) -> str:
        return f"{self.name} ({self.guild_id})" if self.name else str(self.guild_id)

    @property
    def market_active(self) -> bool:
        return self.market and self.market_channel_id is not None

    @property
    def pvp_active(self) -> bool:
        return self.pvp and bool(
            self.pvp_settings.pochven_active or self.pvp_settings.alliance_feed_active
        )

    def enabled(self, feature: str) -> bool:
        return bool(getattr(self, feature))

    def to_json(self) -> dict:
        pvp = asdict(self.pvp_settings)
        pvp.pop("enabled")
        return {
            "id": self.guild_id,
            "name": self.name,
            "features": {name: getattr(self, name) for name in FEATURES},
            "market": {"channel_id": self.market_channel_id},
            "pvp": pvp,
        }

    @classmethod
    def from_json(cls, value) -> ServerSettings:
        if not isinstance(value, dict) or set(value) - {"id", "name", "features", "market", "pvp"}:
            raise ValueError("Invalid server entry in the configuration.")
        features = value.get("features", {})
        market = value.get("market", {})
        pvp = value.get("pvp", {})
        if not isinstance(features, dict) or set(features) - set(FEATURES):
            raise ValueError("Unknown feature in the server settings.")
        if not isinstance(market, dict) or set(market) - {"channel_id"}:
            raise ValueError("Invalid server market settings.")
        if not isinstance(pvp, dict) or "enabled" in pvp:
            raise ValueError("Invalid server PvP settings.")
        pvp_settings = PvpSettings.from_config(pvp)
        if features.get("pvp", False) and pvp_settings.ping == "role" and not pvp_settings.role_id:
            raise ValueError("Enter a role ID or choose another PvP ping.")
        return cls(
            guild_id=value.get("id"),
            name=value.get("name", ""),
            timers=features.get("timers", False),
            market=features.get("market", False),
            market_channel_id=market.get("channel_id"),
            pvp=features.get("pvp", False),
            pvp_settings=pvp_settings,
        )


def legacy_servers(
    guild_ids: tuple[str, ...], market: MarketSettings, pvp: PvpSettings
) -> tuple[ServerSettings, ...]:
    """Profiles from before per-server settings: timers everywhere, market and PvP once."""
    servers = [ServerSettings(guild_id, timers=True) for guild_id in guild_ids]
    if market.enabled and market.channel_id is not None:
        # The old market channel could be in any configured server.
        servers.append(ServerSettings(None, market=True, market_channel_id=market.channel_id))
    if pvp.enabled and servers:
        first = servers[0]
        servers[0] = ServerSettings(
            first.guild_id,
            first.name,
            first.timers,
            pvp=True,
            pvp_settings=replace(pvp, enabled=False),
        )
    return tuple(servers)


def parse_servers(value) -> tuple[ServerSettings, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Add at least one Discord server.")
    servers = tuple(ServerSettings.from_json(item) for item in value)
    ids = [server.guild_id for server in servers]
    if any(guild_id is None for guild_id in ids):
        raise ValueError("Every server needs an ID.")
    if len(set(ids)) != len(ids):
        raise ValueError("The server list contains duplicate IDs.")
    return servers


def servers_from_config(config: dict) -> tuple[ServerSettings, ...]:
    """Servers of a JSON configuration, migrating a legacy single-server profile in memory."""
    if "servers" in config:
        return parse_servers(config["servers"])
    market = MarketSettings.from_config(config.get("market"), environ={})
    pvp = PvpSettings.from_config(config.get("pvp"))
    servers = legacy_servers(tuple(config.get("guild_ids", [])), market, pvp)
    named = [server for server in servers if server.guild_id is not None]
    if any(server.guild_id is None for server in servers) and named:
        # In the editor the old market channel belongs to the first server.
        first = named[0]
        named[0] = ServerSettings(
            first.guild_id,
            first.name,
            first.timers,
            market=True,
            market_channel_id=market.channel_id,
            pvp=first.pvp,
            pvp_settings=first.pvp_settings,
        )
    return tuple(named)


def migrate_config(config: dict) -> dict:
    """The same configuration written in the per-server format."""
    servers = servers_from_config(config)
    result = {key: value for key, value in config.items() if key != "pvp"}
    result["servers"] = [server.to_json() for server in servers]
    result["guild_ids"] = [server.guild_id for server in servers]
    market = dict(result.get("market", {}))
    market.pop("channel_id", None)
    market["enabled"] = False
    result["market"] = market
    return result
