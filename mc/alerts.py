"""
Simple admin alerts: surface critical log records in game and, optionally, to a webhook.

An AdminAlertHandler is attached to the "mc" and application loggers in run_mc_server.main(). Records at or above the
configured level (CRITICAL by default) become alerts, which go to two sinks:

* in game, as a short `say` command to the running server (through the sender registered with set_in_game_sender)
* a JSON POST to MC_ALERT_WEBHOOK_URL, if configured (no webhook by default, in which case there is no HTTP at all)

emit() never blocks or raises: it only formats the alert and puts it on a bounded queue. One daemon worker thread
delivers the alerts. Failures of the worker are never logged at WARNING or above (the handler is attached to the
loggers it would log to, which would alert about itself forever), see _note_failure.

See .env.template and the README for configuration.

"""

import datetime
import logging
import queue
import re
import socket
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlparse

import requests

from mc import config

_log = logging.getLogger(__name__)

IN_GAME_PREFIX = "[Admin alert]"
IN_GAME_MAX_LENGTH = 100
CONTENT_MAX_LENGTH = 1900
TRACEBACK_MAX_LENGTH = 3000
QUEUE_SIZE = 50
POST_TIMEOUT = (5, 10)  # connect, read
POST_ATTEMPTS = 3
POST_BACKOFF_SECONDS = (1.0, 3.0)
STDERR_NOTE_INTERVAL_SECONDS = 600
_MAX_TRACKED_ALERTS = 500

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

_sender_lock = threading.Lock()
_in_game_sender: Callable[[str], None] | None = None


def set_in_game_sender(sender: Callable[[str], None] | None):
    """
    Register the function used to send a console command to the running server, or None while it is not running.
    Called from run_mc_server whenever the runtime is created or replaced.
    """
    global _in_game_sender
    with _sender_lock:
        _in_game_sender = sender


def get_in_game_sender() -> Callable[[str], None] | None:
    with _sender_lock:
        return _in_game_sender


def format_in_game_message(message: str) -> str:
    """Single line, no control characters, truncated, prefixed. Safe to send as the argument of `say`."""
    text = " ".join(_CONTROL_CHARS.sub(" ", message).split())
    if len(text) > IN_GAME_MAX_LENGTH:
        text = text[:IN_GAME_MAX_LENGTH - 3].rstrip() + "..."
    return f"{IN_GAME_PREFIX} {text}"


def validate_webhook_url(url: str | None) -> str | None:
    """Returns the url if it is a usable http(s) URL, else None."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    return url


def describe_webhook_url(url: str) -> str:
    """scheme://host only, the rest of the URL may contain a secret token."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}"


def _get_server_version() -> str | None:
    try:
        from mc import paths
        return paths.get_current_version()
    except Exception:  # noqa
        return None


