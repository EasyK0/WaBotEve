"""Validated environment and guild configuration; no credentials in source files."""

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .market_config import MarketSettings
from .messages import MessageSettings
from .pvp_config import PvpSettings
from .servers import ServerSettings, legacy_servers, parse_servers


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    guild_ids: tuple[str, ...]
    database_path: Path
    backup_dir: Path
    offsite_backup_dir: Path | None = None
    log_level: str = "INFO"
    market: MarketSettings = field(default_factory=MarketSettings)
    pvp: PvpSettings = field(default_factory=PvpSettings)
    messages: MessageSettings = field(default_factory=MessageSettings)
    servers: tuple[ServerSettings, ...] = ()

    @property
    def server_settings(self) -> tuple[ServerSettings, ...]:
        """Per-server features; older profiles are migrated from the global settings."""
        return self.servers or legacy_servers(self.guild_ids, self.market, self.pvp)

    def feature_enabled(self, guild_id, feature: str) -> bool:
        return str(guild_id) in self.feature_guilds(feature)

    def feature_guilds(self, feature: str) -> tuple[str, ...]:
        servers = [server for server in self.server_settings if server.enabled(feature)]
        if any(server.guild_id is None for server in servers):
            # A migrated legacy market channel may be in any configured server.
            return self.guild_ids
        return tuple(server.guild_id for server in servers if server.guild_id in self.guild_ids)

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "Settings":
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if require_token and (not token or token == "YOUR_DISCORD_BOT_TOKEN"):
            raise ConfigurationError("Set DISCORD_TOKEN to your bot token in the environment.")
        config_path = Path(os.environ.get("CONFIG_PATH", "config.json")).expanduser()
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
        except (OSError, ValueError) as exc:
            raise ConfigurationError(
                "Cannot read CONFIG_PATH as a JSON configuration file."
            ) from exc
        if (
            not isinstance(config, dict)
            or ("guild_ids" not in config and "servers" not in config)
            or set(config) - {"guild_ids", "servers", "market", "pvp", "messages"}
        ):
            raise ConfigurationError(
                "Configuration requires guild_ids or servers and permits optional market, pvp and messages settings."
            )
        servers = ()
        if "servers" in config:
            try:
                servers = parse_servers(config["servers"])
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
            guilds = [server.guild_id for server in servers]
        else:
            guilds = config["guild_ids"]
        if not isinstance(guilds, list) or not guilds:
            raise ConfigurationError(
                "guild_ids must be a non-empty list of Discord server ID strings."
            )
        for guild in guilds:
            if (
                not isinstance(guild, str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", guild)
                or int(guild) >= 2**64
            ):
                raise ConfigurationError(
                    "Replace guild_ids placeholders with Discord server ID strings."
                )
        if len(set(guilds)) != len(guilds):
            raise ConfigurationError("guild_ids must not contain duplicates.")
        database_path = (
            Path(os.environ.get("DATABASE_PATH", "data/bot.sqlite3")).expanduser().resolve()
        )
        if database_path.is_dir():
            raise ConfigurationError("DATABASE_PATH must name a writable SQLite file.")
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.")
        offsite = os.environ.get("OFFSITE_BACKUP_DIR", "").strip()
        try:
            if "market" in config and not isinstance(config["market"], dict):
                raise ValueError("market must be a configuration object.")
            market = MarketSettings.from_config(config.get("market"))
            if "pvp" in config and not isinstance(config["pvp"], dict):
                raise ValueError("pvp must be a configuration object.")
            pvp = PvpSettings.from_config(config.get("pvp"))
            if "messages" in config and not isinstance(config["messages"], dict):
                raise ValueError("messages must be a configuration object.")
            messages = MessageSettings.from_config(config.get("messages"))
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            token=token,
            guild_ids=tuple(guilds),
            database_path=database_path,
            backup_dir=Path(os.environ.get("BACKUP_DIR", str(database_path.parent / "backups")))
            .expanduser()
            .resolve(),
            offsite_backup_dir=Path(offsite).expanduser().resolve() if offsite else None,
            log_level=level,
            market=market,
            pvp=pvp,
            messages=messages,
            servers=servers,
        )
