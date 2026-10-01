import logging
import sys
import time

import pytest

from mc import alerts, config, paths, update

WEBHOOK = "https://hooks.example.com/services/SECRET-TOKEN"
LINK = "https://www.minecraft.net/bedrockdedicatedserver/bin-win/bedrock-server-1.26.52.3.zip"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_handler(**kwargs):
    kwargs.setdefault("start_worker", False)
    kwargs.setdefault("sleep", lambda s: None)
    return alerts.AdminAlertHandler(**kwargs)


def record(msg="boom", level=logging.CRITICAL, name="mc.update", exc_info=None, args=()):
    return logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)


def drain(handler):
    items = []
    while not handler.queue.empty():
        items.append(handler.queue.get_nowait())
    return items


@pytest.fixture(autouse=True)
def reset_sender():
    alerts.set_in_game_sender(None)
    yield
    alerts.set_in_game_sender(None)


@pytest.fixture
def posts(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(alerts.requests, "post", fake_post)
    return calls


def test_ignores_records_below_level():
    h = make_handler(level=logging.CRITICAL)
    h.handle(record(level=logging.ERROR))
    assert drain(h) == []
    h = make_handler(level=logging.ERROR)
    h.handle(record(level=logging.ERROR))
    assert len(drain(h)) == 1


def test_ignores_own_logger():
    h = make_handler()
    h.handle(record(name="mc.alerts"))
    assert drain(h) == []


def test_unset_webhook_makes_no_http_calls(posts):
    sent = []
    alerts.set_in_game_sender(sent.append)
    h = make_handler(webhook_url=None)
    h.handle(record())
    h.deliver(drain(h)[0])
    assert posts == []
    assert len(sent) == 1


def test_nothing_enabled_is_a_noop(posts):
    h = make_handler(webhook_url=None, in_game=False)
    h.handle(record())
    assert drain(h) == []
    assert posts == []


def test_payload_shape_and_post(posts, monkeypatch):
    monkeypatch.setattr(alerts, "_get_server_version", lambda: "1.2.3.4")
    h = make_handler(webhook_url=WEBHOOK)
    try:
        raise ValueError("bad")
    except ValueError:
        h.handle(record("it %s", args=("broke",), exc_info=sys.exc_info()))
    h.deliver(drain(h)[0])

    url, kwargs = posts[0]
    assert url == WEBHOOK
    assert kwargs["timeout"] == alerts.POST_TIMEOUT
    p = kwargs["json"]
    assert set(p) == {"content", "text", "level", "logger", "message", "host", "server_version", "timestamp",
                      "repeat_count", "traceback"}
    assert p["content"] == p["text"] == "[CRITICAL] mc.update: it broke"
    assert p["level"] == "CRITICAL" and p["logger"] == "mc.update" and p["message"] == "it broke"
    assert p["server_version"] == "1.2.3.4"
    assert p["repeat_count"] == 1
    assert "ValueError: bad" in p["traceback"]
    assert p["timestamp"].endswith("+00:00")


def test_content_truncated_full_message_kept():
    h = make_handler(webhook_url=WEBHOOK)
    h.handle(record("x" * 5000))
    p = drain(h)[0]
    assert len(p["content"]) == alerts.CONTENT_MAX_LENGTH
    assert p["message"] == "x" * 5000
    assert p["traceback"] is None


def test_traceback_truncated():
    h = make_handler(webhook_url=WEBHOOK)
    try:
        raise ValueError("y" * 10000)
    except ValueError:
        h.handle(record(exc_info=sys.exc_info()))
    assert len(drain(h)[0]["traceback"]) == alerts.TRACEBACK_MAX_LENGTH


def test_server_version_failure_is_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(paths, "get_current_version", boom)
    assert alerts._get_server_version() is None


def test_in_game_message_formatting():
    msg = alerts.format_in_game_message("line one\nline\ttwo\x07\r\n" + "y" * 300)
    assert msg.startswith(alerts.IN_GAME_PREFIX + " line one line two")
    assert "\n" not in msg and "\x07" not in msg and "\r" not in msg
    body = msg[len(alerts.IN_GAME_PREFIX) + 1:]
    assert len(body) == alerts.IN_GAME_MAX_LENGTH and body.endswith("...")
    assert alerts.format_in_game_message("short") == "[Admin alert] short"


def test_in_game_sends_say_command():
    sent = []
    alerts.set_in_game_sender(sent.append)
    h = make_handler()
    h.handle(record("hello"))
    h.deliver(drain(h)[0])
    assert sent == ["say [Admin alert] hello"]


def test_in_game_disabled_and_no_sender():
    sent = []
    alerts.set_in_game_sender(sent.append)
    h = make_handler(in_game=False)
    h.handle(record())
    assert drain(h) == []
    assert sent == []

    alerts.set_in_game_sender(None)
    h = make_handler()
    h.handle(record())
    h.deliver(drain(h)[0])  # silently skipped


def test_in_game_sender_raising_is_swallowed(posts):
    def bad(_):
        raise RuntimeError("Server not started")

    alerts.set_in_game_sender(bad)
    h = make_handler(webhook_url=WEBHOOK)
    h.handle(record())
    h.deliver(drain(h)[0])
    assert len(posts) == 1  # webhook still sent


def test_cooldown_dedupe_and_repeat_count():
    clock = Clock()
    h = make_handler(cooldown_seconds=60, clock=clock)
    h.handle(record("same"))
    assert len(drain(h)) == 1
    for _ in range(3):
        clock.now += 10
        h.handle(record("same"))
    assert drain(h) == []
    h.handle(record("other"))  # different message is not suppressed
    h.handle(record("same", name="mc.other"))  # neither is a different logger
    assert len(drain(h)) == 2
    clock.now += 100
    h.handle(record("same"))
    again = drain(h)
    assert len(again) == 1 and again[0]["repeat_count"] == 4
    clock.now += 100
    h.handle(record("same"))
    assert drain(h)[0]["repeat_count"] == 1


def test_zero_cooldown_disables_dedupe():
    h = make_handler(cooldown_seconds=0)
    h.handle(record("same"))
    h.handle(record("same"))
    assert len(drain(h)) == 2


def test_queue_is_bounded_and_drops_oldest():
    h = make_handler(cooldown_seconds=0)
    for i in range(alerts.QUEUE_SIZE + 5):
        h.handle(record(f"m{i}"))
    items = drain(h)
    assert len(items) == alerts.QUEUE_SIZE
    assert items[0]["message"] == "m5" and items[-1]["message"] == f"m{alerts.QUEUE_SIZE + 4}"


def test_webhook_failure_retries_does_not_raise_or_recurse(monkeypatch, caplog, capsys):
    attempts = []
    sleeps = []

    def failing_post(url, **kwargs):
        attempts.append(url)
        raise alerts.requests.RequestException(f"cannot reach {url}")

    monkeypatch.setattr(alerts.requests, "post", failing_post)
    h = make_handler(webhook_url=WEBHOOK, sleep=sleeps.append)
    lib_log = logging.getLogger("mc")
    lib_log.addHandler(h)
    try:
        with caplog.at_level(logging.DEBUG, logger="mc"):
            h.handle(record())
            h.deliver(drain(h)[0])
    finally:
        lib_log.removeHandler(h)

    assert len(attempts) == alerts.POST_ATTEMPTS
    assert len(sleeps) == alerts.POST_ATTEMPTS - 1
    assert h.queue.empty()  # no new alert was raised about the failure
    assert all(r.levelno < logging.WARNING for r in caplog.records if r.name == "mc.alerts")
    assert "SECRET-TOKEN" not in capsys.readouterr().err
    assert "SECRET-TOKEN" not in caplog.text


def test_webhook_http_error_status_counts_as_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(alerts.requests, "post",
                        lambda url, **kw: calls.append(1) or type("R", (), {"status_code": 500})())
    h = make_handler(webhook_url=WEBHOOK)
    h.handle(record())
    h.deliver(drain(h)[0])
    assert len(calls) == alerts.POST_ATTEMPTS


def test_stderr_note_is_rate_limited(capsys):
    clock = Clock()
    h = make_handler(clock=clock)
    h._note_failure("one")
    h._note_failure("two")
    assert capsys.readouterr().err.count("[mc.alerts]") == 1
    clock.now += alerts.STDERR_NOTE_INTERVAL_SECONDS
    h._note_failure("three")
    assert capsys.readouterr().err.count("[mc.alerts]") == 1


def test_emit_never_raises(monkeypatch):
    h = make_handler()
    monkeypatch.setattr(h, "_enqueue", lambda a: 1 / 0)
    h.handle(record())  # no exception


def test_worker_thread_delivers(posts):
    h = alerts.AdminAlertHandler(webhook_url=WEBHOOK, in_game=False)
    h.handle(record())
    deadline = time.time() + 5
    while not posts and time.time() < deadline:
        time.sleep(0.01)
    assert len(posts) == 1


@pytest.mark.parametrize("url", ["ftp://x.com/a", "not a url", "javascript:alert(1)", "http://", ""])
def test_invalid_urls_rejected(url):
    assert alerts.validate_webhook_url(url) is None


def test_describe_hides_path():
    assert alerts.describe_webhook_url(WEBHOOK) == "https://hooks.example.com"


def test_create_handler_unset_webhook(monkeypatch, caplog):
    for name in ("MC_ALERT_WEBHOOK_URL", "MC_ALERT_MIN_LEVEL", "MC_ALERT_IN_GAME", "MC_ALERT_COOLDOWN_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.INFO, logger="mc.alerts"):
        h = alerts.create_handler()
    assert h.webhook_url is None and h.level == logging.CRITICAL and h.in_game
    assert h.cooldown_seconds == 30 * 60
    assert [r.levelname for r in caplog.records] == ["INFO"]


def test_create_handler_valid_logs_host_only(monkeypatch, caplog):
    monkeypatch.setenv("MC_ALERT_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("MC_ALERT_MIN_LEVEL", "error")
    monkeypatch.setenv("MC_ALERT_IN_GAME", "false")
    monkeypatch.setenv("MC_ALERT_COOLDOWN_MINUTES", "2")
    with caplog.at_level(logging.INFO, logger="mc.alerts"):
        h = alerts.create_handler()
    assert h.webhook_url == WEBHOOK and h.level == logging.ERROR and not h.in_game and h.cooldown_seconds == 120
    assert "https://hooks.example.com" in caplog.text and "SECRET-TOKEN" not in caplog.text


def test_create_handler_invalid_url_warns_once_and_disables(monkeypatch, caplog):
    monkeypatch.setenv("MC_ALERT_WEBHOOK_URL", "ftp://nope.example.com/x")
    with caplog.at_level(logging.INFO, logger="mc.alerts"):
        h = alerts.create_handler()
    assert h.webhook_url is None
    assert [r.levelname for r in caplog.records].count("WARNING") == 1


def test_invalid_min_level_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("MC_ALERT_MIN_LEVEL", "WARNING")
    with caplog.at_level(logging.WARNING):
        assert config.get_alert_min_level() == logging.CRITICAL
    assert len(caplog.records) == 1


def test_empty_webhook_env_is_unset(monkeypatch):
    monkeypatch.setenv("MC_ALERT_WEBHOOK_URL", "  ")
    assert config.get_alert_webhook_url() is None


# ------------------------------------------------ sustained failure escalation

@pytest.fixture
def counter(monkeypatch):
    monkeypatch.setattr(update, "_consecutive_failures", 0)
    monkeypatch.setattr(update, "_failure_threshold", None)
    monkeypatch.delenv("MC_ALERT_SCRAPE_FAILURE_THRESHOLD", raising=False)


def criticals(caplog):
    return [r for r in caplog.records if r.levelno == logging.CRITICAL]


def test_scrape_failures_escalate_once_at_threshold(counter, caplog):
    with caplog.at_level(logging.DEBUG):
        for _ in range(update.SCRAPE_FAILURE_THRESHOLD - 1):
            update._record_check_failure()
        assert criticals(caplog) == []
        update._record_check_failure()
    crit = criticals(caplog)
    assert len(crit) == 1
    assert f"after {update.SCRAPE_FAILURE_THRESHOLD} consecutive attempts" in crit[0].getMessage()


def test_scrape_success_resets_counter(counter, caplog):
    with caplog.at_level(logging.DEBUG):
        for _ in range(update.SCRAPE_FAILURE_THRESHOLD - 1):
            update._record_check_failure()
        update._record_check_success()
        for _ in range(update.SCRAPE_FAILURE_THRESHOLD - 1):
            update._record_check_failure()
    assert criticals(caplog) == []


def test_scrape_threshold_env_and_realert_message_is_stable(counter, caplog, monkeypatch):
    monkeypatch.setenv("MC_ALERT_SCRAPE_FAILURE_THRESHOLD", "2")
    with caplog.at_level(logging.DEBUG):
        for _ in range(4):
            update._record_check_failure()
    crit = criticals(caplog)
    assert len(crit) == 2
    assert crit[0].getMessage() == crit[1].getMessage()  # identical, so the alert cooldown dedupes them


def test_download_version_if_required_counts_failures(counter, monkeypatch, caplog):
    monkeypatch.setenv("MC_ALERT_SCRAPE_FAILURE_THRESHOLD", "3")
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    monkeypatch.setattr(update, "_get_most_recent_downloaded_version", lambda: None)
    monkeypatch.setattr(update.paths, "get_current_version", lambda *a, **k: None)
    results = iter([None, None, LINK])
    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: next(results))
    monkeypatch.setattr(update.downloads, "download_and_extract", lambda link: False)
    with caplog.at_level(logging.DEBUG):
        assert update.download_version_if_required() is None
    # two link failures plus a failed download reaches the threshold of 3
    assert len(criticals(caplog)) == 1


def test_successful_download_resets_counter(counter, monkeypatch):
    update._record_check_failure()
    monkeypatch.setattr(update, "_get_most_recent_downloaded_version", lambda: None)
    monkeypatch.setattr(update.paths, "get_current_version", lambda *a, **k: None)
    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: LINK)
    monkeypatch.setattr(update.downloads, "download_and_extract", lambda link: True)
    assert update.download_version_if_required() == "1.26.52.3"
    assert update._consecutive_failures == 0
