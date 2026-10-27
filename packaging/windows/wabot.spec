from pathlib import Path

root = Path(SPECPATH).resolve().parents[1]
a = Analysis(
    [str(root / "src" / "wabot" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    datas=[
        (str(root / "src" / "wabot" / "migrations" / "*.sql"), "wabot/migrations"),
        (str(root / "src" / "wabot" / "assets" / "*.json.gz"), "wabot/assets"),
        (str(root / "docs" / "windows-launcher.md"), "."),
        (str(root / "docs" / "pochven-and-messages.md"), "."),
    ],
    hiddenimports=["wabot.migrations"],
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
launcher = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WabotLauncher",
               console=False, upx=False)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WabotWorker",
             console=True, upx=False)
COLLECT(launcher, worker, a.binaries, a.datas, name="Wabot", upx=False)
