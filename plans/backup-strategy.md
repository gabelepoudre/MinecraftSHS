# Backup strategy overhaul

Goal: configurable tiered retention for world backups and update backups, plus change detection so idle servers don't produce redundant backups. Reduces storage while keeping a long rollback horizon.

## Current state (see `mc/server_runtime.py`, `mc/update.py`, `mc/paths.py`)

- `ServerRuntime._backup_thread` sleeps 1h, calls `backup()`, and `break`s on any exception, so one failure permanently ends backups. (Bug, fix first.)
- `ServerRuntime.backup()` zips `worlds/<level-name>` into `<backup_dir>/<level-name>/<YYYY-mm-dd_HH-MM-SS>.zip`. It reopens the zip in append mode for every file (slow). Backups are never pruned.
- `update.try_update()` zips the whole `current` server dir to `<backup_dir>/updates/<old>_to_<new>.zip`. Never pruned.
- `update._get_most_recent_downloaded_version()` hardcodes keeping 5 downloaded versions in the versions dir.
- Config comes from env vars via `mc/paths.py` (`.env`, see `.env.template`).

## Step 1: Fix backup thread and zip writing

- Backup thread must log errors and continue the loop rather than `break`. Always send `save resume` in a `finally` after `save hold`, so a failed backup never leaves the server in held-save state.
- Open the zip once per backup, not once per file. Keep the per-file try/except so one locked file does not abort the backup.
- Write to a temp name (e.g. `.partial`) and rename on success so pruning never sees incomplete archives.

## Step 2: Tiered retention (new module `mc/retention.py`)

Pure, stateless, unit-testable function: given a list of `(timestamp, path)` and a policy, return the set to keep and the set to delete.

Policy is a list of buckets, GFS / restic-forget style:

- `keep_all_hours` (default 48): keep every backup newer than this.
- `keep_daily` (default 7), `keep_weekly` (default 4), `keep_monthly` (default 6), `keep_yearly` (default 2): for each calendar period, keep only the newest backup in it, for the N most recent periods that contain a backup.
- A backup is kept if any rule claims it. Never delete the single newest backup. Never delete if the policy is empty or unparsable (fail safe: keep everything and log an error).

Config: env vars (document in `.env.template`), e.g. `MC_BACKUP_KEEP_ALL_HOURS`, `MC_BACKUP_KEEP_DAILY`, etc., parsed in a new `mc/config.py` or in `paths.py` style with validation and defaults. Invalid values log a warning and fall back to defaults.

Run the prune pass after each successful world backup, per world subdirectory. Parse timestamps from filenames; ignore files that do not match the pattern (never delete unknown files). Add a dry-run/log mode that logs what would be deleted.

## Step 3: Update backup retention

- Rename update backups to include a timestamp: `<YYYY-mm-dd_HH-MM-SS>_<old>_to_<new>.zip` (keep parsing of the old name format so existing files are still pruned/handled).
- Separate policy from world backups: `MC_UPDATE_BACKUP_KEEP_LAST` (default 6, always kept), then thinned tiers e.g. one per 3 months, one per 6 months, one per year (configurable, same bucket machinery as step 2).
- Exclude `bedrock_server.exe` and other vendor binaries from update backups where reasonable. Back up `worlds`, `server.properties`, `allowlist.json`, `permissions.json` (the files that are actually carried over). Decide carefully and note it in the code; do not drop anything the update process does not recreate from the new version.
- Make the versions-dir cap (currently hardcoded 5) configurable (`MC_KEEP_DOWNLOADED_VERSIONS`, default 5).
- Run update-backup prune after a successful update.

## Step 4: Change detection (skip unchanged backups)

- Before zipping, after `save hold` / `save query`, build a manifest of the world dir: relative path, size, mtime for each file. Ignore a configurable set of noise files if they churn on an idle server (investigate `LOG`, `LOG.old`, `LOCK`, `CURRENT`, `MANIFEST-*` behaviour; start by comparing everything and ignoring only files proven to change while idle).
- Store the manifest next to the latest backup (e.g. `<backup>.manifest.json`, or one `last_manifest.json` per world). If the new manifest equals the previous one, skip creating the backup, log it, still send `save resume`.
- Optional extra: skip when zero players have been online since the last backup, only if reliably detectable from server stdout (player connected/disconnected lines). Treat as a nice-to-have; do not block the main work on it.
- Out of scope for now: real deduplicated or incremental backups (restic/borg-style).

## Testing and constraints

- Unit tests for `mc/retention.py` with fabricated timestamps covering bucket boundaries, the newest-always-kept rule, unknown filenames, and an empty policy. Use pytest if available; otherwise plain asserts runnable via `python -m`.
- Do not run the real server or touch real data dirs in tests; use temp dirs.
- Match existing code style (logging via `_log`, type hints in the `str | None` style, no new heavy dependencies).
- Update `README.md`: document the new env vars, and tick off / adjust the relevant TODO items (the "delete runtime backups more than 48 hours old" and "update backups sorted by world name" items are superseded by this plan; note that). Update `.env.template`.
- Do not commit; leave changes in the working tree for review.

## Out of scope

- 4:00am scheduled restarts, arbitrary on-start commands (separate plans).
