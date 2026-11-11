"""Exercise the actual installer in an isolated profile, without Discord access."""

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import winreg
from contextlib import closing
from pathlib import Path

from wabot.desktop_support import DesktopProfile, atomic_write, json_bytes, read_config
from wabot.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{CC340B42-F94C-4F3F-B250-6148294B287F}_is1"


def run(arguments, *, timeout=60):
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(
            f"Smoke operation failed (exit {result.returncode}): {result.stdout[-1000:]} {result.stderr[-1000:]}"
        )
    return result.stdout


def main():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            raise SystemExit(
                "An installed Wabot already exists. Use a clean Windows account for installer testing."
            )
    except FileNotFoundError:
        pass
    parent = ROOT / "build" / "windows"
    parent.mkdir(parents=True, exist_ok=True)
    test = Path(tempfile.mkdtemp(prefix="desktop-smoke-", dir=parent)).resolve()
    app, data = test / "program", test / "profile"
    assert app.resolve().is_relative_to(parent.resolve())
    assert data.resolve().is_relative_to(parent.resolve())
    installer = ROOT / "dist" / "windows" / "WabotSetup.exe"
    arguments = [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/NOICONS",
        "/MERGETASKS=!desktopicon",
        f"/DIR={app}",
        f"/DataDir={data}",
        f"/LOG={test / 'install.log'}",
    ]
    report = {"installer": str(installer), "test_directory": str(test)}
    try:
        run(arguments)
        worker, launcher = app / "WabotWorker.exe", app / "WabotLauncher.exe"
        assert worker.exists() and launcher.exists()
        assert (data / "config.json").exists()
        assert read_config(data / "config.json") == read_config(
            ROOT / "build" / "windows" / "seed" / "config.json"
        )
        report["configuration_preserved"] = True
        report["runtime"] = json.loads(run([str(worker), "--self-test"]))
        assert report["runtime"]["migration"] is True
        assert report["runtime"]["catalog_items"] > 19000
        assert "Configuration and database OK" in run(
            [str(worker), "--worker", "check", "--data-dir", str(data)]
        )
        report["offline_database_check"] = True
        with closing(sqlite3.connect(data / "data" / "bot.sqlite3")) as database:
            assert database.execute("PRAGMA user_version").fetchone()[0] == 3
            assert database.execute("SELECT COUNT(*) FROM completion_totals").fetchone() is not None
        report["schema_version"] = 3
        run([str(worker), "--smoke-gui", "--data-dir", str(data)], timeout=15)
        run([str(launcher), "--smoke-gui", "--data-dir", str(data)], timeout=15)
        report["launcher_gui"] = True
        profile = DesktopProfile(data)
        config = read_config(profile.config_path)
        config["market"]["interval_minutes"] = 75
        config["pvp"] = {
            "enabled": True,
            "ping": "none",
            "role_id": None,
            "system_channels": {"Kino": "456", "Niarja": "456", "Archee": "789"},
        }
        config["messages"] = {"timer_reminder": "Check {system}! Timer {timer_id}"}
        profile.save(config)
        assert "Configuration and database OK" in run(
            [str(worker), "--worker", "check", "--data-dir", str(data)]
        )
        run([str(launcher), "--smoke-gui", "--data-dir", str(data)], timeout=15)
        report["pvp_and_custom_messages"] = True

        async def check_statistics():
            repository = Repository(profile.database_path)
            await repository.initialize()
            try:
                guild = config["guild_ids"][0]
                before = (await repository.stats(guild))["total"]
                await repository.set_statistic(
                    guild_id=guild,
                    author_id="900000000000000001",
                    author_name="Smoke pilot",
                    system="kino",
                    count=4,
                    expected_count=0,
                )
                assert (await repository.stats(guild))["total"] == before + 4
                assert len((await repository.statistics_editor(guild))[1]) >= 1
            finally:
                await repository.close()

        asyncio.run(check_statistics())
        report["statistics_corrections"] = True
        run(arguments)
        assert read_config(profile.config_path)["market"]["interval_minutes"] == 75
        assert read_config(profile.config_path)["messages"] == config["messages"]
        assert read_config(profile.config_path)["pvp"] == config["pvp"]
        report["reinstall_preserves_user_changes"] = True
    finally:
        uninstaller = app / "unins000.exe"
        if uninstaller.exists():
            # Only uninstall our freshly created instance and its verified directory.
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                registered, _ = winreg.QueryValueEx(key, "InstallLocation")
            assert Path(registered).resolve() == app.resolve()
            assert uninstaller.resolve().is_relative_to(test)
            run(
                [
                    str(uninstaller),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    f"/LOG={test / 'uninstall.log'}",
                ]
            )
    assert (data / "config.json").exists() and (data / "data" / "bot.sqlite3").exists()
    report["uninstall_preserves_data"] = True
    assert not (app / "WabotLauncher.exe").exists()
    atomic_write(ROOT / "dist" / "windows" / "smoke-report.json", json_bytes(report))
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Windows only")
    main()
