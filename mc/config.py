"""
Backup and retention configuration read from environment variables (see .env.template).

Invalid values log a warning and fall back to the default. Values are read on every call, nothing is cached.

"""

import logging
import os

from mc.retention import Policy

_log = logging.getLogger(__name__)

WORLD_BACKUP_DEFAULTS = {
    "MC_BACKUP_KEEP_ALL_HOURS": 48,
    "MC_BACKUP_KEEP_DAILY": 7,
    "MC_BACKUP_KEEP_WEEKLY": 4,
    "MC_BACKUP_KEEP_MONTHLY": 6,
    "MC_BACKUP_KEEP_YEARLY": 2,
}

UPDATE_BACKUP_DEFAULTS = {
    "MC_UPDATE_BACKUP_KEEP_LAST": 6,
    "MC_UPDATE_BACKUP_KEEP_QUARTERLY": 4,  # one per 3 months, for a year
    "MC_UPDATE_BACKUP_KEEP_HALF_YEARLY": 2,  # one per 6 months, for a year
    "MC_UPDATE_BACKUP_KEEP_YEARLY": 3,
}

DEFAULT_KEEP_DOWNLOADED_VERSIONS = 5


def get_int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip().strip("'\""))
    except ValueError:
        _log.warning(f"{name} is set to '{raw}', which is not an integer, using default {default}")
        return default
    if value < minimum:
        _log.warning(f"{name} is set to {value}, which is below the minimum {minimum}, using default {default}")
        return default
    return value


def get_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().strip("'\"").lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    _log.warning(f"{name} is set to '{raw}', which is not a boolean, using default {default}")
    return default


def get_world_backup_policy() -> Policy:
    d = WORLD_BACKUP_DEFAULTS
    return Policy(
        keep_all_hours=get_int_env("MC_BACKUP_KEEP_ALL_HOURS", d["MC_BACKUP_KEEP_ALL_HOURS"]),
        keep_daily=get_int_env("MC_BACKUP_KEEP_DAILY", d["MC_BACKUP_KEEP_DAILY"]),
        keep_weekly=get_int_env("MC_BACKUP_KEEP_WEEKLY", d["MC_BACKUP_KEEP_WEEKLY"]),
        keep_monthly=get_int_env("MC_BACKUP_KEEP_MONTHLY", d["MC_BACKUP_KEEP_MONTHLY"]),
        keep_yearly=get_int_env("MC_BACKUP_KEEP_YEARLY", d["MC_BACKUP_KEEP_YEARLY"]),
    )


def get_update_backup_policy() -> Policy:
    d = UPDATE_BACKUP_DEFAULTS
    return Policy(
        keep_last=get_int_env("MC_UPDATE_BACKUP_KEEP_LAST", d["MC_UPDATE_BACKUP_KEEP_LAST"]),
        keep_quarterly=get_int_env("MC_UPDATE_BACKUP_KEEP_QUARTERLY", d["MC_UPDATE_BACKUP_KEEP_QUARTERLY"]),
        keep_half_yearly=get_int_env("MC_UPDATE_BACKUP_KEEP_HALF_YEARLY", d["MC_UPDATE_BACKUP_KEEP_HALF_YEARLY"]),
        keep_yearly=get_int_env("MC_UPDATE_BACKUP_KEEP_YEARLY", d["MC_UPDATE_BACKUP_KEEP_YEARLY"]),
    )


def get_prune_dry_run() -> bool:
    return get_bool_env("MC_BACKUP_PRUNE_DRY_RUN", False)


def get_keep_downloaded_versions() -> int:
    return get_int_env("MC_KEEP_DOWNLOADED_VERSIONS", DEFAULT_KEEP_DOWNLOADED_VERSIONS, minimum=1)


def get_manifest_ignore_patterns() -> list[str]:
    """Comma separated fnmatch patterns, matched against the world-relative path (forward slashes) and basename."""
    raw = os.environ.get("MC_BACKUP_MANIFEST_IGNORE", "")
    raw = raw.strip().strip("'\"")
    return [p.strip() for p in raw.split(",") if p.strip()]
