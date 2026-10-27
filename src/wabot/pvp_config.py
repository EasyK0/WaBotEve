"""Killmail channel settings, including migration of older alert profiles."""

import re
from dataclasses import dataclass, field, fields

ROUTE_SYSTEMS = ("Kino", "Niarja", "Archee")


MAX_ALLIANCES = 50
HERE_MIN_VALUE = 1_000_000_000
ALLIANCE_HERE_LOSS_VALUE = 2_000_000_000


@dataclass(frozen=True, slots=True)
class PvpSettings:
    enabled: bool = False
    ping: str = "none"
    role_id: str | None = None
    system_channels: dict[str, str] = field(default_factory=dict)
    # Every killmail in any Pochven system also goes to this channel.
    pochven_channel: str | None = None
    # Kills and losses of these alliances anywhere in EVE go to `alliance_channel`.
    alliance_ids: list[str] = field(default_factory=list)
    alliance_channel: str | None = None
    # @here for an alliance kill / alliance loss worth at least this many ISK.
    here_min_value: int = HERE_MIN_VALUE
    alliance_here_loss_value: int = ALLIANCE_HERE_LOSS_VALUE

    @property
    def alliance_feed_active(self) -> bool:
        return bool(self.alliance_ids) and bool(self.alliance_channel)

    @property
    def pochven_active(self) -> bool:
        return bool(self.system_channels or self.pochven_channel)

    def channels_for(self, system: str) -> list[str]:
        """Channels that get a killmail from this Pochven system, without duplicates."""
        channels = [self.system_channels.get(system), self.pochven_channel]
        return list(dict.fromkeys(channel for channel in channels if channel))

    def __post_init__(self):
        if isinstance(self.alliance_ids, tuple):
            object.__setattr__(self, "alliance_ids", list(self.alliance_ids))
        if not isinstance(self.alliance_ids, list) or len(self.alliance_ids) > MAX_ALLIANCES:
            raise ValueError(f"The alliance list can have at most {MAX_ALLIANCES} IDs.")
        for alliance in self.alliance_ids:
            if not isinstance(alliance, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", alliance):
                raise ValueError(f"Invalid EVE alliance ID: {alliance}")
        if len(set(self.alliance_ids)) != len(self.alliance_ids):
            raise ValueError("The alliance list contains duplicate IDs.")
        if self.alliance_channel is not None and (
            not isinstance(self.alliance_channel, str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", self.alliance_channel)
            or int(self.alliance_channel) >= 2**64
        ):
            raise ValueError("Invalid Discord channel ID for alliance kills.")
        if self.pochven_channel is not None and (
            not isinstance(self.pochven_channel, str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", self.pochven_channel)
            or int(self.pochven_channel) >= 2**64
        ):
            raise ValueError("Invalid Discord channel ID for all Pochven kills.")
        if self.alliance_ids and self.alliance_channel is None:
            raise ValueError("Enter a Discord channel ID for alliance kills and losses.")
        for name in ("here_min_value", "alliance_here_loss_value"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"pvp.{name} must be an ISK amount (0 or more).")
        if type(self.enabled) is not bool:
            raise ValueError("pvp.enabled must be a boolean.")
        for name in ("role_id",):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
                or int(value) >= 2**64
            ):
                raise ValueError(f"pvp.{name} must be a Discord ID string or null.")
        if not isinstance(self.system_channels, dict):
            raise ValueError("pvp.system_channels must map system names to channel IDs.")
        for system, channel in self.system_channels.items():
            if system not in ROUTE_SYSTEMS:
                raise ValueError("Choose Kino, Niarja or Archee for killmail routing.")
            if (
                not isinstance(channel, str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", channel)
                or int(channel) >= 2**64
            ):
                raise ValueError(f"Invalid Discord channel ID for {system}.")
        if self.enabled and not (self.pochven_active or self.alliance_feed_active):
            raise ValueError("Add a system and channel ID before enabling monitoring.")
        if self.ping not in ("everyone", "role", "none"):
            raise ValueError("pvp.ping must be everyone, role or none.")
        if self.enabled and self.ping == "role" and self.role_id is None:
            raise ValueError("Set the PvP role ID before enabling role pings.")

    @classmethod
    def from_config(cls, config=None):
        if config is None:
            config = {}
        legacy = {"channel_id", "min_kills", "window_minutes", "cooldown_minutes"}
        if not isinstance(config, dict) or set(config) - ({f.name for f in fields(cls)} | legacy):
            raise ValueError("pvp must be an object with known PvP settings.")
        values = {key: value for key, value in config.items() if key not in legacy}
        if not values.get("system_channels") and config.get("channel_id"):
            values["system_channels"] = dict.fromkeys(ROUTE_SYSTEMS, config["channel_id"])
        return cls(**values)
