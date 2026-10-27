# Changelog

All notable changes to Wabot. Versions follow `MAJOR.MINOR.PATCH`.

## [1.10.0] - 2026-10-27

- Market analysis: 30-day verdict per item from ESI price history.
- New `/market-analysis` command for everyone, with a public reply.
- Verdict line in the market report and persistent 📈 buttons under it; a click shows the analysis privately.
- New "Market analysis" page in the launcher with a price chart, 5-minute refresh and saved sales tax / broker fee.

## [1.9.0] - 2026-10-25

- "All Pochven" feed: every Pochven kill can go to one channel.

## [1.8.1] - 2026-10-19

- Kills older than 1 hour are skipped, so a restart does not replay the feed backlog.

## [1.8.0] - 2026-10-19

- All bot, launcher and installer texts translated to English.

## [1.7.2] - 2026-10-18

- The PvP tab now has per-system entries.

## [1.7.1] - 2026-10-18

- Per-server alliance killmail feed with value thresholds.

## [1.7.0] - 2026-10-18

- One bot serves many Discord servers; timers, market and PvP can be turned on per server.
- Old single-server settings move over automatically.
- Redesigned launcher with a sidebar and server picker.

## 1.6 and earlier

- Pochven killmails every 30 seconds, system-to-channel assignments, channel test messages.
- Fix: fresh kills were skipped when the PC clock was behind.
