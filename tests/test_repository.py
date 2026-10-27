import asyncio
import sqlite3

import pytest
import pytest_asyncio

from wabot.repository import Repository


@pytest_asyncio.fixture
async def repository(tmp_path):
    repo = Repository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    try:
        yield repo
    finally:
        await repo.close()


async def record(repo, interaction="100", **overrides):
    values = {
        "interaction_id": interaction,
        "guild_id": "1",
        "channel_id": "10",
        "author_id": "1000",
        "author_name": "Pilot",
        "system": "kino",
        "payout_at": 1000,
        "due_at": 2380,
        "created_at": 1100,
    }
    values.update(overrides)
    return await repo.record_timer(**values)


@pytest.mark.asyncio
async def test_first_start_versions_schema_and_creates_required_indexes(repository):
    with sqlite3.connect(repository.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        indexes = {row[1] for row in db.execute("PRAGMA index_list(timers)")}
    assert {"idx_timers_guild_system", "idx_timers_guild_author", "idx_timers_delivery"} <= indexes
    assert await repository.stats("1") == {"kino": 0, "niarja": 0, "archee": 0, "total": 0}
    assert await repository.get_timer(999) is None


@pytest.mark.asyncio
async def test_replayed_interaction_is_one_record_and_returns_original(repository):
    original, inserted = await record(repository)
    assert inserted
    assert original.status == "pending"
    assert original.next_attempt_at == original.due_at
    replay, inserted = await record(repository, author_name="Changed", system="invalid")
    assert not inserted
    assert replay == original
    second, inserted = await record(repository, "101")
    assert inserted and second.id != original.id
    assert (await repository.stats("1"))["total"] == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_inserts_across_connections(repository):
    second = Repository(repository.path)
    await second.initialize()
    try:
        results = await asyncio.gather(
            *[record(repository if number % 2 else second, "shared") for number in range(20)]
        )
    finally:
        await second.close()
    assert sum(inserted for _, inserted in results) == 1
    assert len({timer.id for timer, _ in results}) == 1
    assert (await repository.stats("1"))["total"] == 1


@pytest.mark.asyncio
async def test_sql_parameters_and_invalid_inserts_do_not_change_totals(repository):
    await record(repository, author_name="x'); DROP TABLE timers; --")
    for overrides in ({"system": "other"}, {"due_at": 2381}):
        with pytest.raises(sqlite3.IntegrityError):
            await record(repository, "rejected", **overrides)
    assert (await repository.stats("1"))["total"] == 1
    assert (await repository.leaderboard("1")).entries[
        0
    ].author_name == "x'); DROP TABLE timers; --"


@pytest.mark.asyncio
async def test_stats_include_every_status_and_are_isolated_by_guild(repository):
    statuses = ["retry", "sent", "skipped_expired", "failed", "delivery_unknown"]
    for number, status in enumerate(statuses):
        timer, _ = await record(
            repository, str(number), system=("kino", "niarja", "archee")[number % 3]
        )
        claimed = await repository.claim_next_due(2380)
        assert claimed.id == timer.id
        await repository.update_delivery(timer.id, status, next_attempt_at=5000)
    sending, _ = await record(repository, "sending")
    assert (await repository.claim_next_due(2380)).id == sending.id
    await record(repository, "pending")
    await record(repository, "other-guild", guild_id="2", system="archee")
    assert await repository.stats("1") == {"kino": 4, "niarja": 2, "archee": 1, "total": 7}
    assert await repository.stats("2") == {"kino": 0, "niarja": 0, "archee": 1, "total": 1}
    assert (await repository.leaderboard("1")).entries[0].count == 7
    assert (await repository.leaderboard("2")).entries[0].count == 1


@pytest.mark.asyncio
async def test_leaderboard_global_competition_ranks_numeric_ids_and_latest_name(repository):
    # Two joint leaders, followed by ten tied pilots split across two pages.
    for author_id in ["9", "10"]:
        await record(repository, f"{author_id}-old", author_id=author_id, author_name="Old")
        await record(
            repository, f"{author_id}-new", author_id=author_id, author_name=f"New {author_id}"
        )
    followers = [str(value) for value in range(20, 29)] + ["18446744073709551615"]
    for author_id in reversed(followers):
        await record(repository, author_id, author_id=author_id)
    await record(repository, "private", guild_id="2", author_id="9", author_name="Other server")
    first = await repository.leaderboard("1")
    second = await repository.leaderboard("1", page=2)
    assert first.total_pages == second.total_pages == 2
    assert first.total_authors == 12
    assert len(first.entries) == 10 and len(second.entries) == 2
    assert [entry.author_id for entry in first.entries[:2]] == ["9", "10"]
    assert [entry.rank for entry in first.entries[:3]] == [1, 1, 3]
    assert [entry.rank for entry in second.entries] == [3, 3]
    assert first.entries[0].author_name == "New 9"
    assert [entry.author_id for entry in first.entries[2:] + second.entries] == followers
    for page in [0, -1, 3]:
        with pytest.raises(ValueError, match=r"^Invalid page\. Choose a page between 1 and 2\.$"):
            await repository.leaderboard("1", page=page)


@pytest.mark.asyncio
async def test_empty_leaderboard(repository):
    result = await repository.leaderboard("1")
    assert result.entries == []
    assert result.total_authors == 0 and result.total_pages == 0 and result.page == 1


@pytest.mark.asyncio
async def test_claims_are_atomic_between_connections_and_retry_obeys_schedule(repository):
    timer, _ = await record(repository)
    second = Repository(repository.path)
    await second.initialize()
    try:
        assert await repository.claim_next_due(2379) is None
        claims = await asyncio.gather(repository.claim_next_due(2380), second.claim_next_due(2380))
        assert sum(claim is not None for claim in claims) == 1
        claim = next(claim for claim in claims if claim is not None)
        assert claim.id == timer.id and claim.status == "sending" and claim.attempt_count == 1
        await repository.update_delivery(
            timer.id, "retry", next_attempt_at=2385, last_error="HTTP 503"
        )
        assert await second.claim_next_due(2384) is None
        assert (await second.claim_next_due(2385)).attempt_count == 2
        await second.update_delivery(timer.id, "sent", sent_at=2385, message_id="9000")
        await repository.update_delivery(timer.id, "failed", last_error="stale worker")
        assert await repository.claim_next_due(10000) is None
        result = await repository.get_timer(timer.id)
        assert result.status == "sent" and result.sent_at == 2385 and result.message_id == "9000"
        assert result.last_error is None
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_restart_preserves_pending_and_marks_ambiguous_delivery(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    original = Repository(path)
    await original.initialize()
    first, _ = await record(original, "first")
    second, _ = await record(original, "second")
    assert (await original.claim_next_due(2380)).id == first.id
    await original.close()
    restarted = Repository(path)
    await restarted.initialize()
    try:
        assert await restarted.recover_sending() == [first.id]
        assert await restarted.recover_sending() == []
        assert (await restarted.get_timer(first.id)).status == "delivery_unknown"
        assert (await restarted.claim_next_due(2380)).id == second.id
        assert (await restarted.stats("1"))["total"] == 2
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_future_migration_is_rejected_without_changing_database(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 999")
    repo = Repository(path)
    with pytest.raises(RuntimeError, match="newer than this application"):
        await repo.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (999,)
        assert db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []


@pytest.mark.asyncio
async def test_cancelled_transaction_rolls_back_and_connection_remains_usable(repository):
    entered = asyncio.Event()
    wait_forever = asyncio.Event()

    async def interrupted_insert():
        async with repository._transaction() as db:
            await db.execute(
                """INSERT INTO timers (interaction_id, guild_id, channel_id, author_id,
                    author_name, system, payout_at, due_at, created_at, next_attempt_at)
                    VALUES ('cancelled', '1', '10', '1000', 'Pilot', 'kino', 1000, 2380, 1100, 2380)"""
            )
            entered.set()
            await wait_forever.wait()

    task = asyncio.create_task(interrupted_insert())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await repository.stats("1"))["total"] == 0
    await record(repository)
    assert (await repository.stats("1"))["total"] == 1


@pytest.mark.asyncio
async def test_backup_captures_wal_and_is_independent_of_later_writes(repository, tmp_path):
    timer, _ = await record(repository)
    backup_path = tmp_path / "backups" / "daily.sqlite3"
    await repository.backup(backup_path)
    await record(repository, "later", system="archee")
    snapshot = Repository(backup_path)
    await snapshot.initialize()
    try:
        assert await snapshot.get_timer(timer.id) == timer
        assert (await snapshot.stats("1"))["total"] == 1
        assert (await repository.stats("1"))["total"] == 2
    finally:
        await snapshot.close()
    with pytest.raises(ValueError, match="must differ"):
        await repository.backup(repository.path)
