import asyncio
import json
import os
import queue
import sqlite3
import threading
import zipfile
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from filelock import FileLock, Timeout

from wabot.__main__ import _watch_desktop_stop
from wabot.desktop_entry import _stdin_control
from wabot.desktop_launcher import Launcher, worker_command
from wabot.desktop_support import (
    DesktopProfile,
    json_bytes,
    protect_secret,
    read_config,
    read_env_token,
)
from wabot.repository import Repository

TOKEN = "example-token-for-offline-tests.abcdef.abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def seed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_bytes(
        json_bytes(
            {
                "guild_ids": ["123"],
                "market": {
                    "enabled": True,
                    "channel_id": "456",
                    "interval_minutes": 60,
                    "datacaster_min_buy_price": "600000",
                },
            }
        )
    )
    return source


async def populate_database(source):
    repository = Repository(source / "bot.sqlite3")
    try:
        await repository.initialize()
        await repository.record_timer(
            interaction_id="1",
            guild_id="123",
            channel_id="456",
            author_id="789",
            author_name="Pilot",
            system="kino",
            payout_at=1000,
            due_at=2380,
            created_at=1000,
        )
    finally:
        await repository.close()


def database_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT author_name, status FROM timers").fetchall()


def test_first_install_preserves_configuration_and_history_without_replaying_old_timers(
    seed, tmp_path
):
    asyncio.run(populate_database(seed))
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    assert read_config(profile.config_path) == read_config(seed / "config.json")
    assert database_rows(profile.database_path) == [("Pilot", "skipped_expired")]
    assert database_rows(seed / "bot.sqlite3") == [("Pilot", "pending")]
    assert not profile.token_path.exists()


def test_upgrade_never_overwrites_existing_profile(seed, tmp_path):
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    config = read_config(profile.config_path)
    config["market"]["interval_minutes"] = 120
    profile.save(config)
    profile.bootstrap(seed)
    assert read_config(profile.config_path)["market"]["interval_minutes"] == 120


def test_generic_installer_without_seed_config_leaves_profile_empty(tmp_path):
    empty_seed = tmp_path / "seed"
    empty_seed.mkdir()
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(empty_seed)
    assert not profile.config_path.exists()
    assert not profile.database_path.exists()
    assert not profile.token_path.exists()


def test_installer_does_not_replace_existing_database_without_config(seed, tmp_path):
    profile = DesktopProfile(tmp_path / "installed")
    profile.database_path.parent.mkdir(parents=True)
    profile.database_path.write_bytes(b"existing data must be preserved")
    with pytest.raises(ValueError, match="database exists without"):
        profile.bootstrap(seed)
    assert profile.database_path.read_bytes() == b"existing data must be preserved"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_token_is_protected_at_rest_and_portable_export_import_restores_it(seed, tmp_path):
    asyncio.run(populate_database(seed))
    profile = DesktopProfile(tmp_path / "first")
    profile.bootstrap(seed)
    profile.save(read_config(profile.config_path), TOKEN)
    assert TOKEN.encode() not in profile.token_path.read_bytes()
    assert profile.token() == TOKEN
    destination = tmp_path / "USB drive" / "transfer.wabot.zip"
    profile.export(destination)
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "config.json",
            "manifest.json",
            "credentials.json",
            "bot.sqlite3",
        }
        assert json.loads(archive.read("credentials.json"))["discord_token"] == TOKEN
    migrated = DesktopProfile(tmp_path / "second")
    migrated.import_archive(destination)
    assert migrated.token() == TOKEN
    assert read_config(migrated.config_path) == read_config(profile.config_path)
    assert database_rows(migrated.database_path) == [("Pilot", "skipped_expired")]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_does_not_accept_corrupt_secrets():
    encrypted = protect_secret(b"secret")
    assert protect_secret(encrypted, decrypt=True) == b"secret"
    with pytest.raises(OSError):
        protect_secret(b"broken", decrypt=True)


def test_export_requires_stopped_bot(seed, tmp_path):
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    with FileLock(str(profile.database_path) + ".lock"):
        with pytest.raises(Timeout):
            profile.export(tmp_path / "transfer.zip")
    assert not (tmp_path / "transfer.zip").exists()


@pytest.mark.parametrize("filename", ["../escape.txt", "C:/escape.txt", "token.dpapi"])
def test_import_rejects_unexpected_paths_and_preserves_profile(seed, tmp_path, filename):
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    old = profile.config_path.read_bytes()
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("config.json", (seed / "config.json").read_bytes())
        archive.writestr(filename, "anything")
    with pytest.raises(ValueError):
        profile.import_archive(archive_path)
    assert profile.config_path.read_bytes() == old
    assert not (tmp_path / "escape.txt").exists()


