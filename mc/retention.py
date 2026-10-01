"""
Tiered (GFS / restic-forget style) backup retention.

``select`` is pure and stateless: given ``(timestamp, path)`` entries and a ``Policy`` it returns which paths to keep
and which to delete. ``prune_dir`` applies it to a directory on disk, never touching files it cannot parse.

"""

import datetime
import logging
import os
import re
from dataclasses import dataclass, fields
from typing import Callable

_log = logging.getLogger(__name__)

# (timestamp, path)
Entry = tuple[datetime.datetime, str]

TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
_WORLD_BACKUP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.zip$")
_UPDATE_BACKUP_NEW_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(.+)_to_(.+)\.zip$")
_UPDATE_BACKUP_OLD_RE = re.compile(r"^(.+)_to_(.+)\.zip$")


@dataclass(frozen=True)
class Policy:
    """
    Every field is a count (hours for ``keep_all_hours``); 0 disables that rule.

    - keep_all_hours: keep every backup newer than this many hours
    - keep_last: keep the N newest backups regardless of age
    - keep_daily/weekly/monthly/quarterly/half_yearly/yearly: for the N most recent calendar periods that contain a
      backup, keep the newest backup in that period. Weeks are ISO weeks, quarters are 3 calendar months, half years
      are Jan-Jun / Jul-Dec.
    """
    keep_all_hours: int = 0
    keep_last: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_quarterly: int = 0
    keep_half_yearly: int = 0
    keep_yearly: int = 0

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) <= 0 for f in fields(self))


def _period_daily(ts: datetime.datetime):
    return ts.year, ts.month, ts.day


def _period_weekly(ts: datetime.datetime):
    iso = ts.isocalendar()
    return iso[0], iso[1]


def _period_monthly(ts: datetime.datetime):
    return ts.year, ts.month


def _period_quarterly(ts: datetime.datetime):
    return ts.year, (ts.month - 1) // 3


def _period_half_yearly(ts: datetime.datetime):
    return ts.year, (ts.month - 1) // 6


def _period_yearly(ts: datetime.datetime):
    return (ts.year,)


_PERIOD_RULES: list[tuple[str, Callable[[datetime.datetime], tuple]]] = [
    ("keep_daily", _period_daily),
    ("keep_weekly", _period_weekly),
    ("keep_monthly", _period_monthly),
    ("keep_quarterly", _period_quarterly),
    ("keep_half_yearly", _period_half_yearly),
    ("keep_yearly", _period_yearly),
]


def select(
        entries: list[Entry],
        policy: Policy,
        now: datetime.datetime | None = None,
) -> tuple[set[str], set[str]]:
    """
    Decide which backups to keep and which to delete.

    A backup is kept if any rule claims it. The single newest backup is always kept. An empty policy keeps everything.

    :return: (paths to keep, paths to delete)
    """
    if now is None:
        now = datetime.datetime.now()

    all_paths = {path for _, path in entries}
    if not entries:
        return set(), set()

    if policy.is_empty():
        _log.error("Retention policy is empty, keeping all backups")
        return all_paths, set()

    ordered = sorted(entries, key=lambda e: (e[0], e[1]), reverse=True)  # newest first
    keep: set[str] = {ordered[0][1]}  # never delete the newest

    if policy.keep_all_hours > 0:
        cutoff = now - datetime.timedelta(hours=policy.keep_all_hours)
        keep.update(path for ts, path in ordered if ts > cutoff)

    if policy.keep_last > 0:
        keep.update(path for _, path in ordered[:policy.keep_last])

    for attr, period_fn in _PERIOD_RULES:
        count = getattr(policy, attr)
        if count <= 0:
            continue
        seen: set[tuple] = set()
        for ts, path in ordered:  # newest first, so the first one seen in a period is the newest in it
            period = period_fn(ts)
            if period in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(period)
            keep.add(path)

    return keep, all_paths - keep


def parse_world_backup_name(name: str, path: str | None = None) -> datetime.datetime | None:
    m = _WORLD_BACKUP_RE.match(name)
    if m is None:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), TIMESTAMP_FORMAT)
    except ValueError:
        return None


def parse_update_backup_name(name: str, path: str | None = None) -> datetime.datetime | None:
    """
    New format ``<timestamp>_<old>_to_<new>.zip`` is parsed from the name. The legacy ``<old>_to_<new>.zip`` has no
    timestamp, so the file modification time is used instead.
    """
    m = _UPDATE_BACKUP_NEW_RE.match(name)
    if m is not None:
        try:
            return datetime.datetime.strptime(m.group(1), TIMESTAMP_FORMAT)
        except ValueError:
            return None
    if _UPDATE_BACKUP_OLD_RE.match(name) is not None and path is not None:
        try:
            return datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return None
    return None


def prune_dir(
        directory: str,
        parse_name: Callable[[str, str | None], datetime.datetime | None],
        policy: Policy,
        dry_run: bool = False,
        now: datetime.datetime | None = None,
) -> list[str]:
    """
    Apply ``policy`` to the files directly inside ``directory``. Files ``parse_name`` returns None for (including
    ``.partial`` files and sub directories) are never touched.

    :return: the paths deleted (or that would have been deleted in dry run mode)
    """
    if not os.path.isdir(directory):
        return []

    entries: list[Entry] = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        ts = parse_name(name, path)
        if ts is None:
            continue
        entries.append((ts, path))

    keep, delete = select(entries, policy, now)
    deleted = []
    for path in sorted(delete):
        if dry_run:
            _log.info(f"[dry run] would delete old backup: {path}")
            deleted.append(path)
            continue
        try:
            os.remove(path)
            _log.info(f"Deleted old backup: {path}")
            deleted.append(path)
        except OSError as e:
            _log.error(f"Failed to delete old backup {path}: {e}")
    _log.debug(f"Retention for {directory}: kept {len(keep)}, deleted {len(deleted)}")
    return deleted
