# Server events: event log, add-on, and statistics

Status: plan only, not started.

Goal: a rolling, append-only event log is the foundation and the single source of truth. Everything else (statistics, playtime, feeds) is derived from it later, on demand. Build it in three phases, each usable on its own:

1. **Event log** from what we already have: the server console (connects, disconnects, achievements) and the app's own lifecycle (start, stop, crash, restart, update, backup). No add-on.
2. **Add-on** as an extra event source writing into the same log: deaths with cause and killer, spawns, and other stable Script API events. No chat.
3. **Parsers and statistics**: playtime, deaths, downtime, etc., computed by replaying the log files. Auxiliary; built when wanted.

## Event log format (shared by all phases)

- Location: one file per UTC day, `YYYY-MM-DD.jsonl` (matches the existing daily server logs). The directory is configurable: `MC_EVENTS_DIR` env var if set, otherwise `<repo>/data/events/` (already git-ignored by the `/data/*` rule). Resolved by a `get_path_to_events_dir()` helper in `mc/paths.py` following the same pattern as `get_path_to_data_dir()` (strip quotes, normalise, cache), creating the directory if missing. Documented in `.env.template`.
- One JSON object per line, never multi-line. Common fields: `"v":1` (schema version), `"ts"` (UTC ISO 8601), `"type"`, `"source"` (`console`, `app`, `addon`). Type-specific fields alongside, e.g. `{"v":1,"ts":"...","type":"player_join","source":"console","player":"Steve","xuid":"2535..."}`.
- Kept indefinitely by default; the files are tiny (a busy day is kilobytes) and the history is what statistics depend on. Optional `MC_EVENT_LOG_KEEP_DAYS` (default 0 = keep forever).
- **One central emitter.** Every event, whatever its source, goes through a single function in `mc/events.py`: `emit(event_type, source, **fields)`. It stamps `v` and `ts`, serialises, and appends to the right day's file, thread-safe (lock + append + flush per line, like `ThreadSafeFileLogger`). A write failure logs at ERROR and never interrupts the server. Nothing else writes event files.
- **Readable call sites.** Console parsing and app actions differ only in how they decide to call `emit`; the call itself looks the same everywhere, e.g. `events.emit("server_crash", "app", version=v)`. Event type names live as constants in one place in `mc/events.py` (with a short comment per type listing its fields) so a reader can see every event the system produces in one screen. No per-type wrapper functions unless one is genuinely clearer.
- Document every event type and its fields in a schema section of the README. Adding a field is fine; renaming or changing meaning bumps `v`.

## Phase 1: event log from existing sources

### Step 1.0: console line samples (no live server)

We cannot currently run and connect to a server for testing, so phase 1 is built and verified entirely with automated tests and mocks. Use the known BDS console formats (e.g. `[2026-10-05 12:00:00:000 INFO] Player connected: Steve, xuid: 2535...`, `Player disconnected: Steve, xuid: ..., pfid: ...`, `Server started.`) as test fixtures, keeping the regexes tolerant of prefix variations. Mark the achievement pattern as unverified. Once the code runs on the live server, compare the real lines to the fixtures and adjust.

### Step 1.1: console events

- In `ServerRuntime.__stdout_packer` (`mc/server_runtime.py`), pass each line to a parser in `mc/events.py` after the existing raw log write (raw logging stays unchanged). Regexes tolerant of the BDS timestamp/level prefix.
- Events: `player_join` and `player_leave` (name, xuid), `achievement` (player, achievement text, if the console provides it), `server_ready` (the "Server started" line).
- Unknown lines are ignored; a line that matches a pattern but fails to parse logs at DEBUG.

### Step 1.2: app lifecycle events (`source: "app"`)

Emitted from the existing code paths in `run_mc_server.py` and `mc/server_runtime.py`:

- `server_start` (version), `server_stop` (reason: `daily_restart`, `update`, `manual`, `exit`), `server_crash` (when the process dies unexpectedly in `maintain_loop`).
- `restart_scheduled` (reason, countdown), `update_available` (from/to version), `update_installed`, `update_failed`.
- `backup_completed` / `backup_failed`.
- `app_start` / `app_exit`, so gaps where the whole app was down are visible.

A `server_stop` or `server_crash` implicitly ends every open player session; phase 3 relies on this, so there is no need for heartbeats or a database to keep playtime crash-safe.

### Step 1.3: tests and docs

