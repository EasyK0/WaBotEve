"""Durable completion records and atomic reminder delivery claims."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

import aiosqlite

from .models import PAGE_SIZE, SYSTEMS, RankEntry, RankPage, Timer


class Repository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        # A transaction belongs to one coroutine, even though aiosqlite shares
        # a connection between coroutines. SQLite also protects other processes.
        self._lock = asyncio.Lock()

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Repository has not been initialized.")
        return self._connection

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
            db = await aiosqlite.connect(str(self.path), timeout=10, isolation_level=None)
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("PRAGMA busy_timeout = 10000")
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA synchronous = FULL")
                await db.execute("PRAGMA foreign_keys = ON")
                await self._migrate(db)
            except BaseException:
                await db.close()
                raise
            self._connection = db

    @staticmethod
    async def _migrate(db: aiosqlite.Connection) -> None:
        migrations = sorted(
            (entry for entry in files("wabot.migrations").iterdir() if entry.name.endswith(".sql")),
            key=lambda entry: entry.name,
        )
        versions = [int(entry.name.split("_", 1)[0]) for entry in migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise RuntimeError("Database migrations must be numbered consecutively.")
        # The version check and migration share a write transaction so concurrent
        # first starts cannot both decide to create the schema.
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            version = row[0]
            if version > len(migrations):
                raise RuntimeError(
                    f"Database schema version {version} is newer than this application "
                    f"supports ({len(migrations)})."
                )
            for number, migration in enumerate(migrations, start=1):
                if number <= version:
                    continue
                statement = ""
                for line in migration.read_text(encoding="utf-8").splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        await db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError(f"Incomplete SQL in {migration.name}.")
                # The value comes exclusively from bundled migration numbering.
                await db.execute(f"PRAGMA user_version = {number}")
            await db.commit()
        except BaseException:
            await asyncio.shield(db.rollback())
            raise

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def load_pvp_state(self) -> dict:
        async with self._transaction(write=False) as db:
            async with db.execute("SELECT payload FROM pvp_state WHERE id = 1") as cursor:
                row = await cursor.fetchone()
            return json.loads(row[0]) if row else {}

    async def save_pvp_state(self, state: dict) -> None:
        async with self._transaction() as db:
            await db.execute(
                "INSERT INTO pvp_state (id, payload) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (json.dumps(state),),
            )

    @asynccontextmanager
    async def _transaction(self, *, write: bool = True) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            db = self._db
            try:
                await db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
                await db.commit()
            except BaseException:
                await asyncio.shield(db.rollback())
                raise

    @staticmethod
    def _timer(row: aiosqlite.Row) -> Timer:
        return Timer(**dict(row))

    async def record_timer(
        self,
        *,
        interaction_id: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        author_name: str,
        system: str,
        payout_at: int,
        due_at: int,
        created_at: int,
    ) -> tuple[Timer, bool]:
        async with self._transaction() as db:
            # Check first: replaying an interaction always returns its original
            # record, even if a caller has subsequently changed its parameters.
            async with db.execute(
                "SELECT * FROM timers WHERE interaction_id = ?", (str(interaction_id),)
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                return self._timer(existing), False
            async with db.execute(
                """INSERT INTO timers (
                    interaction_id, guild_id, channel_id, author_id, author_name,
                    system, payout_at, due_at, created_at, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (interaction_id) DO NOTHING""",
                (
                    str(interaction_id),
                    str(guild_id),
                    str(channel_id),
                    str(author_id),
                    author_name,
                    system,
                    payout_at,
                    due_at,
                    created_at,
                    due_at,
                ),
            ) as cursor:
                inserted = cursor.rowcount == 1
            async with db.execute(
                "SELECT * FROM timers WHERE interaction_id = ?", (str(interaction_id),)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Inserted timer could not be read back.")
            return self._timer(row), inserted

    async def get_timer(self, timer_id: int) -> Timer | None:
        async with self._lock:
            async with self._db.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)) as cursor:
                row = await cursor.fetchone()
            return self._timer(row) if row is not None else None

    async def stats(self, guild_id: str) -> dict[str, int]:
        async with self._lock:
            async with self._db.execute(
                "SELECT system, SUM(count) AS count FROM completion_totals WHERE guild_id = ? GROUP BY system",
                (str(guild_id),),
            ) as cursor:
                rows = await cursor.fetchall()
        counts = {"kino": 0, "niarja": 0, "archee": 0}
        counts.update({row["system"]: row["count"] for row in rows})
        counts["total"] = sum(counts.values())
        return counts

    async def leaderboard(self, guild_id: str, page: int = 1) -> RankPage:
        async with self._transaction(write=False) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM (SELECT author_id FROM completion_totals WHERE guild_id = ? GROUP BY author_id HAVING SUM(count) > 0)",
                (str(guild_id),),
            ) as cursor:
                total_authors = (await cursor.fetchone())[0]
            total_pages = (total_authors + PAGE_SIZE - 1) // PAGE_SIZE
            if not total_authors:
                return RankPage(entries=[], page=page, total_pages=0, total_authors=0)
            if page < 1 or page > total_pages:
                raise ValueError(f"Invalid page. Choose a page between 1 and {total_pages}.")
            async with db.execute(
                """WITH counts AS (
                    SELECT author_id, SUM(count) AS count
                    FROM completion_totals WHERE guild_id = ? GROUP BY author_id HAVING SUM(count) > 0
                ), ranked AS (
                    SELECT author_id, count,
                           RANK() OVER (ORDER BY count DESC) AS place
                    FROM counts
                )
                SELECT ranked.author_id,
                    (SELECT author_name FROM completion_events e WHERE e.guild_id = ?
                     AND e.author_id = ranked.author_id ORDER BY created_at DESC, source DESC, id DESC LIMIT 1) AS author_name,
                    ranked.count, ranked.place
                FROM ranked
                ORDER BY ranked.count DESC,
                    LENGTH(LTRIM(ranked.author_id, '0')), LTRIM(ranked.author_id, '0'),
                    ranked.author_id
                LIMIT ? OFFSET ?""",
                (str(guild_id), str(guild_id), PAGE_SIZE, (page - 1) * PAGE_SIZE),
            ) as cursor:
                rows = await cursor.fetchall()
            return RankPage(
                entries=[
                    RankEntry(
                        author_id=row["author_id"],
                        author_name=row["author_name"],
                        count=row["count"],
                        rank=row["place"],
                    )
                    for row in rows
                ],
                page=page,
                total_pages=total_pages,
                total_authors=total_authors,
            )

    async def statistics_editor(self, guild_id: str) -> tuple[list[dict], list[dict]]:
        async with self._transaction(write=False) as db:
            async with db.execute(
                """SELECT t.author_id, t.system, t.count,
                    (SELECT author_name FROM completion_events e WHERE e.guild_id = t.guild_id
                     AND e.author_id = t.author_id ORDER BY created_at DESC, source DESC, id DESC LIMIT 1) AS author_name
                   FROM completion_totals t WHERE guild_id = ? ORDER BY author_id, system""",
                (str(guild_id),),
            ) as cursor:
                rows = await cursor.fetchall()
            players = {}
            for row in rows:
                player = players.setdefault(
                    row["author_id"],
                    {
                        "author_id": row["author_id"],
                        "author_name": row["author_name"],
                        **dict.fromkeys(SYSTEMS, 0),
                    },
                )
                player[row["system"]] = row["count"]
            async with db.execute(
                "SELECT * FROM statistics_adjustments WHERE guild_id = ? ORDER BY id DESC LIMIT 50",
                (str(guild_id),),
            ) as cursor:
                history = [dict(row) for row in await cursor.fetchall()]
            return list(players.values()), history

    async def set_statistic(
        self,
        *,
        guild_id: str,
        author_id: str,
        author_name: str,
        system: str,
        count: int,
        expected_count: int,
    ) -> None:
        for value in (guild_id, author_id):
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
                or int(value) >= 2**64
            ):
                raise ValueError("Enter a valid Discord server and user ID.")
        if system not in SYSTEMS or type(count) is not int or not 0 <= count <= 1_000_000:
            raise ValueError("Choose a system and a completion count from 0 to 1000000.")
        if not isinstance(author_name, str) or not author_name.strip() or len(author_name) > 100:
            raise ValueError("Enter a player name (1–100 characters).")
        async with self._transaction() as db:
            async with db.execute(
                "SELECT count FROM completion_totals WHERE guild_id = ? AND author_id = ? AND system = ?",
                (guild_id, author_id, system),
            ) as cursor:
                row = await cursor.fetchone()
            current = row[0] if row else 0
            if current != expected_count:
                raise ValueError(
                    "The score changed since it was loaded. Refresh the statistics and try again."
                )
            await db.execute(
                """INSERT INTO statistics_adjustments
                (guild_id, author_id, author_name, system, before_count, after_count, delta, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    guild_id,
                    author_id,
                    author_name.strip(),
                    system,
                    current,
                    count,
                    count - current,
                    int(time.time()),
                ),
            )

    async def claim_next_due(self, now: int) -> Timer | None:
        async with self._transaction() as db:
            async with db.execute(
                """SELECT id FROM timers
                WHERE status IN ('pending', 'retry') AND due_at <= ? AND next_attempt_at <= ?
                ORDER BY due_at, id LIMIT 1""",
                (now, now),
            ) as cursor:
                candidate = await cursor.fetchone()
            if candidate is None:
                return None
            timer_id = candidate["id"]
            async with db.execute(
                """UPDATE timers SET status = 'sending', attempt_count = attempt_count + 1
                WHERE id = ? AND status IN ('pending', 'retry')
                    AND due_at <= ? AND next_attempt_at <= ?""",
                (timer_id, now, now),
            ) as cursor:
                if cursor.rowcount != 1:
                    return None
            async with db.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)) as cursor:
                return self._timer(await cursor.fetchone())

    async def update_delivery(
        self,
        timer_id: int,
        status: str,
        *,
        sent_at: int | None = None,
        message_id: str | None = None,
        next_attempt_at: int | None = None,
        last_error: str | None = None,
    ) -> None:
        if status not in {"retry", "sent", "skipped_expired", "failed", "delivery_unknown"}:
            raise ValueError(f"Invalid delivery result: {status}")
        async with self._transaction() as db:
            await db.execute(
                """UPDATE timers SET status = ?, sent_at = ?, message_id = ?,
                    next_attempt_at = COALESCE(?, next_attempt_at), last_error = ?
                WHERE id = ? AND status = 'sending'""",
                (
                    status,
                    sent_at,
                    str(message_id) if message_id is not None else None,
                    next_attempt_at,
                    last_error,
                    timer_id,
                ),
            )

    async def recover_sending(self) -> list[int]:
        async with self._transaction() as db:
            async with db.execute(
                "SELECT id FROM timers WHERE status = 'sending' ORDER BY id"
            ) as cursor:
                timer_ids = [row["id"] for row in await cursor.fetchall()]
            await db.execute(
                """UPDATE timers SET status = 'delivery_unknown',
                last_error = 'Process stopped before delivery could be confirmed.'
                WHERE status = 'sending'"""
            )
            return timer_ids

    async def backup(self, destination: Path) -> None:
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("The backup destination must differ from the active database.")
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        async with self._lock:
            async with aiosqlite.connect(str(destination)) as target:
                await self._db.backup(target, pages=100, sleep=0.01)
