# PvP feeds, channel tests, messages and statistics

## Pochven killmails

Stop the bot and open the **PvP** page. Enter a channel ID for Kino, Niarja, Archee or **All Pochven** (every Pochven system). Several fields can share one channel, and an empty field turns that system off. To get a channel ID in Discord, right-click the channel and choose *Copy Channel ID*. Choose the ping (*No ping*, *@everyone* or a *Selected role*) and click **Save PvP settings**.

While it runs, the bot checks zKillboard's R2Z2 feed every **30 seconds**. Each killmail gets its own message with the system, the UTC time and a zKillboard link, which Discord usually expands into a card. There is no loss threshold or cooldown, so pods and NPC kills are included too. Kills older than one hour are skipped, so a restart doesn't replay the backlog.

The channel needs *View Channel*, *Send Messages* and *Embed Links*. An `@everyone` ping also needs *Mention Everyone*, and a role ping needs permission to mention that role. New configurations default to no ping.

The feed only contains killmails published on zKillboard. The 30 seconds is the polling interval, not a promise about how soon after a kill the message appears: publishing delays, network errors and rate limits can make it longer. The read cursor and a duplicate guard are stored in SQLite. A send whose result is unknown (for example after a lost response) is not retried, to avoid double posts.

## Alliance kills and losses

Under **Alliance kills and losses** on the same page, enter alliance IDs and a channel. Their kills and losses from anywhere in EVE are posted as cards with the victim, ship, value and attackers. Two thresholds add an `@here`: one for alliance kills above an ISK value, and a separate one for alliance losses.

## Channel tests

- **PvP → Send test to saved channels** sends a clearly marked **WABOT TEST** message to every saved PvP channel.
- **Server features → Channel test → Send test** covers every saved channel of the server, including the market report channel.

A channel shared by several systems gets one message listing its assignments. Tests never ping anyone, work while the bot is running, and use the *saved* settings, not unsaved edits. The result for each channel shows under the button and in the log, and one failing channel doesn't stop the rest. Tests don't touch timers, statistics or killmail history.

## Editing messages

**Timers → Messages / pings** edits the 23-minute reminder, the `/timer` confirmation, `/stats`, `/rank` and the market report descriptions. The available placeholders are listed above the editor. The preview uses sample data and sends nothing. Message texts are shared by all servers. Save, then restart the bot. Killmail posts always keep the plain zKillboard link.

## Correcting statistics

Stop the bot and open **Timers → Statistics / leaderboard**. Pick the server and refresh. Then select a player, pick Kino, Niarja or Archee, enter the correct total and click **Save correction**. Enter `0` to reset the player for that system. **Add player manually** takes a Discord user ID and display name for someone with no timer records yet.

Corrections show in both `/stats` and `/rank`, and future completions add on top of the corrected value. Players with zero completions are left off the leaderboard. A correction affects only the selected server and system, and never creates, deletes or reschedules reminders. If another completion changed the count after the launcher read it, the edit is rejected as stale.

**Recent corrections** shows the last 50 changes with UTC time and before/after values. The full history is kept in SQLite, so it is included in backups and data exports. To undo a correction, set the value back. The database upgrades itself from older versions; don't run an older version of the program against an upgraded database.

## Upgrading older configurations

Older single-server configurations are converted automatically. An old PvP `channel_id` becomes a shared channel for Kino, Niarja and Archee when no per-system channels exist. Old thresholds, cooldowns and unused summary-alert templates are ignored and removed at the next save. The current per-server format is:

```json
{
  "id": "123456789012345678",
  "name": "My corp",
  "features": { "timers": true, "market": true, "pvp": true },
  "market": { "channel_id": "223456789012345678" },
  "pvp": {
    "ping": "none",
    "role_id": null,
    "system_channels": { "Kino": "323456789012345678", "Niarja": "323456789012345678" },
    "pochven_channel": null,
    "alliance_ids": [],
    "alliance_channel": null,
    "here_min_value": 1000000000,
    "alliance_here_loss_value": 2000000000
  }
}
```
