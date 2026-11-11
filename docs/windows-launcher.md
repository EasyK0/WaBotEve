# Windows installer and launcher

`WabotSetup.exe` installs Wabot for the current Windows user. No administrator rights are needed. Python and every library are bundled, so nothing is downloaded during installation. The bot itself needs an internet connection.

Reinstalling keeps existing settings, the database and the saved token. Uninstalling removes the program and shortcuts, but keeps your data in `%LOCALAPPDATA%\Wabot`.

## First start

1. Open **Wabot Launcher** from the desktop or the Start menu.
2. On the **Bot token** page, paste the bot token and click **Save token**. You get the token in the [Discord Developer Portal](https://discord.com/developers/applications), on the *Bot* page of your application. The application ID is not the token.
3. Add your Discord server with **+ Add server**, then choose the features for it on **Server features**.
4. Click **▶ Start bot**. On the first start, and after a configuration change, the launcher registers slash commands automatically. The status turns to *Bot running* once the bot is connected to Discord.

**■ Stop** and closing the launcher both stop the bot after it finishes the current work. The bot only runs while the launcher and the PC are on; for 24/7 operation use the [Docker deployment](../README.md#deploying-with-docker). **Check installation** validates the configuration and database without connecting to Discord. Logs are shown in the window and saved under `data/logs` in the data folder.

The token is encrypted with [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) for your Windows account, and it is redacted from the logs.

## Watching EVE items

1. Stop the bot and open the **Market** page.
2. Type an English item name, part of one, or a type ID. The search covers the whole catalog; past 200 results, narrow the name down.
3. Select a result and click **Add to watched →** (or double-click it). **Remove from watched** takes an item out of the reports.
4. Start the bot. New items appear in the next report with the current price thresholds, channel and interval.

The catalog comes from the [official CCP Static Data Export](https://developers.eveonline.com/docs/services/static-data/) and lists every published item type, including ones that can't be traded. Only tradeable items can be added to the order analysis. A copy ships with the program, so search works offline. **Update catalog** downloads the current CCP data in the background (about 100 MB) and keeps a compact catalog in the data folder. If the download fails, the previous catalog stays in use.

## Moving the bot to another PC

1. Stop the bot on the old PC.
2. On the **Move bot** page, click **Export data and settings** and save the `.wabot.zip` package.
3. Install Wabot on the new PC, click **Import data package**, then **▶ Start bot**.

> **The package contains the database, the configuration and the bot token in plain form.** Treat it like a password: don't share it, and delete it after importing. The import encrypts the token again for the Windows account on the new PC. Never copy `token.dpapi` between computers, because it only works for the account that created it.

**Import old bot folder** takes a folder with `config.json`, `data/bot.sqlite3` and an optional private `.env`. Before replacing an existing profile, the launcher saves `backups/before-import-*.wabot.zip`. Overdue reminders are skipped during the import, and statistics are kept. Run only one copy of the bot per database.

## Building the installer

On a Windows machine with Python 3.11/3.12 and [Inno Setup 6](https://jrsoftware.org/isdl.php):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_windows.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

The script packages the app with [PyInstaller](https://pyinstaller.org/en/stable/spec-files.html) and builds a [per-user Inno Setup installer](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm). It also writes a SHA-256 checksum and a `build-info.json` without secrets. Output goes to the ignored `build/` and `dist/` folders.

The build can seed the installer with your current `config.json` and `data/bot.sqlite3`. It reads a token only from a private `.env`, the `DISCORD_TOKEN` variable or `--token-file`, and never from `.env.example`. **An installer built with a token is a private file: never publish it or attach it to a GitHub release.** The installer is not code-signed.
