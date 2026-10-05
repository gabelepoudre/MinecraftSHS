# self host windows minecraft bedrock server

Self updating/downloading so long as the scraper works

You must also run the command `CheckNetIsolation.exe LoopbackExempt –a –p=S-1-15-2-1958404141-86561845-1752920682-3514627264-368642714-62675701-733520436` as admin in cmd

Config for storage found in .env, conda environment in environment.yml

You'll need to try to start once, and then use the download in `/active/current` to change your server.properties. These carry over updates

I'm sure there is lots of missing QOL and outright bugs, but it works for me

### Backups

World backups are taken hourly to `MC_BACKUP_DIR/<level-name>/<YYYY-mm-dd_HH-MM-SS>.zip` (written as `.partial` and
renamed when complete). If the world has not changed since the last backup (compared by file path, size and mtime,
stored in `last_manifest.json`), the backup is skipped. Old backups are pruned after every new backup; files that do
not match the naming pattern are never deleted.

Update backups go to `MC_BACKUP_DIR/updates/<timestamp>_<old>_to_<new>.zip` (old `<old>_to_<new>.zip` files are still
recognised, using their modification time) and are pruned after a successful update. They exclude root level
`.exe`/`.dll`/`.pdb` files, which the update recreates from the downloaded version.

Retention is configured with these optional env vars (see `.env.template`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `MC_BACKUP_KEEP_ALL_HOURS` | 48 | keep every world backup newer than this many hours |
| `MC_BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY` / `_YEARLY` | 7 / 4 / 6 / 2 | newest backup in each of the N most recent days / ISO weeks / months / years that have one |
| `MC_UPDATE_BACKUP_KEEP_LAST` | 6 | newest update backups, always kept |
| `MC_UPDATE_BACKUP_KEEP_QUARTERLY` / `_HALF_YEARLY` / `_YEARLY` | 4 / 2 / 3 | thinned update backup tiers (one per 3 months / 6 months / year) |
| `MC_KEEP_DOWNLOADED_VERSIONS` | 5 | downloaded server versions kept |
| `MC_BACKUP_PRUNE_DRY_RUN` | false | only log what would be deleted |
| `MC_BACKUP_MANIFEST_IGNORE` | empty | comma separated patterns of files ignored by change detection |

A backup is kept if any rule claims it, the newest backup is never deleted, and an empty policy keeps everything.

### Admin alerts

A simple way to surface critical problems. Every CRITICAL log record (server process died, update failed, ...) and
sustained update check failures (by default 6 consecutive failures to find or download an update, after which one
CRITICAL is logged) raise an alert to up to two places:

1. In game, as a short `say` message (`[Admin alert] ...`, one line, about 100 characters), if the server is running.
2. A JSON POST to a webhook, **only if `MC_ALERT_WEBHOOK_URL` is set**. It is not set by default, so by default alerts
   are in-game only and no HTTP request is ever made.

Enable the webhook by adding `MC_ALERT_WEBHOOK_URL=https://...` to `.env`. The request body is JSON
(`Content-Type: application/json`):

```json
{
  "content": "[CRITICAL] mc.update: Unexpected exception during update",
  "text": "[CRITICAL] mc.update: Unexpected exception during update",
  "level": "CRITICAL",
  "logger": "mc.update",
  "message": "Unexpected exception during update",
  "host": "MC-SERVER",
  "server_version": "1.26.52.3",
  "timestamp": "2026-10-01T04:00:00+00:00",
  "repeat_count": 1,
  "traceback": null
}
```

`content` works with Discord webhooks and `text` with Slack and Teams style incoming webhooks (both hold the same short
message, `content` is capped at 1900 characters); use the other fields if you write your own receiver. `traceback` is
the last 3000 characters of the exception text, if there was one.

Alerts are sent from a background thread with a short timeout and a couple of retries, so a dead webhook never blocks
the server, and webhook failures are not logged as alerts. Identical alerts (same logger and message) inside the
cooldown window are suppressed; the next one that gets through reports how many occurred in `repeat_count`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MC_ALERT_WEBHOOK_URL` | unset | webhook endpoint, unset or empty disables the webhook |
| `MC_ALERT_MIN_LEVEL` | CRITICAL | `ERROR` or `CRITICAL` |
| `MC_ALERT_IN_GAME` | true | show alerts in game |
| `MC_ALERT_COOLDOWN_MINUTES` | 30 | suppress identical alerts for this long (0 disables) |
| `MC_ALERT_SCRAPE_FAILURE_THRESHOLD` | 6 | consecutive update check/download failures before a CRITICAL |

### Event log

An append-only record of what happened, for statistics later (playtime, crashes, ...). One JSON object per line, one
file per UTC day: `MC_EVENTS_DIR/YYYY-MM-DD.jsonl`, by default `data/events/` (`MC_DATA_DIR/events`). The directory is
created if missing. Files are kept forever; they are small (a busy day is a few kilobytes).

```json
{"v": 1, "ts": "2026-10-05T12:00:00.000+00:00", "type": "player_join", "source": "console", "player": "Steve", "xuid": "2535412345678901"}
```

Every event has `v` (schema version), `ts` (UTC, ISO 8601), `type` and `source` (`console`: parsed from the server
output, `app`: this wrapper). Adding a field keeps `v`; renaming a field or changing its meaning bumps it. All events
are written by `emit()` in `mc/events.py`, where the event types are listed; a failure to write is logged at ERROR and
never stops the server.

| Type | Source | Fields | When |
| --- | --- | --- | --- |
| `player_join` | console | `player`, `xuid` | `Player connected: ...` (`xuid` is null if the console shows none) |
| `player_leave` | console | `player`, `xuid` | `Player disconnected: ...` |
| `server_ready` | console | | `Server started.`, players can join |
| `achievement` | console | `player`, `achievement` | unverified: the console line format is a guess and may never match |
| `app_start` | app | | the wrapper started |
| `app_exit` | app | `reason`: `stop_command`, `keyboard_interrupt`, `error` | the wrapper is exiting (nothing is written if it is killed) |
| `server_start` | app | `version` | the server process was started |
| `server_stop` | app | `reason`: `daily_restart`, `update`, `manual`, `exit` | the server was stopped on purpose |
| `server_crash` | app | `version`, `exit_code` | the server process died on its own (it is restarted) |
| `restart_scheduled` | app | `reason`: `daily_restart`, `update`; `countdown_seconds` | the 15 minute restart warning started |
| `update_available` | app | `from_version`, `to_version` | a newer downloaded version will be installed (restart follows) |
| `update_installed` | app | `from_version` (null on first install), `to_version` | an update finished |
| `update_failed` | app | `from_version`, `to_version`, `error` | an update raised an error |
| `backup_completed` | app | `level`, `skipped` (true if the world was unchanged) | hourly or pre-restart world backup |
| `backup_failed` | app | `level`, `error` | a world backup raised an error |

A `server_stop` or `server_crash` (or a gap after the last app event) ends every open player session. The console
formats come from known BDS output and have not yet been checked against a live server.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MC_EVENTS_DIR` | `MC_DATA_DIR/events` | where the event log is written |

Tests: `python -m pytest tests`

### TODO
- ~~auto 4:00am restarts (with backup just in case)~~ (done, set `MC_DAILY_RESTART_UTC`, e.g. `04:00`, in .env)
- ~~delete runtime backups more than 48 hours old~~ (superseded by tiered retention, see Backups)
- ~~update backups need to be sorted by world name~~ (superseded: update backups are timestamped and pruned, see Backups)
- ~~arbitrary on-start commands~~ (done, edit `startup_commands.txt`, created from `startup_commands.template.txt` on first start; `$$sleep(N)` waits N seconds; path via `MC_STARTUP_COMMANDS_FILE`)
- no internet at startup causes a restart loop: in online mode BDS waits about a minute for Minecraft services, then stops itself ("Could not connect to Minecraft services"), which `maintain_loop` treats as a crash and restarts, repeating `server_crash` events and CRITICAL alerts until the connection returns. Detect that log line and back off (and alert once) instead. See `plans/headless-listener-feasibility.md`
