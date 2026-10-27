import sqlite3

import pytest
import pytest_asyncio

from wabot.backup import BackupManager, restore_database
from wabot.repository import Repository


@pytest_asyncio.fixture
async def repo(tmp_path):
    repo = Repository(tmp_path / "live.sqlite3")
    await repo.initialize()
    try:
        yield repo
    finally:
        await repo.close()


async def record(repo, identity="1", payout=1000):
    return await repo.record_timer(
        interaction_id=identity,
        guild_id="1",
        channel_id="2",
        author_id="3",
        author_name="Pilot",
        system="kino",
        payout_at=payout,
        due_at=payout + 1380,
        created_at=payout,
    )


async def test_snapshot_is_consistent_and_copied_offsite(repo, tmp_path):
    await record(repo)
    manager = BackupManager(repo, tmp_path / "backups", tmp_path / "offsite")
    path = await manager.create_backup()
    assert path.read_bytes() == (tmp_path / "offsite" / path.name).read_bytes()
    await record(repo, "2")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM timers").fetchone()[0] == 1


async def test_retains_seven_snapshots_and_unrelated_files(repo, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    keep = backups / "personal.sqlite3"
    keep.write_text("leave this alone")
    manager = BackupManager(repo, backups)
    snapshots = [await manager.create_backup() for _ in range(9)]
    assert not snapshots[0].exists() and not snapshots[1].exists()
    assert all(path.exists() for path in snapshots[2:])
    assert keep.read_text() == "leave this alone"
    with pytest.raises(ValueError):
        BackupManager(repo, backups, keep=6)


async def test_restore_suppresses_old_due_times_without_losing_statistics(repo, tmp_path):
    await record(repo)
    await record(repo, "2", payout=3000)
    path = await BackupManager(repo, tmp_path / "backups").create_backup()
    destination = repo.path
    await repo.close()
    await restore_database(path, destination, 2380)
    await repo.initialize()
    assert (await repo.get_timer(1)).status == "skipped_expired"
    assert (await repo.get_timer(2)).status == "pending"
    assert (await repo.stats("1"))["total"] == 2
    assert await repo.claim_next_due(2381) is None


@pytest.mark.parametrize("kind", ["garbage", "empty_sqlite", "future_version"])
async def test_invalid_restore_preserves_live_database(repo, tmp_path, kind):
    await record(repo)
    invalid = tmp_path / "invalid.sqlite3"
    if kind == "garbage":
        invalid.write_bytes(b"not sqlite")
    elif kind == "empty_sqlite":
        with sqlite3.connect(invalid) as db:
            db.execute("CREATE TABLE other(value TEXT)")
    else:
        await repo.backup(invalid)
        with sqlite3.connect(invalid) as db:
            db.execute("PRAGMA user_version=999")
    destination = repo.path
    await repo.close()
    with pytest.raises((ValueError, sqlite3.DatabaseError, RuntimeError)):
        await restore_database(invalid, destination, 5000)
    await repo.initialize()
    assert (await repo.stats("1"))["total"] == 1
    assert (await repo.get_timer(1)).status == "pending"
