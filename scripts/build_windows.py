"""Build the personal Windows installer from this bot's existing configuration."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from wabot.desktop_support import (
    APP_VERSION,
    atomic_write,
    json_bytes,
    read_config,
    read_env_token,
    snapshot_database,
    validate_token,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Build WabotSetup.exe with current settings/data")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "bot.sqlite3")
    parser.add_argument("--token-file", type=Path, help="Private .env containing DISCORD_TOKEN")
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Build this Windows installer on Windows.")
    config = read_config(args.config)
    token = read_env_token(args.token_file or ROOT / ".env")
    if not token:
        token = validate_token(os.environ.get("DISCORD_TOKEN", ""))
    seed = ROOT / "build" / "windows" / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    atomic_write(seed / "config.json", json_bytes(config))
    if args.database.is_file():
        snapshot_database(args.database, seed / "bot.sqlite3")
    else:
        (seed / "bot.sqlite3").unlink(missing_ok=True)
    if token:
        atomic_write(seed / "credentials.json", json_bytes({"discord_token": token}))
    else:
        (seed / "credentials.json").unlink(missing_ok=True)
    output = ROOT / "dist" / "windows"
    output.mkdir(parents=True, exist_ok=True)
    if not args.skip_freeze:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--log-level",
                "WARN",
                "--distpath",
                str(output),
                "--workpath",
                str(ROOT / "build" / "windows" / "freeze"),
                str(ROOT / "packaging" / "windows" / "wabot.spec"),
            ],
            cwd=ROOT,
            check=True,
        )
    candidates = [
        args.iscc,
        ROOT / "build" / "installer-tools" / "inno" / "ISCC.exe",
        Path(shutil.which("ISCC.exe") or "missing"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
    ]
    compiler = next((path for path in candidates if path and path.is_file()), None)
    if compiler is None:
        raise SystemExit("Install Inno Setup 6 or pass --iscc pointing to ISCC.exe.")
    subprocess.run(
        [
            str(compiler),
            "/Q",
            f"/DPayloadDir={output / 'Wabot'}",
            f"/DSeedDir={seed}",
            f"/DOutputPath={output}",
            str(ROOT / "packaging" / "windows" / "wabot.iss"),
        ],
        check=True,
    )
    installer = output / "WabotSetup.exe"
    with installer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    atomic_write(output / "WabotSetup.sha256", f"{digest}  WabotSetup.exe\n".encode())
    atomic_write(
        output / "build-info.json",
        json_bytes(
            {
                "version": APP_VERSION,
                "sha256": digest,
                "token_included": bool(token),
                "database_included": args.database.is_file(),
                "market_interval_minutes": config.get("market", {}).get("interval_minutes", 60),
            }
        ),
    )
    shutil.copyfile(ROOT / "docs" / "windows-launcher.md", output / "Instrukcja.md")
    shutil.copyfile(ROOT / "docs" / "pvp-and-messages.md", output / "pvp-and-messages.md")
    print(f"Installer ready: {installer}")
    print(f"Current settings included; token included: {bool(token)}")


if __name__ == "__main__":
    main()
