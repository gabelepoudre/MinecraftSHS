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

Tests: `python -m pytest tests`

### TODO
- auto 4:00am (local?) restarts (with backup just in case)
- ~~delete runtime backups more than 48 hours old~~ (superseded by tiered retention, see Backups)
- ~~update backups need to be sorted by world name~~ (superseded: update backups are timestamped and pruned, see Backups)
- arbitrary on-start commands
