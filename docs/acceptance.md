# Acceptance test plan

## Automated tests

Run `python -m pytest -q` with `requirements-dev.txt` and the package installed. The suite covers:

- UTC handling, strict `HH:MM` parsing, whole minutes, crossing midnight and new year, and the 23-minute boundary.
- Private error replies, permission checks, plain text channels only, and public confirmations without mentions.
- Atomic, idempotent writes: concurrent calls, rollback, write failure and restart.
- Isolated servers, every ping status, author renames, a 12-player leaderboard and ties across pages.
- On-time pings, 30/120/300-second delays, and skipping after 301/360 seconds.
- Retries, `429 retry_after`, timeouts with an unknown result, missing channel or permissions, and no repeated ping after a restart.
- A full cycle: payout 20:20 Kino → restart → ping 20:43 → payout 20:50 Archee → ping 21:13, with final statistics Kino 1, Archee 1, total 2.
- Backups of a live WAL database: seven copies, an off-site copy, safe restore and rejection of a bad snapshot.

## On a Discord server (after configuration)

Use a separate test server: these tests post real `@everyone` messages. Create a plain text channel, give the bot *View Channel*, *Send Messages* and *Mention Everyone*, and sync the commands.

1. At 20:25 EVE time, run `/timer time:20:20 system:Kino`. The confirmation should show payout 20:20, respawn 20:43, a Timer ID and +1, with no ping.
2. Run `/stats` and `/rank`. Note the totals and confirm that only this author and Kino went up by one.
3. Restart the bot before 20:43. At 20:43 (normally within 10 seconds), expect exactly one `@everyone` asking to check Kino, Niarja and Archee. The ping itself doesn't change the totals.
4. After another completion in Archee with payout 20:50, run `/timer time:20:50 system:Archee`. Expect a new Timer ID, +1 for Archee and this author, and another reminder at 21:13.
5. Submit an invalid time and an overdue payout: each should get a private error and leave the totals unchanged. Without *Mention Everyone* before saving, the timer is refused.
6. Remove *Mention Everyone* only after the next timer is accepted. At the due time, a notice without the ping should appear, with the Timer ID and a warning in the log. Statistics stay.
7. Stop the bot and start it 2 minutes after a due time: expect `Delayed reminder`. Repeat with 6 minutes: no ping, `skipped_expired` in the log, and the completion still counts.
8. Check isolation on a second configured server, nickname changes, that names containing mentions don't ping, and that commands don't work in threads or DMs.

You can use current UTC times instead of the ones above, as long as you keep the 23-minute gap after the payout.

## 24/7 hosting (on the VPS)

- Run Compose on the external host. Check the persistent volume, the single replica and the restart policy.
- Turn the owner's PC off and leave the VPS running. From another device, run `/timer` and confirm the reminder arrives later.
- Restart the VPS and check that timers and data survive and that no second worker starts.
- Check the latest backup log and the actual off-site copy. Restore a snapshot onto a separate test volume: old reminders must be skipped and totals kept.
- For every step, record the date, server, Timer ID, scheduled and actual time, and the result.

Don't sign off a deployment before these checks pass. Local tests can't prove behaviour on real Discord, or that the bot keeps running with the owner's PC switched off.
