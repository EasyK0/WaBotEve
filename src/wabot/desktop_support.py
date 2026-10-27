"""Windows desktop profile, protected credentials and portable data packages."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import ExitStack, closing, contextmanager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock

from wabot.backup import _validate_database, restore_database
from wabot.market_config import _ENV_FIELDS, MarketSettings
from wabot.messages import MessageSettings
from wabot.pvp_config import PvpSettings
from wabot.servers import parse_servers

APP_VERSION = "1.10.0"
MAX_DATABASE_BYTES = 512 * 1024 * 1024


def validate_config(config: object) -> dict:
    if not isinstance(config, dict) or set(config) - {
        "guild_ids",
        "servers",
        "market",
        "pvp",
        "messages",
    }:
        raise ValueError("Invalid bot configuration file.")
    servers = parse_servers(config["servers"]) if "servers" in config else None
    guilds = (
        [server.guild_id for server in servers] if servers is not None else config.get("guild_ids")
    )
    if not isinstance(guilds, list) or not guilds:
        raise ValueError("Enter the ID of at least one Discord server.")
    for guild in guilds:
        if (
            not isinstance(guild, str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", guild)
            or int(guild) >= 2**64
        ):
            raise ValueError("The server ID must be a valid Discord ID.")
    if len(set(guilds)) != len(guilds):
        raise ValueError("The server list contains duplicate IDs.")
    if "market" in config and not isinstance(config["market"], dict):
        raise ValueError("Market settings must be a JSON object.")
    MarketSettings.from_config(config.get("market"), environ={})
    if "pvp" in config and not isinstance(config["pvp"], dict):
        raise ValueError("pvp must be a configuration object.")
    pvp = PvpSettings.from_config(config.get("pvp"))
    if "messages" in config and not isinstance(config["messages"], dict):
        raise ValueError("messages must be a configuration object.")
    messages = MessageSettings.from_config(config.get("messages"))
    result = copy.deepcopy(config)
    if servers is not None:
        result["servers"] = [server.to_json() for server in servers]
        result["guild_ids"] = guilds
    if "pvp" in result:
        result["pvp"] = asdict(pvp)
    if "messages" in result:
        result["messages"] = messages.overrides
    return result


def read_config(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("The configuration file is too large.")
    return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".wabot-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def protect_secret(data: bytes, *, decrypt: bool = False) -> bytes:
    """DPAPI binds the installed token to the current Windows user."""
    if os.name != "nt":
        raise OSError("Saving the launcher token requires Windows.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    original = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(original), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Cannot read or save the token for this Windows account.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def validate_token(token: str) -> str:
    token = token.strip()
    if token and (len(token) < 30 or token.count(".") != 2 or any(c.isspace() for c in token)):
        raise ValueError(
            "Enter the bot token from the Discord Developer Portal, not the application ID."
        )
    return token


def read_env_token(path: Path) -> str:
    """Only read the explicitly selected DISCORD_TOKEN value; never execute .env."""
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() == "DISCORD_TOKEN":
            return validate_token(value.strip().strip("\"'"))
    return ""


def snapshot_database(source: Path, target: Path) -> None:
    _validate_database(source)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as backup:
            original.backup(backup)
            backup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup.execute("PRAGMA journal_mode=DELETE")
    _validate_database(target)


@dataclass(frozen=True)
class DesktopProfile:
    directory: Path

    @classmethod
    def default(cls) -> DesktopProfile:
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise OSError("Cannot find the Windows user data folder.")
        return cls(Path(base) / "Wabot")

    @property
    def config_path(self) -> Path:
        return self.directory / "config.json"

    @property
    def database_path(self) -> Path:
        return self.directory / "data" / "bot.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.directory / "token.dpapi"

    def token(self) -> str:
        return (
            protect_secret(self.token_path.read_bytes(), decrypt=True).decode("utf-8")
            if self.token_path.exists()
            else ""
        )

    @contextmanager
    def stopped(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            stack.enter_context(FileLock(str(self.database_path) + ".lock", timeout=0))
            stack.enter_context(FileLock(str(self.database_path) + ".maintenance.lock", timeout=0))
            yield

    def save(self, config: dict, token: str | None = None) -> None:
        config = validate_config(config)
        protected = protect_secret(validate_token(token).encode("utf-8")) if token else None
        with self.stopped():
            atomic_write(self.config_path, json_bytes(config))
            if protected is not None:
                atomic_write(self.token_path, protected)
            (self.directory / "commands.sha256").unlink(missing_ok=True)

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        for key in (*_ENV_FIELDS, "DISCORD_TOKEN", "OFFSITE_BACKUP_DIR"):
            environment.pop(key, None)
        environment.update(
            CONFIG_PATH=str(self.config_path),
            DATABASE_PATH=str(self.database_path),
            BACKUP_DIR=str(self.directory / "backups"),
            LOG_LEVEL="INFO",
            DISCORD_TOKEN=self.token(),
            PYTHONUTF8="1",
            PYTHONUNBUFFERED="1",
        )
        return environment

    def bootstrap(self, seed: Path) -> None:
        # Upgrades never replace an existing profile or its database/credentials.
        if self.config_path.exists():
            return
        if self.database_path.exists():
            raise ValueError(
                "A bot database exists without config.json. Restore the configuration or use the data import in the launcher."
            )
        self.import_folder(seed, initial=True)

    def import_folder(self, source: Path, *, initial: bool = False) -> None:
        source = source.resolve()
        if source == self.directory.resolve():
            raise ValueError("Choose a folder other than the launcher's current data folder.")
        config = read_config(source / "config.json")
        token = ""
        credentials = source / "credentials.json"
        if credentials.is_file():
            if credentials.stat().st_size > 16384:
                raise ValueError("Invalid token file.")
            token = validate_token(
                json.loads(credentials.read_text(encoding="utf-8"))["discord_token"]
            )
        elif (source / "token.dpapi").is_file():
            token = DesktopProfile(source).token()
        else:
            token = read_env_token(source / ".env")
        protected = protect_secret(token.encode("utf-8")) if token else None
        database = source / "bot.sqlite3"
        if not database.exists():
            database = source / "data" / "bot.sqlite3"
        if database.exists():
            _validate_database(database)
        with self.stopped():
            if initial and self.config_path.exists():
                return
            # Retain the previous profile as a usable migration package before replacing it.
            if self.config_path.exists():
                previous = self.directory / "backups" / f"before-import-{time.time_ns()}.wabot.zip"
                self._export(previous)
            with tempfile.TemporaryDirectory(
                prefix="wabot-import-", dir=self.directory
            ) as temporary:
                staging = Path(temporary)
                # Prepare all data before committing configuration or the database.
                atomic_write(staging / "config.json", json_bytes(config))
                if database.exists():
                    asyncio.run(restore_database(database, self.database_path, int(time.time())))
                os.replace(staging / "config.json", self.config_path)
                if protected is not None:
                    atomic_write(self.token_path, protected)
                elif not initial:
                    # Do not connect the imported configuration with an unrelated old token.
                    self.token_path.unlink(missing_ok=True)
                (self.directory / "commands.sha256").unlink(missing_ok=True)

    def _export(self, destination: Path) -> None:
        config = read_config(self.config_path)
        token = self.token()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wabot-export-", dir=self.directory) as temporary:
            staging = Path(temporary)
            archive_path = staging / "transfer.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("config.json", json_bytes(config))
                archive.writestr("manifest.json", json_bytes({"format": 1, "version": APP_VERSION}))
                if token:
                    archive.writestr("credentials.json", json_bytes({"discord_token": token}))
                if self.database_path.exists():
                    snapshot_database(self.database_path, staging / "bot.sqlite3")
                    archive.write(staging / "bot.sqlite3", "bot.sqlite3")
            # Stage beside the selected target so publication also works on a USB drive.
            descriptor, name = tempfile.mkstemp(prefix=".wabot-transfer-", dir=destination.parent)
            os.close(descriptor)
            published = Path(name)
            try:
                shutil.copyfile(archive_path, published)
                os.replace(published, destination)
            finally:
                published.unlink(missing_ok=True)

    def export(self, destination: Path) -> None:
        if destination.resolve() in {
            self.config_path.resolve(),
            self.database_path.resolve(),
            self.token_path.resolve(),
        }:
            raise ValueError("Choose a new package file, not a file the bot is using.")
        with self.stopped():
            self._export(destination)

    def import_archive(self, source: Path) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wabot-unpack-", dir=self.directory) as temporary:
            staging = Path(temporary)
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                allowed = {
                    "manifest.json": 16384,
                    "config.json": 1024 * 1024,
                    "credentials.json": 16384,
                    "bot.sqlite3": MAX_DATABASE_BYTES,
                }
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)) or "config.json" not in names:
                    raise ValueError("Invalid Wabot package.")
                for entry in entries:
                    if entry.filename not in allowed or entry.file_size > allowed[entry.filename]:
                        raise ValueError("The package contains an unsupported or too large file.")
                    with archive.open(entry) as src, (staging / entry.filename).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            self.import_folder(staging)
