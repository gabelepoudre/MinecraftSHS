"""
The event log: an append-only record of what happened on the server (players joining and leaving, starts, stops,
crashes, updates, backups), kept as one JSON object per line in one file per UTC day.

Every event goes through emit(), whatever its source, so a call site always looks like:

    events.emit(events.SERVER_CRASH, events.SOURCE_APP, version="1.26.52.3", exit_code=1)

and a line in <MC_EVENTS_DIR>/2026-10-05.jsonl looks like:

    {"v": 1, "ts": "2026-10-05T12:00:00.000+00:00", "type": "server_crash", "source": "app", "version": ..., ...}

Console lines from the server are turned into events by parse_console_line(), which ServerRuntime calls for every
stdout line. Statistics (playtime etc.) are meant to be derived later by replaying the files.

emit() never raises: a failure to write is logged at ERROR and the event is lost, the server carries on.

See the "Event log" section of the README for the schema.

"""

import datetime
import json
import logging
import os
import re
import threading

from mc import paths

_log = logging.getLogger(__name__)

# Bump when a field is renamed or changes meaning. Adding a field does not need a bump.
SCHEMA_VERSION = 1

SOURCE_CONSOLE = "console"  # parsed from the server's stdout
SOURCE_APP = "app"  # this wrapper's own lifecycle

# ---------------------------------------------------------------- event types (fields besides v, ts, type, source)
# from the console
PLAYER_JOIN = "player_join"  # player, xuid (None if the console shows an empty xuid)
PLAYER_LEAVE = "player_leave"  # player, xuid
ACHIEVEMENT = "achievement"  # player, achievement. UNVERIFIED: the console line format is a guess, see _ACHIEVEMENT_RE
SERVER_READY = "server_ready"  # (none) the "Server started." line, the world is loaded and players can join
# from the app
APP_START = "app_start"  # (none)
APP_EXIT = "app_exit"  # reason: stop_command, keyboard_interrupt, error
SERVER_START = "server_start"  # version
SERVER_STOP = "server_stop"  # reason: daily_restart, update, manual, exit
SERVER_CRASH = "server_crash"  # version, exit_code. The process died without being asked to stop
RESTART_SCHEDULED = "restart_scheduled"  # reason (daily_restart, update), countdown_seconds
UPDATE_AVAILABLE = "update_available"  # from_version, to_version. A newer downloaded version will be installed
UPDATE_INSTALLED = "update_installed"  # from_version (None on first install), to_version
UPDATE_FAILED = "update_failed"  # from_version, to_version, error
BACKUP_COMPLETED = "backup_completed"  # level, skipped (True if the world was unchanged and no zip was written)
BACKUP_FAILED = "backup_failed"  # level, error

_write_lock = threading.Lock()


def _now() -> datetime.datetime:
    # a function so tests can replace the clock
    return datetime.datetime.now(datetime.timezone.utc)


def emit(event_type: str, source: str, **fields):
    """
    Append one event to today's (UTC) file. Thread safe, never raises.

    :param event_type: one of the event type constants above
    :param source: SOURCE_CONSOLE or SOURCE_APP
    :param fields: the type specific fields, they must be JSON serialisable (anything else is written with str())
    """
    try:
        now = _now()
        event = {
            "v": SCHEMA_VERSION,
            "ts": now.isoformat(timespec="milliseconds"),
            "type": event_type,
            "source": source,
        }
        # the common fields always win, a type specific field cannot overwrite them
        event.update({k: v for k, v in fields.items() if k not in event})
        line = json.dumps(event, ensure_ascii=False, default=str)  # json.dumps never writes a newline in a value

        events_dir = paths.get_path_to_events_dir()
        path = os.path.join(events_dir, f"{now.strftime('%Y-%m-%d')}.jsonl")
        with _write_lock:
            os.makedirs(events_dir, exist_ok=True)  # in case it was deleted while we were running
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
    except Exception as e:  # noqa
        _log.error(f"Could not write {event_type} event to the event log: {e!r}")


# ---------------------------------------------------------------- console parsing
#
# BDS console lines look like:
#     [2026-10-05 12:00:00:000 INFO] Player connected: Steve, xuid: 2535412345678901
#     [2026-10-05 12:05:00:000 INFO] Player disconnected: Steve, xuid: 2535412345678901, pfid: 1a2b3c4d5e6f7a8b
#     [2026-10-05 11:59:00:000 INFO] Server started.
# Some versions print "NO LOG FILE! - " before the timestamp. We strip any such prefix and the bracketed
# timestamp/level, then match the message from its start. Matching from the start (not anywhere in the line) means a
# player cannot fake an event by writing "Player connected: ..." in a message that ends up on the console.

_PREFIX_RE = re.compile(r"^\s*(?:NO LOG FILE! - )?(?:\[[^\]]*\]\s*)*")

# Each pattern: (event type, a cheap trigger the message must start with, the full regex). A message that starts with
# the trigger but does not match the regex is logged at DEBUG, so a changed format shows up in the logs.
_JOIN_RE = re.compile(r"^Player connected:\s*(?P<player>[^,\s][^,]*?)\s*,\s*xuid:\s*(?P<xuid>\d*)\s*(?:,.*)?$")
_LEAVE_RE = re.compile(r"^Player disconnected:\s*(?P<player>[^,\s][^,]*?)\s*,\s*xuid:\s*(?P<xuid>\d*)\s*(?:,.*)?$")
_READY_RE = re.compile(r"^Server started\.?$")
# UNVERIFIED: we have not seen BDS print achievements on the console. This guesses at the in-game chat wording
# ("Steve has earned the achievement [Stone Age]"); check it against a live server and adjust or remove.
_ACHIEVEMENT_RE = re.compile(
    r"^(?P<player>[^\[\]<>]+?) has (?:earned|unlocked|made) the achievement \[?(?P<achievement>[^\[\]]+?)\]?$"
)

_CONSOLE_PATTERNS = [
    (PLAYER_JOIN, "Player connected:", _JOIN_RE),
    (PLAYER_LEAVE, "Player disconnected:", _LEAVE_RE),
    (SERVER_READY, "Server started", _READY_RE),
    (ACHIEVEMENT, None, _ACHIEVEMENT_RE),  # no fixed start, the message starts with the player name
]


def strip_console_prefix(line: str) -> str:
    """The message part of a console line, without the "[timestamp LEVEL]" prefix and surrounding whitespace."""
    return _PREFIX_RE.sub("", line, count=1).strip()


def parse_console_line(line: str) -> str | None:
    """
    Emit the event for one line of server stdout, if it is one we recognise. Never raises.

    :return: the event type emitted, or None if the line is not an event
    """
    try:
        message = strip_console_prefix(line)
        for event_type, trigger, regex in _CONSOLE_PATTERNS:
            if trigger is not None and not message.startswith(trigger):
                continue
            match = regex.match(message)
            if match is None:
                if trigger is not None:
                    _log.debug(f"Console line looks like {event_type} but could not be parsed: {line!r}")
                continue

            fields = match.groupdict()
            if "xuid" in fields:
                fields["xuid"] = fields["xuid"] or None  # some servers (e.g. offline mode) print an empty xuid
            emit(event_type, SOURCE_CONSOLE, **fields)
            return event_type
    except Exception as e:  # noqa
        _log.debug(f"Error parsing console line {line!r}: {e!r}")
    return None
