import sqlite3
from importlib.resources import files

import pytest

from wabot.repository import Repository


@pytest.fixture
async def repository(tmp_path):
    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    yield repo
    await repo.close()


async def completion(repo, interaction="1", **overrides):
    args = dict(
        interaction_id=interaction,
        guild_id="1",
        author_id="10",
        author_name="Pilot",
        system="kino",
        channel_id="5",
        payout_at=1000,
        due_at=2380,
        created_at=1000,
    )
    return await repo.record_timer(**(args | overrides))


async def adjust(repo, **overrides):
    await repo.set_statistic(
        **(
            dict(
                guild_id="1",
                author_id="10",
                author_name="Pilot",
                system="kino",
                count=7,
                expected_count=1,
            )
            | overrides
        )
    )


async def test_corrections_update_stats_rank_and_history_without_rescheduling(repository):
    timer, _ = await completion(repository)
    await adjust(repository)
    assert (await repository.stats("1"))["kino"] == 7
    assert (await repository.leaderboard("1")).entries[0].count == 7
    assert await repository.get_timer(timer.id) == timer
    players, history = await repository.statistics_editor("1")
    assert players[0]["kino"] == 7
    assert history[0]["before_count"] == 1 and history[0]["after_count"] == 7
    assert history[0]["delta"] == 6
    await completion(repository, "2")
    assert (await repository.stats("1"))["total"] == 8
    assert (await repository.leaderboard("1")).entries[0].count == 8


async def test_zeroing_manual_players_ties_and_guild_isolation(repository):
    await completion(repository)
    await adjust(repository, count=0)
    assert (await repository.leaderboard("1")).entries == []
    await adjust(repository, count=3, expected_count=0)
    await adjust(
        repository, author_id="20", author_name="Scout", system="niarja", count=3, expected_count=0
    )
    await adjust(repository, guild_id="2", count=10, expected_count=0)
    ranked = (await repository.leaderboard("1")).entries
    assert [(r.author_id, r.count, r.rank) for r in ranked] == [("10", 3, 1), ("20", 3, 1)]
    assert (await repository.stats("1"))["total"] == 6
    assert (await repository.stats("2"))["total"] == 10


async def test_stale_edits_cannot_overwrite_new_completion(repository):
    await completion(repository)
    await completion(repository, "2")
    with pytest.raises(ValueError, match="changed since"):
        await adjust(repository)
    assert (await repository.stats("1"))["total"] == 2
    assert (await repository.statistics_editor("1"))[1] == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"count": -1},
        {"count": True},
        {"author_id": "bad"},
        {"system": "jita"},
        {"author_name": ""},
        {"count": 1_000_001},
    ],
)
async def test_invalid_corrections_are_atomic(repository, overrides):
    await completion(repository)
    with pytest.raises(ValueError):
        await adjust(repository, **overrides)
    assert (await repository.stats("1"))["total"] == 1


async def test_backup_and_restart_retain_adjustments(repository, tmp_path):
    await completion(repository)
    await adjust(repository)
    backup = tmp_path / "backup.sqlite3"
    await repository.backup(backup)
    restored = Repository(backup)
    await restored.initialize()
    try:
        assert (await restored.stats("1"))["total"] == 7
        assert len((await restored.statistics_editor("1"))[1]) == 1
    finally:
        await restored.close()


async def test_version_one_database_upgrades_with_original_records(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(files("wabot.migrations").joinpath("001_initial.sql").read_text())
        db.execute("PRAGMA user_version = 1")
        db.execute("""INSERT INTO timers (interaction_id, guild_id, author_id, author_name, channel_id,
                    system, payout_at, due_at, created_at, next_attempt_at) VALUES ('1', '1', '10', 'Pilot', '5', 'kino', 1000, 2380, 1000, 2380)""")
    repo = Repository(path)
    await repo.initialize()
    try:
        assert (await repo.stats("1"))["total"] == 1
        await adjust(repo)
        assert (await repo.stats("1"))["total"] == 7
    finally:
        await repo.close()


async def test_manual_corrections_keep_rank_pages_consistent(repository):
    for author in range(10, 23):
        await adjust(repository, author_id=str(author), count=1, expected_count=0)
    page = await repository.leaderboard("1", 2)
    assert len(page.entries) == 3 and page.total_authors == 13
    for author in range(20, 23):
        await adjust(repository, author_id=str(author), count=0, expected_count=1)
    assert (await repository.leaderboard("1")).total_pages == 1
    with pytest.raises(ValueError, match="Invalid page"):
        await repository.leaderboard("1", 2)
