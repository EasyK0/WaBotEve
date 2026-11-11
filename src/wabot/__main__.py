import argparse
import asyncio
import logging
import signal
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import discord
from filelock import FileLock, Timeout

from wabot.backup import BackupManager, restore_database
from wabot.bot import Wabot
from wabot.config import ConfigurationError, Settings
from wabot.logging_setup import configure_logging
from wabot.repository import Repository

log = logging.getLogger(__name__)


async def execute(
    args: argparse.Namespace, settings: Settings, *, stop_event: threading.Event | None = None
) -> None:
    if args.command == "restore":
        await restore_database(Path(args.source), settings.database_path, int(time.time()))
        print(
            "Database restored. Past-due reminders were skipped; completion totals are preserved."
        )
        return
    repository = Repository(settings.database_path)
    try:
        await repository.initialize()
        if args.command == "check":
            print(f"Configuration and database OK. Configured servers: {len(settings.guild_ids)}.")
        elif args.command == "backup":
            destination = await BackupManager(
                repository, settings.backup_dir, settings.offsite_backup_dir
            ).create_backup()
            print(f"Backup created: {destination}")
        else:
            client = Wabot(settings, repository)
            async with client:
                if args.command == "sync":
                    # login runs setup_hook; sync needs no workers or gateway.
                    client.setup_hook = _no_setup
                    await client.login(settings.token)
                    for guild_id in settings.guild_ids:
                        commands = await client.tree.sync(guild=discord.Object(id=int(guild_id)))
                        print(f"Registered {len(commands)} commands in server {guild_id}.")
                else:
                    loop = asyncio.get_running_loop()
                    previous_handlers = {}

                    def shutdown(signum, frame):
                        loop.call_soon_threadsafe(lambda: asyncio.create_task(client.close()))

                    for sig in (signal.SIGINT, signal.SIGTERM):
                        previous_handlers[sig] = signal.signal(sig, shutdown)
                    stop_task = (
                        asyncio.create_task(_watch_desktop_stop(client, stop_event))
                        if stop_event is not None
                        else None
                    )
                    try:
                        log.info(
                            "Starting Wabot; SQLite persistent queue and single-instance lock enabled"
                        )
                        await client.start(settings.token)
                        if client.background_failed:
                            raise RuntimeError("A background worker stopped unexpectedly.")
                    finally:
                        if stop_task is not None:
                            stop_event.set()
                            await asyncio.gather(stop_task, return_exceptions=True)
                        for sig, previous in previous_handlers.items():
                            signal.signal(sig, previous)
    finally:
        await repository.close()
        log.info("Database closed")


async def _no_setup() -> None:
    pass


async def _watch_desktop_stop(client: Wabot, stop: threading.Event) -> None:
    await asyncio.to_thread(stop.wait)
    await client.close()


def main(*, stop_event: threading.Event | None = None) -> None:
    parser = argparse.ArgumentParser(description="EVE payout reminders for Discord")
    subparsers = parser.add_subparsers(dest="command")
    for name in ("run", "sync", "check", "backup"):
        subparsers.add_parser(name)
    restore = subparsers.add_parser(
        "restore", help="Restore a backup while the bot is stopped; skip old reminders"
    )
    restore.add_argument("source", help="Path to a SQLite backup")
    args = parser.parse_args()
    args.command = args.command or "run"
    try:
        settings = Settings.from_env(require_token=args.command in {"run", "sync"})
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        configure_logging(
            settings.log_level, settings.token, settings.database_path.parent / "logs"
        )
        # Online backups can coexist with run, but cannot overlap an offline restore.
        with ExitStack() as locks:
            if args.command != "backup":
                locks.enter_context(FileLock(str(settings.database_path) + ".lock", timeout=0))
            if args.command != "run":
                locks.enter_context(
                    FileLock(str(settings.database_path) + ".maintenance.lock", timeout=0)
                )
            asyncio.run(execute(args, settings, stop_event=stop_event))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Timeout:
        print(
            "Another bot or maintenance process is using this database. Stop it first.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Never print arbitrary exception text: network errors may contain credentials.
        logging.getLogger(__name__).error(
            "Startup or maintenance failed (%s). Check configuration, database access and Discord connectivity.",
            type(exc).__name__,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