class AdminAlertHandler(logging.Handler):
    def __init__(
        self,
        level: int = logging.CRITICAL,
        webhook_url: str | None = None,
        in_game: bool = True,
        cooldown_seconds: float = 30 * 60,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_worker: bool = True,
    ):
        super().__init__(level)
        self.webhook_url = webhook_url
        self.in_game = in_game
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._sleep = sleep
        self._start_worker = start_worker

        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._worker: threading.Thread | None = None
        self._state_lock = threading.Lock()
        # (logger name, message) -> [time of last delivered alert, number suppressed since]
        self._seen: dict[tuple[str, str], list] = {}
        self._last_stderr_note = float("-inf")

    @property
    def enabled(self) -> bool:
        return self.in_game or self.webhook_url is not None

    # ------------------------------------------------------------------ producer side

    def emit(self, record: logging.LogRecord):
        try:
            if record.name == __name__ or not self.enabled:
                return
            if record.levelno < self.level:
                return

            message = record.getMessage()
            repeat_count = self._register(record.name, message)
            if repeat_count is None:
                return  # suppressed by the cooldown

            traceback_text = None
            if record.exc_info:
                traceback_text = logging.Formatter().formatException(record.exc_info)[-TRACEBACK_MAX_LENGTH:]

            short = f"[{record.levelname}] {record.name}: {message}"
            alert = {
                "content": short[:CONTENT_MAX_LENGTH],
                "text": short[:CONTENT_MAX_LENGTH],
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "host": socket.gethostname(),
                "server_version": None,  # filled in by the worker, off the logging thread
                "timestamp": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat(),
                "repeat_count": repeat_count,
                "traceback": traceback_text,
            }
            self._enqueue(alert)
        except Exception:  # noqa
            pass  # logging must never raise into the caller

    def _register(self, logger_name: str, message: str) -> int | None:
        """Cooldown/dedupe. Returns repeat_count for an alert that should be sent, None if it is suppressed."""
        now = self._clock()
        key = (logger_name, message)
        with self._state_lock:
            entry = self._seen.get(key)
            if entry is not None and self.cooldown_seconds > 0 and now - entry[0] < self.cooldown_seconds:
                entry[1] += 1
                return None
            repeat_count = 1 + (entry[1] if entry is not None else 0)
            self._seen[key] = [now, 0]
            if len(self._seen) > _MAX_TRACKED_ALERTS:
                # forget expired entries that have nothing suppressed pending
                for k in [k for k, v in self._seen.items() if now - v[0] >= self.cooldown_seconds and v[1] == 0]:
                    del self._seen[k]
            return repeat_count

    def _enqueue(self, alert: dict):
        while True:
            try:
                self.queue.put_nowait(alert)
                break
            except queue.Full:
                try:
                    self.queue.get_nowait()  # drop the oldest
                except queue.Empty:
                    pass
        if self._start_worker:
            self._ensure_worker()

    def _ensure_worker(self):
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._worker_loop, name="admin-alerts", daemon=True)
            self._worker.start()

    # ------------------------------------------------------------------ consumer side

    def _worker_loop(self):
        while True:
            alert = self.queue.get()
            try:
                self.deliver(alert)
            except Exception as e:  # noqa
                self._note_failure(f"unexpected error delivering alert: {e!r}")

    def deliver(self, alert: dict):
        """Send one alert to the sinks. Never raises."""
        if self.in_game:
            self._send_in_game(alert)
        if self.webhook_url is not None:
            self._post_webhook(alert)

    def _send_in_game(self, alert: dict):
        try:
            sender = get_in_game_sender()
            if sender is None:
                return
            sender("say " + format_in_game_message(alert["message"]))
        except Exception as e:  # noqa
            _log.debug(f"Could not send admin alert in game: {e!r}")

    def _post_webhook(self, alert: dict):
        payload = dict(alert)
        payload["server_version"] = _get_server_version()
        last_error = None
        for attempt in range(POST_ATTEMPTS):
            try:
                response = requests.post(self.webhook_url, json=payload, timeout=POST_TIMEOUT)
                status = getattr(response, "status_code", 200)
                if isinstance(status, int) and status >= 400:
                    raise RuntimeError(f"webhook returned status {status}")
                return
            except Exception as e:  # noqa
                # str(e) from requests can contain the full URL, so only keep the exception type and status text
                last_error = type(e).__name__ if not isinstance(e, RuntimeError) else str(e)
                _log.debug(f"Admin alert webhook attempt {attempt + 1}/{POST_ATTEMPTS} failed: {last_error}")
            if attempt < POST_ATTEMPTS - 1:
                try:
                    self._sleep(POST_BACKOFF_SECONDS[min(attempt, len(POST_BACKOFF_SECONDS) - 1)])
                except Exception:  # noqa
                    pass
        self._note_failure(f"admin alert webhook failed after {POST_ATTEMPTS} attempts: {last_error}")

    def _note_failure(self, text: str):
        """At most one stderr line per interval. Deliberately not a log call: see the module docstring."""
        _log.debug(text)
        now = self._clock()
        if now - self._last_stderr_note < STDERR_NOTE_INTERVAL_SECONDS:
            return
        self._last_stderr_note = now
        try:
            print(f"[mc.alerts] {text}", file=sys.stderr)
        except Exception:  # noqa
            pass


def create_handler() -> AdminAlertHandler:
    """Build the handler from the environment (see mc/config.py) and log one INFO line about the webhook."""
    raw_url = config.get_alert_webhook_url()
    webhook_url = validate_webhook_url(raw_url)
    if raw_url and webhook_url is None:
        _log.warning("MC_ALERT_WEBHOOK_URL is not a valid http/https URL, the alert webhook is disabled")

    handler = AdminAlertHandler(
        level=config.get_alert_min_level(),
        webhook_url=webhook_url,
        in_game=config.get_alert_in_game(),
        cooldown_seconds=config.get_alert_cooldown_minutes() * 60,
    )

    if webhook_url is not None:
        _log.info(f"Admin alert webhook enabled ({describe_webhook_url(webhook_url)})")
    else:
        _log.info("Admin alert webhook disabled (MC_ALERT_WEBHOOK_URL not set)")
    return handler
