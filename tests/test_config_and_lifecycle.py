import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from wabot.bot import Wabot
from wabot.config import ConfigurationError, Settings
from wabot.logging_setup import RedactingFormatter
from wabot.repository import Repository


@pytest.fixture
def environment(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"guild_ids": ["123456789012345678"]}), encoding="utf-8")
    for key in [
        "DISCORD_TOKEN",
        "CONFIG_PATH",
        "DATABASE_PATH",
        "BACKUP_DIR",
        "OFFSITE_BACKUP_DIR",
        "LOG_LEVEL",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "bot.sqlite3"))
    return config


def test_missing_token_stops_run_but_local_check_is_available(environment):
    with pytest.raises(ConfigurationError, match="DISCORD_TOKEN"):
        Settings.from_env()
    settings = Settings.from_env(require_token=False)
    assert settings.guild_ids == ("123456789012345678",)
    assert settings.database_path.name == "bot.sqlite3"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"guild_ids": []},
        {"guild_ids": [123]},
        {"guild_ids": ["YOUR_DISCORD_SERVER_ID"]},
        {"guild_ids": ["1", "1"]},
        {"guild_ids": [str(2**64)]},
        {"guild_ids": ["1"], "role_ids": {}},
    ],
)
def test_invalid_configuration_fails_before_connection(environment, config):
    environment.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        Settings.from_env(require_token=False)


def test_secrets_redacted_from_log_message_and_exception():
    token = "pretend-secret"
    formatter = RedactingFormatter(token)
    record = logging.LogRecord("test", logging.ERROR, "file.py", 1, "failure: %s", (token,), None)
    assert token not in formatter.format(record)
    try:
        raise RuntimeError(token)
    except RuntimeError as exc:
        record.exc_info = (type(exc), exc, exc.__traceback__)
    assert token not in formatter.format(record)


async def test_shutdown_waits_for_inflight_queue_even_when_disconnected(tmp_path):
    repository = Repository(tmp_path / "db.sqlite3")
    settings = Settings("unused", ("1",), repository.path, tmp_path / "backups")
    client = Wabot(settings, repository)
    started, complete = asyncio.Event(), asyncio.Event()

    async def in_flight():
        started.set()
        await complete.wait()

    async with client:
        client._queue_started = True
        client.background = [asyncio.create_task(in_flight(), name="reminder-queue")]
        await started.wait()
        first = asyncio.create_task(client.close())
        second = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        complete.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        assert not client.background[0].cancelled()


async def test_reconnect_ready_event_does_not_spawn_or_sync(tmp_path):
    repository = Repository(tmp_path / "db.sqlite3")
    client = Wabot(Settings("unused", ("1",), repository.path, tmp_path / "backups"), repository)
    client._connection.user = type("User", (), {"id": 1})()
    client.tree.sync = AsyncMock()
    try:
        await client.on_ready()
        await client.on_ready()
        assert client.background == []
        client.tree.sync.assert_not_called()
    finally:
        await client.close()
