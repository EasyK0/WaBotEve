"""Consistent snapshots and offline restoration of the durable timer queue."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Timer
from .repository import Repository

logger = logging.getLogger(__name__)
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
_BACKUP_NAME = re.compile(r"wabot-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3")


def _validate_database(path: Path) -> None:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("The backup failed SQLite integrity validation.")
        columns = {row[1] for row in db.execute("PRAGMA table_info(timers)")}
        if not {field.name for field in fields(Timer)}.issubset(columns):
            raise ValueError("The backup does not contain the expected timers schema.")


def _fsync_file(path: Path) -> None:
    # Windows requires a writable descriptor for FlushFileBuffers/os.fsync.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _rotate(directory: Path, keep: int) -> None:
    snapshots = sorted(
        path
        for path in directory.iterdir()
        if _BACKUP_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
    )
    for obsolete in snapshots[:-keep]:
        obsolete.unlink()


def _publish_copy(source: Path, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    temporary = directory / f".{source.name}.{uuid4().hex}.tmp"
    try:
        shutil.copyfile(source, temporary)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)


class BackupManager:
    """Create a startup snapshot, then one each day; retain at least seven."""

    def __init__(
        self,
        repository: Repository,
        directory: Path,
        offsite_directory: Path | None = None,
        keep: int = 7,
    ) -> None:
        if keep < 7:
            raise ValueError("At least seven backup snapshots must be retained.")
        self.repository = repository
        self.directory = Path(directory)
        self.offsite_directory = Path(offsite_directory) if offsite_directory else None
        if self.offsite_directory and self.directory.resolve() == self.offsite_directory.resolve():
            raise ValueError("Offsite backups need a separate destination directory.")
        self.keep = keep
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._running = False

    async def create_backup(self) -> Path:
        async with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            destination = self.directory / f"wabot-{stamp}-{uuid4().hex[:8]}.sqlite3"
            temporary = destination.with_suffix(".tmp")
            try:
                await self.repository.backup(temporary)
                await asyncio.to_thread(_validate_database, temporary)
                await asyncio.to_thread(_fsync_file, temporary)
                os.replace(temporary, destination)
                await asyncio.to_thread(_fsync_directory, self.directory)
            finally:
                temporary.unlink(missing_ok=True)
            logger.info("Local database backup created: %s", destination.name)
            if self.offsite_directory:
                await asyncio.to_thread(_publish_copy, destination, self.offsite_directory)
                await asyncio.to_thread(_rotate, self.offsite_directory, self.keep)
                logger.info("Offsite database backup copied: %s", destination.name)
            await asyncio.to_thread(_rotate, self.directory, self.keep)
            return destination

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("The backup worker is already running.")
        self._running = True
        try:
            while not self._stop.is_set():
                try:
                    await self.create_backup()
                except Exception as exc:
                    logger.error(
                        "Database backup failed (%s); retrying in one hour.", type(exc).__name__
                    )
                    interval = 3600
                else:
                    interval = BACKUP_INTERVAL_SECONDS
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        finally:
            self._running = False

    def stop(self) -> None:
        """Wake the daily wait. The caller should await the worker before closing SQLite."""
        self._stop.set()


def _clone_database(source: Path, destination: Path) -> None:
    _validate_database(source)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as restored:
            original.backup(restored)


def _finish_restore(temporary: Path, destination: Path, now: int) -> int:
    with closing(sqlite3.connect(temporary)) as restored:
        with restored:
            suppressed = restored.execute(
                "UPDATE timers SET status = 'skipped_expired', "
                "last_error = 'Suppressed during backup restoration' WHERE due_at <= ?",
                (now,),
            ).rowcount
        restored.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        restored.execute("PRAGMA journal_mode=DELETE")
    _validate_database(temporary)
    _fsync_file(temporary)

    # A crashed process can leave WAL files. Recover/checkpoint them through SQLite
    # before replacing the main file. The caller MUST already hold the process lock.
    sidecars = [Path(str(destination) + suffix) for suffix in ("-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        if not destination.exists():
            raise ValueError(
                "Orphaned destination WAL files require manual recovery before restore."
            )
        with closing(sqlite3.connect(destination, timeout=5)) as old:
            result = old.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result and result[0]:
                raise RuntimeError("The destination database is still in use; stop the bot first.")
            old.execute("PRAGMA journal_mode=DELETE")
        for sidecar in sidecars:
            sidecar.unlink(missing_ok=True)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)
    return suppressed


async def restore_database(source: Path, destination: Path, now: int) -> None:
    """Restore offline. The CLI must own the bot's exclusive process lock first.

    Work on a temporary database, check/migrate it, and suppress every past-due
    reminder before replacement. Reports remain present for stats and rankings.
    """
    source = await asyncio.to_thread(Path(source).resolve)
    destination = await asyncio.to_thread(Path(destination).resolve)
    if source == destination:
        raise ValueError("The backup source and live database must be different files.")
    if not source.is_file():
        raise ValueError("The selected backup file does not exist.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".wabot-restore-", suffix=".sqlite3", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        await asyncio.to_thread(_clone_database, source, temporary)
        repository = Repository(temporary)
        try:
            await repository.initialize()
        finally:
            await repository.close()
        suppressed = await asyncio.to_thread(_finish_restore, temporary, destination, int(now))
        logger.info(
            "Database restored; %s past-due reminders suppressed; completion reports preserved.",
            suppressed,
        )
    finally:
        await asyncio.to_thread(temporary.unlink, missing_ok=True)
        for suffix in ("-wal", "-shm"):
            await asyncio.to_thread(Path(str(temporary) + suffix).unlink, missing_ok=True)
