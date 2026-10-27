"""Frozen launcher/worker entry; the bot always runs in a separate process."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from wabot.desktop_support import APP_VERSION, DesktopProfile


def _stdin_control(stop: threading.Event) -> None:
    # EOF also stops the bot if its owning launcher has exited unexpectedly.
    for line in sys.stdin:
        if line.strip() == "STOP":
            break
    stop.set()


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Wabot Launcher")
    parser.add_argument("--worker", choices=("run", "sync", "check", "backup"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--bootstrap", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-gui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    profile = DesktopProfile(args.data_dir.resolve()) if args.data_dir else DesktopProfile.default()
    if args.self_test:
        import ssl
        import tkinter
        from importlib.resources import files

        from wabot.item_catalog import BUNDLED_CATALOG, ItemCatalog

        print(
            json.dumps(
                {
                    "version": APP_VERSION,
                    "tk": tkinter.TkVersion,
                    "ssl": ssl.OPENSSL_VERSION,
                    "migration": files("wabot.migrations").joinpath("001_initial.sql").is_file(),
                    "catalog_items": len(ItemCatalog.read(BUNDLED_CATALOG).items),
                }
            )
        )
    elif args.bootstrap:
        profile.bootstrap(args.bootstrap)
    elif args.worker:
        from wabot.__main__ import main as bot_main

        environment = profile.environment()
        os.environ.clear()
        os.environ.update(environment)
        sys.argv = [sys.argv[0], args.worker]
        stop = threading.Event() if args.worker == "run" else None
        if stop is not None:
            threading.Thread(target=_stdin_control, args=(stop,), daemon=True).start()
        bot_main(stop_event=stop)
    else:
        from wabot.desktop_launcher import launch

        launch(profile, smoke=args.smoke_gui)


if __name__ == "__main__":
    main()