def test_rejected_database_import_preserves_existing_settings_and_data(seed, tmp_path):
    asyncio.run(populate_database(seed))
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    original = profile.config_path.read_bytes()
    (seed / "bot.sqlite3").write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        profile.import_folder(seed)
    assert profile.config_path.read_bytes() == original
    assert database_rows(profile.database_path) == [("Pilot", "skipped_expired")]


def test_import_keeps_recoverable_previous_profile_and_invalidates_sync(seed, tmp_path):
    asyncio.run(populate_database(seed))
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    marker = profile.directory / "commands.sha256"
    marker.write_bytes(b"old")
    old = read_config(profile.config_path)
    new = read_config(seed / "config.json")
    new["market"]["interval_minutes"] = 120
    (seed / "config.json").write_bytes(json_bytes(new))
    profile.import_folder(seed)
    previous = list((profile.directory / "backups").glob("before-import-*.wabot.zip"))
    assert len(previous) == 1
    with zipfile.ZipFile(previous[0]) as archive:
        assert json.loads(archive.read("config.json")) == old
    assert read_config(profile.config_path) == new
    assert not marker.exists()


def test_desktop_environment_uses_profile_not_inherited_settings(seed, tmp_path, monkeypatch):
    profile = DesktopProfile(tmp_path / "installed")
    profile.bootstrap(seed)
    monkeypatch.setenv("MARKET_INTERVAL_MINUTES", "1")
    monkeypatch.setenv("CONFIG_PATH", "unrelated.json")
    monkeypatch.setenv("DISCORD_TOKEN", "unrelated-secret")
    monkeypatch.setenv("OFFSITE_BACKUP_DIR", "unrelated-directory")
    environment = profile.environment()
    assert "MARKET_INTERVAL_MINUTES" not in environment
    assert "OFFSITE_BACKUP_DIR" not in environment
    assert environment["CONFIG_PATH"] == str(profile.config_path)
    assert environment["DISCORD_TOKEN"] == ""
    assert os.environ["DISCORD_TOKEN"] == "unrelated-secret"


def test_env_file_is_parsed_without_execution_and_id_is_not_token(tmp_path):
    source = tmp_path / ".env"
    source.write_text(f'DISCORD_TOKEN="{TOKEN}"\nOTHER=ignored\n', encoding="utf-8")
    assert read_env_token(source) == TOKEN
    source.write_text("DISCORD_TOKEN=1234567890123456789", encoding="utf-8")
    with pytest.raises(ValueError, match="application ID"):
        read_env_token(source)


def test_worker_command_quotes_paths_as_arguments_and_has_no_token(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Program Files" / "WabotLauncher.exe"))
    profile = DesktopProfile(tmp_path / "Dane użytkownika")
    command = worker_command(profile, "run")
    assert command == [
        str(tmp_path / "Program Files" / "WabotWorker.exe"),
        "--data-dir",
        str(profile.directory),
        "--worker",
        "run",
    ]


def test_launcher_control_requests_shutdown_on_stop_and_on_closed_pipe(monkeypatch):
    from io import StringIO

    for content in ("STOP\n", ""):
        event = threading.Event()
        monkeypatch.setattr("sys.stdin", StringIO(content))
        _stdin_control(event)
        assert event.is_set()


async def test_desktop_shutdown_awaits_regular_bot_close():
    stop = threading.Event()
    client = SimpleNamespace(close=AsyncMock())
    task = asyncio.create_task(_watch_desktop_stop(client, stop))
    await asyncio.sleep(0)
    client.close.assert_not_called()
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    client.close.assert_awaited_once()


def test_failed_start_does_not_turn_next_manual_sync_into_an_automatic_start(tmp_path):
    launcher = Launcher.__new__(Launcher)
    launcher.profile = DesktopProfile(tmp_path)
    launcher.events = queue.Queue()
    launcher.root = Mock()
    launcher.phase = "sync"
    launcher.process = None
    launcher.start_after_sync = True
    launcher.closing = launcher.busy = False
    launcher.status = launcher.status_label = Mock()
    launcher._log = launcher._controls = Mock()
    launcher.action = Mock()
    launcher._sync_stamp = Mock(return_value=b"stamp")
    launcher.events.put(("exit", 1))
    launcher._poll()
    assert not launcher.start_after_sync
    launcher.phase = "sync"
    launcher.events.put(("exit", 0))
    launcher._poll()
    launcher.action.assert_not_called()