- Unit tests (pytest, temp directories, no real server; stub `requests`/`dotenv` if missing as existing tests do): `emit` creates the directory and day file and writes valid JSONL with `v`/`ts`/`type`/`source`, `MC_EVENTS_DIR` override and default path, day rollover at UTC midnight (inject or patch the clock), concurrent emits from several threads, write failure does not raise. Console parsing against the step 1.0 fixtures (with and without prefixes, malformed lines), checked end to end by feeding lines through the parser and reading back the files. Lifecycle events checked with a mocked `ServerRuntime`/process where practical.
- README section "Event log": location, format, event types, env vars.

Stop here. When the live server next runs with this code, check the files look right, confirm the console fixtures match reality, and only then start phase 2.

## Phase 2: add-on as an extra event source

### Step 2.0: verification spike (scratch world only)

Research (2026-10-01, Microsoft Learn `@minecraft/server` stable API): `world.afterEvents.entityDie` (damage source, damaging entity), `playerJoin`, `playerLeave`, `playerSpawn` (with an initial-spawn flag), `playerGameModeChange`, `playerDimensionChange` and more are stable, no beta toggle. Chat is experimental only and is out of scope.

Confirm on a throwaway local BDS instance:

1. A minimal behavior pack whose script does `console.log("[MCSHS-EVENT] ...")` on `world.afterEvents.worldLoad` shows the line on stdout; note the exact prefix BDS adds.
2. Manifest requirements: script module entry, the `@minecraft/server` dependency version the current BDS accepts, min engine version, activation via `worlds/<level-name>/world_behavior_packs.json`.
3. Nothing extra is needed in `server.properties` or `permissions.json`.

### Step 2.1: the add-on (`addons/server_events/`, versioned in the repo)

- `manifest.json` pinned to the verified version, fresh UUIDs. `scripts/main.js`, plain JavaScript, no build step, every handler in try/catch.
- Emits one line per event: `[MCSHS-EVENT] {"v":1,"type":"player_death","player":"Steve","cause":"entityAttack","by":"minecraft:zombie","by_name":null}`.
- Events: `player_death` (cause, killer type id, killer name if a player or named mob), `player_spawn` (initial vs respawn), `player_dimension_change`, `player_gamemode_change`. Optionally a mob-kills-by-player event if cheap and low volume.
- The app parser recognises the `[MCSHS-EVENT]` marker, takes the JSON, adds `ts` and `source: "addon"`, and writes it through `emit`. Same log, same files.

### Step 2.2: installing automatically

- `mc/addons.py`: before server start, ensure `addons/server_events` is copied into `<active>/current/worlds/<level-name>/behavior_packs/server_events/` and listed in `world_behavior_packs.json` (merge, preserve other packs, back up before the first modification). Idempotent; copy only when the add-on version changed. Worlds are already carried across updates by `mc/update.py`.
- `MC_SERVER_EVENTS_ADDON` (default `true`), in `.env.template`.
- After a Mojang update the pinned dependency may stop loading. If the add-on is enabled and no add-on line (e.g. a `addon_loaded` event on `worldLoad`) is seen within N minutes of `server_ready`, log CRITICAL "server events add-on does not appear to be running, the manifest may need updating", which feeds admin alerts.

### Step 2.3: tests and docs

- Tests: add-on line parsing (BDS prefix, malformed JSON), installer idempotence and `world_behavior_packs.json` merge/backup in temp directories.
- README: what the add-on adds, how to disable it, what to do when it stops loading after an update.

## Phase 3: parsers and statistics (on demand)

- `mc/stats.py`, run as `python -m mc.stats`, reads all event files (optionally a date range) and replays them. No database; at this volume a full replay is instant.
- Playtime: sessions from `player_join` to `player_leave`, closed early by `server_stop`, `server_crash` or `app_exit`/a gap with no app events. Keyed by xuid, showing the latest name. Totals for all time, last 7 and 30 days, session count, last seen.
- Deaths per player, by cause and killer (phase 2 data).
- Server uptime/downtime from start/stop/crash events; count of restarts, updates and crashes.
- Table output by default, `--json` for scripting. Tests with small synthetic event files covering join/leave, crash mid-session, leave without join, duplicate join, name change, missing days.

## Later ideas (not part of this plan)

- **Smarter backups:** skip a backup when no player was online since the last one (needs only phase 1).
- **Idle-based restarts:** run the daily restart and updates when nobody is online, skipping the countdown.
- **Discord/Teams feed:** post joins, leaves and deaths through the admin alerts webhook sender, rate limited.
- **In-game leaderboards** via periodic `say`, built on phase 3.

## Constraints

- Plan only. Do not start implementation until asked.
- Anything touching the live world needs a backup first; do the phase 2 spike on a scratch world only.
- No new third-party dependencies; match existing code style.
