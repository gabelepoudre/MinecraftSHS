# Server events add-on, event parsing and playtime tracking

Status: plan only, not started.

Goal: get richer information out of the Bedrock server than its trimmed console gives us (today: connects, disconnects, achievements). Specifically player deaths with cause, joins/leaves with stable identifiers, and optionally chat, then use those events for playtime tracking and other features. Approach: a small script add-on (behavior pack with the Script API) that prints one machine-readable line per event to the server console, plus a parser in this app that consumes those lines from the server's stdout.

## Research findings (as of 2026-10-01)

Sources: Microsoft Learn `@minecraft/server` WorldAfterEvents (stable moniker), Endstone project docs.

- **Stable API** (`@minecraft/server`, no beta toggle): `world.afterEvents.entityDie` (damage source, damaging entity), `playerJoin`, `playerLeave`, `playerSpawn` (with an initial-spawn flag, so it separates first spawn from respawn), `playerBreakBlock`, `playerPlaceBlock`, `playerEmote`, `playerGameModeChange`, `playerDimensionChange`, `entityHurt`, `weatherChange`, and more.
- **Chat is experimental only.** `world.afterEvents.chatSend` (and the before-event) appear only in the experimental/pre-release API reference, marked "still in pre-release, may change or be removed". Using it requires the Beta APIs experiment enabled on the world, and risks breaking on a Mojang update. This matters for a server that auto-updates.
- Script `console.log` / `console.warn` output is reported to reach the dedicated server's console. NOT yet verified first-hand; verify in step 0 below.
- **Alternative: Endstone** (https://github.com/EndstoneMC/endstone), a plugin loader and Python/C++ API wrapping BDS on Windows and Linux, with `PlayerChatEvent`, `PlayerDeathEvent` etc. Gives chat without beta flags, but wraps the server executable and must track every Mojang release, which conflicts with this repo's self-updating download flow. Not recommended unless chat becomes important and the beta route proves unworkable.

## Step 0: Verification spike (do first, small)

Confirm on a throwaway local BDS instance (a scratch world, not the live one):

1. A minimal behavior pack with a script module that does `console.log("[MCSHS] hello")` on `world.afterEvents.worldLoad` shows the line on the server's stdout (and note the exact line format BDS wraps around it, e.g. a timestamp and `[Scripting]` tag).
2. The exact manifest requirements: script module entry, `@minecraft/server` dependency version string that the current BDS accepts (BDS rejects packs whose dependency version it does not support), min engine version, and how the pack is activated for a world (`world_behavior_packs.json` inside `worlds/<level-name>/`).
3. Whether anything extra is needed in `server.properties` or `config/default/permissions.json` for a stdout-only script (expected: no, since we use no `@minecraft/server-net`).
4. For the optional chat feature only: how the Beta APIs experiment is enabled for a dedicated-server world (likely the world's `level.dat` experiments flag) and whether that has side effects (e.g. disables achievements). If this is unreasonable, drop chat.

Document findings at the top of this file or in the add-on's README before building on them.

## Step 1: The add-on (`addons/server_events/`, versioned in the repo)

- `manifest.json` pinned to the `@minecraft/server` version verified in step 0, with fresh UUIDs.
- `scripts/main.js` (plain JavaScript, no build step; keep it tiny). Subscribes to:
  - `playerJoin`, `playerLeave`: emit player name and player id.
  - `playerSpawn`: emit with `initialSpawn` flag (respawn after death shows up here).
  - `entityDie`, filtered to `entity.typeId === "minecraft:player"`: emit player name, `damageSource.cause`, and the killer's type id / name if present.
  - A periodic heartbeat (e.g. every 5 minutes via `system.runInterval`) emitting the list of currently online player names (used for crash-safe playtime, see step 3).
- Output format: one line per event, a fixed prefix and a single JSON object, for example `[MCSHS-EVENT] {"t":"death","player":"Steve","cause":"entityAttack","by":"minecraft:zombie"}`. Include a schema version field (`"v":1`). Never emit anything multi-line. Wrap every handler in try/catch so a script error can never hurt the server.
- Chat (optional, isolated, off by default): a second script file or feature flag using `chatSend`, only activated if step 0.4 proved viable. It must fail safely: if the API is missing in a future version, the rest of the add-on keeps working.
- Add-on `README.md` documenting events and schema.

## Step 2: Installing the add-on automatically

Worlds are copied across updates already (`mc/update.py` carries over `worlds/`), so a pack installed inside the world's folder survives updates.

- New module `mc/addons.py`: on server start (before launching the process), ensure `addons/server_events` is copied into `<active>/current/worlds/<level-name>/behavior_packs/server_events/` and that `world_behavior_packs.json` in that world folder lists it (create or merge the file, preserving other packs; keep a backup before first modification). Idempotent; only copy when the add-on version changed.
- Config: `MC_SERVER_EVENTS_ADDON` (default `true`), documented in `.env.template`. If the pack fails to load (BDS prints a script/pack error), log at ERROR (which feeds admin alerts when enabled); do not crash.
- Because the manifest pins a dependency version, after each Mojang update the pack may stop loading. Add detection: if the add-on is enabled and no heartbeat/event line is seen within N minutes of start, log a CRITICAL "server events add-on does not appear to be running, the manifest may need updating". This leans on the existing admin alert handler.

## Step 3: Parsing events and tracking playtime

- In `mc/server_runtime.py`'s stdout reader, detect lines containing the `[MCSHS-EVENT]` prefix, parse the JSON after it (tolerate any BDS prefix before the marker; ignore malformed lines with a DEBUG log), and dispatch to registered handlers. Keep the existing raw log of stdout unchanged. New module `mc/events.py` holds the parser and a simple dispatcher (`register(event_type, callback)`).
- Playtime tracker `mc/playtime.py`, storing into SQLite (stdlib `sqlite3`) at `<data dir>/playtime.sqlite3` (path overridable, `MC_PLAYTIME_DB`):
  - `sessions(player_id, player_name, joined_at, left_at, closed_by)` where `closed_by` is `leave`, `heartbeat` or `server_stop`.
  - On `playerJoin` open a session; on `playerLeave` close it.
  - Crash safety: the heartbeat updates a `last_seen` for open sessions. On server start (and on stop in `ServerRuntime.stop`), close any session still open using its `last_seen` (or now for a clean stop), so a crash or restart never inflates playtime or leaves ghost sessions. Also reconcile: a heartbeat listing that omits a player with an open session closes it.
  - Track by player id where available, with the latest name; handle name changes.
  - Thread-safe (events arrive on the stdout reader thread); keep DB writes short, one connection per call or a lock.
- Reporting: `python -m mc.playtime` prints a table of total time per player (all time, last 7 days, last 30 days), plus session count and last seen. Optionally `--json`. Keep it simple; no web UI.
- Deaths: append to a JSONL event log `<data dir>/events/YYYY-MM-DD.jsonl` for all parsed events (deaths, joins, leaves, spawns, optional chat), with a retention setting reusing the tiered retention idea or simple max age (`MC_EVENT_LOG_KEEP_DAYS`, default 90). Add a small death counter per player in the same SQLite database.

## Step 4: Documentation and tests

- `README.md`: section "Server events and playtime" covering what the add-on does, what is stable vs experimental, the env vars, and how to run the playtime report.
- Unit tests (pytest, mock/stub where `requests`/`dotenv` are missing as in prior work; no real server): event-line parsing including BDS prefixes and malformed lines, dispatcher, playtime session logic (join/leave, crash recovery via last_seen, duplicate join, leave without join, name change, heartbeat reconciliation), report aggregation, add-on installer idempotence and `world_behavior_packs.json` merge/backup using temp directories.
- Match existing code style; no new third-party dependencies.

## Things you might also want later (not part of this plan)

- **Smarter backups:** the backup plan listed "skip when no players were online since the last backup" as optional. With join/leave/heartbeat events this becomes reliable and cheap. Good first follow-up.
- **Discord/Teams feed of events:** reuse the admin alerts webhook sender (or a second `MC_EVENT_WEBHOOK_URL`) to post joins, leaves and deaths as a live feed. Rate limit it.
- **Death leaderboard / "most time played" announcements** in-game via periodic `say`, built on the tracker.
- **Idle-based restarts:** prefer to run the daily restart (and updates) when the server is empty, skipping the 15 minute countdown when nobody is online.
- **Chat logging** if step 0.4 shows the beta API is acceptable, or via Endstone if it is worth taking on the loader.
- **Other stable events** worth logging cheaply: gamemode changes, dimension changes, block break/place counts (careful: high volume).

## Constraints

- Plan only. Do not start implementation until asked.
- Anything touching the live world needs a backup first; do the step 0 spike on a scratch world only.
