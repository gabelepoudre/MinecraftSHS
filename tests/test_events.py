import datetime as dt
import json
import logging
import os
import threading
import types

import pytest

from mc import events, paths, server_runtime, update

UTC = dt.timezone.utc

# BDS console lines, from the known formats (not yet compared against a live server, see step 1.0 of the plan)
JOIN = "[2026-10-05 12:00:00:000 INFO] Player connected: Steve, xuid: 2535412345678901"
JOIN_PFID = "[2026-10-05 12:00:00:000 INFO] Player connected: Steve, xuid: 2535412345678901, pfid: 1a2b3c4d5e6f7a8b"
LEAVE = "[2026-10-05 12:05:00:000 INFO] Player disconnected: Steve, xuid: 2535412345678901, pfid: 1a2b3c4d5e6f7a8b"
READY = "[2026-10-05 11:59:00:000 INFO] Server started."
ACHIEVEMENT = "[2026-10-05 12:01:00:000 INFO] Steve has earned the achievement [Stone Age]"  # unverified format


def read_events(directory):
    """All events in all day files, in file name then line order."""
    out = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            out.extend(json.loads(line) for line in f)
    return out


def set_clock(monkeypatch, when):
    monkeypatch.setattr(events, "_now", lambda: when)


# ---------------------------------------------------------------- emit

def test_emit_creates_dir_and_day_file(events_dir, monkeypatch):
    set_clock(monkeypatch, dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC))
    events.emit(events.PLAYER_JOIN, events.SOURCE_CONSOLE, player="Steve", xuid="2535")
    assert os.listdir(events_dir) == ["2026-10-05.jsonl"]
    text = (events_dir / "2026-10-05.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n") and text.count("\n") == 1
    assert json.loads(text) == {
        "v": 1, "ts": "2026-10-05T12:00:00.000+00:00", "type": "player_join", "source": "console",
        "player": "Steve", "xuid": "2535",
    }


def test_emit_appends_one_line_per_event(events_dir):
    events.emit(events.APP_START, events.SOURCE_APP)
    events.emit(events.SERVER_START, events.SOURCE_APP, version="1.26.52.3")
    events.emit(events.APP_EXIT, events.SOURCE_APP, reason="stop_command\nwith a newline")
    got = read_events(events_dir)
    assert [e["type"] for e in got] == ["app_start", "server_start", "app_exit"]
    assert got[2]["reason"] == "stop_command\nwith a newline"  # escaped, still one line
    for e in got:
        assert e["v"] == events.SCHEMA_VERSION and e["source"] == "app"
        assert dt.datetime.fromisoformat(e["ts"]).utcoffset() == dt.timedelta(0)


def test_fields_cannot_overwrite_common_fields(events_dir):
    events.emit(events.APP_START, events.SOURCE_APP, v=99, ts="nope", type="other")
    (e,) = read_events(events_dir)
    assert e["v"] == 1 and e["type"] == "app_start" and e["ts"] != "nope"


def test_non_json_field_is_written_as_text(events_dir):
    events.emit(events.APP_START, events.SOURCE_APP, thing=object())
    (e,) = read_events(events_dir)
    assert isinstance(e["thing"], str)


def test_day_rollover_at_utc_midnight(events_dir, monkeypatch):
    set_clock(monkeypatch, dt.datetime(2026, 10, 5, 23, 59, 59, 999000, tzinfo=UTC))
    events.emit(events.PLAYER_JOIN, events.SOURCE_CONSOLE, player="Steve", xuid="1")
    set_clock(monkeypatch, dt.datetime(2026, 10, 6, 0, 0, 0, tzinfo=UTC))
    events.emit(events.PLAYER_LEAVE, events.SOURCE_CONSOLE, player="Steve", xuid="1")
    assert sorted(os.listdir(events_dir)) == ["2026-10-05.jsonl", "2026-10-06.jsonl"]
    assert [e["type"] for e in read_events(events_dir)] == ["player_join", "player_leave"]


def test_default_clock_is_utc():
    assert events._now().utcoffset() == dt.timedelta(0)


def test_concurrent_emits_do_not_interleave(events_dir):
    n_threads, per_thread = 8, 200

    def worker(i):
        for j in range(per_thread):
            events.emit(events.PLAYER_JOIN, events.SOURCE_CONSOLE, player=f"p{i}" * 20, n=j)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    got = read_events(events_dir)  # json.loads fails on any interleaved line
    assert len(got) == n_threads * per_thread
    for i in range(n_threads):
        assert [e["n"] for e in got if e["player"] == f"p{i}" * 20] == list(range(per_thread))


def test_write_failure_does_not_raise(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setattr(paths, "_path_to_events_dir", str(blocker / "events"))  # cannot create a dir under a file
    with caplog.at_level(logging.ERROR, logger="mc.events"):
        events.emit(events.APP_START, events.SOURCE_APP)
    assert any(r.levelno == logging.ERROR and "app_start" in r.getMessage() for r in caplog.records)


def test_path_failure_does_not_raise(monkeypatch, caplog):
    def boom():
        raise RuntimeError("no path")

    monkeypatch.setattr(paths, "get_path_to_events_dir", boom)
    with caplog.at_level(logging.ERROR, logger="mc.events"):
        events.emit(events.APP_START, events.SOURCE_APP)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------- events dir

def test_events_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_path_to_events_dir", None)
    target = tmp_path / "custom" / "events"
    monkeypatch.setenv("MC_EVENTS_DIR", f'"{target}"')  # quotes are stripped
    assert paths.get_path_to_events_dir() == os.path.abspath(str(target))
    assert target.is_dir()  # created if missing
    events.emit(events.APP_START, events.SOURCE_APP)
    assert len(read_events(target)) == 1


def test_events_dir_default_is_data_events(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_path_to_events_dir", None)
    monkeypatch.delenv("MC_EVENTS_DIR", raising=False)
    monkeypatch.setattr(paths, "_path_to_data_dir", str(tmp_path / "data"))
    assert paths.get_path_to_events_dir() == os.path.join(str(tmp_path / "data"), "events")
    assert (tmp_path / "data" / "events").is_dir()


def test_events_dir_default_location_without_data_dir_override():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # what get_path_to_data_dir falls back to, so the default events dir is <repo>/data/events
    assert os.path.abspath(os.path.join(os.path.dirname(paths.__file__), "..", "data")) == os.path.join(repo, "data")


# ---------------------------------------------------------------- console parsing

@pytest.mark.parametrize("line, expected", [
    (JOIN, ("player_join", {"player": "Steve", "xuid": "2535412345678901"})),
    (JOIN_PFID, ("player_join", {"player": "Steve", "xuid": "2535412345678901"})),
    (LEAVE, ("player_leave", {"player": "Steve", "xuid": "2535412345678901"})),
    (READY, ("server_ready", {})),
    (ACHIEVEMENT, ("achievement", {"player": "Steve", "achievement": "Stone Age"})),
    # prefix variations
    ("Player connected: Steve, xuid: 2535412345678901", ("player_join", {"player": "Steve", "xuid": "2535412345678901"})),
    ("NO LOG FILE! - [2019-01-01 00:00:00 INFO] Server started.", ("server_ready", {})),
    ("[INFO] Player disconnected: Steve, xuid: 1, pfid: x", ("player_leave", {"player": "Steve", "xuid": "1"})),
    ("[2026-10-05 12:00:00:000 INFO] [Server] Server started.\n", ("server_ready", {})),
    ("   Server started.  \r\n", ("server_ready", {})),
    # names with spaces, empty xuid (offline mode)
    (JOIN.replace("Steve", "Big Steve 42"), ("player_join", {"player": "Big Steve 42", "xuid": "2535412345678901"})),
    ("[INFO] Player connected: Alex, xuid: ", ("player_join", {"player": "Alex", "xuid": None})),
])
def test_console_lines_become_events(events_dir, line, expected):
    event_type, fields = expected
    assert events.parse_console_line(line) == event_type
    (e,) = read_events(events_dir)
    assert e["type"] == event_type and e["source"] == "console"
    assert {k: v for k, v in e.items() if k not in ("v", "ts", "type", "source")} == fields


@pytest.mark.parametrize("line", [
    "",
    "\n",
    "[2026-10-05 12:00:00:000 INFO] Starting Server",
    "[2026-10-05 12:00:00:000 INFO] Version: 1.26.52.3",
    "[2026-10-05 12:00:00:000 INFO] Level Name: Bedrock level",
    "[2026-10-05 12:00:00:000 INFO] Server started with some other wording",
    "[2026-10-05 12:00:00:000 INFO] Player Spawned: Steve xuid: 2535412345678901, pfid: 1a2b",
    # not at the start of the message, e.g. a player typing it in chat
    "[2026-10-05 12:00:00:000 INFO] <Steve> Player connected: Fake, xuid: 1",
    "[2026-10-05 12:00:00:000 INFO] <Steve> Steve has earned the achievement [Fake]",
])
def test_other_lines_are_ignored(events_dir, line):
    assert events.parse_console_line(line) is None
    assert read_events(events_dir) == []


@pytest.mark.parametrize("line", [
    "[2026-10-05 12:00:00:000 INFO] Player connected: Steve",
    "[2026-10-05 12:00:00:000 INFO] Player connected: Steve, xuid: notanumber",
    "[2026-10-05 12:00:00:000 INFO] Player disconnected: , xuid: 1",
])
def test_malformed_matching_lines_log_debug(events_dir, line, caplog):
    with caplog.at_level(logging.DEBUG, logger="mc.events"):
        assert events.parse_console_line(line) is None
    assert read_events(events_dir) == []
    assert any(r.levelno == logging.DEBUG and "could not be parsed" in r.getMessage() for r in caplog.records)


def test_parse_never_raises(events_dir):
    assert events.parse_console_line(None) is None  # noqa


def test_console_session_end_to_end(events_dir, monkeypatch):
    start = dt.datetime(2026, 10, 5, 23, 58, tzinfo=UTC)
    times = iter(start + dt.timedelta(minutes=i) for i in range(4))
    monkeypatch.setattr(events, "_now", lambda: next(times))
    for line in [READY, JOIN, "[2026-10-05 23:59:00:000 INFO] Running AutoCompaction...", LEAVE, JOIN_PFID]:
        events.parse_console_line(line + "\n")
    assert sorted(os.listdir(events_dir)) == ["2026-10-05.jsonl", "2026-10-06.jsonl"]
    got = read_events(events_dir)
    assert [e["type"] for e in got] == ["server_ready", "player_join", "player_leave", "player_join"]


# ---------------------------------------------------------------- lifecycle: ServerRuntime

class FakeProcess:
    """Stands in for subprocess.Popen: stdout is a fixed list of lines, poll() is None until killed or crashed."""

    def __init__(self, stdout_lines=()):
        self.stdout = list(stdout_lines)
        self.stderr = []
        self.returncode = None
        self.sent = []
        process = self

        class Stdin:
            def write(self, text):
                if process.returncode is not None:
                    raise OSError("broken pipe")
                process.sent.append(text)

            def flush(self):
                pass

        self.stdin = Stdin()

    def poll(self):
        return self.returncode

    def kill(self):
        if self.returncode is None:
            self.returncode = 1

    def wait(self):
        return self.returncode

    def terminate(self):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A ServerRuntime with a fake process, no background threads and no sleeping."""
    server = tmp_path / "server"
    server.mkdir()
    (server / "bedrock_server.exe").write_bytes(b"")
    (server / "server.properties").write_text("level-name=w\n")

    procs = []

    def fake_popen(*args, **kwargs):
        procs.append(FakeProcess([JOIN + "\n", "[INFO] something else\n", LEAVE + "\n"]))
        return procs[-1]

    monkeypatch.setattr(server_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_runtime, "time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(server_runtime.ServerRuntime, "_backup_thread", lambda self: None)
    monkeypatch.setattr(server_runtime.ServerRuntime, "_startup_commands_thread", lambda self: None)
    monkeypatch.setattr(paths, "get_current_version", lambda *a, **k: "1.26.52.3")

    rt = server_runtime.ServerRuntime(str(server / "bedrock_server.exe"))
    rt.procs = procs
    yield rt
    # stop while the patches are still in place, otherwise __del__ stops it later and writes to the real events dir
    for p in procs:
        p.returncode = 0
    rt.process = None


def test_runtime_start_and_stop_events(runtime, events_dir):
    runtime.start()
    runtime.stop(reason="update")
    got = read_events(events_dir)
    types_ = [e["type"] for e in got]
    # stdout lines are parsed on the packer thread, which stop() joins, so they are all in before server_stop
    assert types_[0] == "server_start" and types_[-1] == "server_stop"
    assert sorted(types_[1:-1]) == ["player_join", "player_leave"]
    assert got[0]["version"] == "1.26.52.3" and got[0]["source"] == "app"
    assert got[-1]["reason"] == "update"


def test_runtime_stop_default_reason_is_manual(runtime, events_dir):
    runtime.start()
    runtime.stop()
    last = read_events(events_dir)[-1]
    assert last["type"] == "server_stop" and last["reason"] == "manual"


def test_runtime_stop_after_crash_writes_no_server_stop(runtime, events_dir):
    runtime.start()
    runtime.procs[0].returncode = 3  # died on its own
    with pytest.raises(OSError):
        runtime.stop()  # as today: sending "stop" to a dead process fails, the caller ignores it
    assert "server_stop" not in [e["type"] for e in read_events(events_dir)]


def test_backup_events(runtime, events_dir, monkeypatch):
    runtime.start()
    monkeypatch.setattr(runtime, "_backup_world_files", lambda: True)
    monkeypatch.setattr(runtime, "_prune_world_backups", lambda: None)
    runtime.backup()
    monkeypatch.setattr(runtime, "_backup_world_files", lambda: False)
    runtime.backup()

    def fail():
        raise FileNotFoundError("World directory does not exist")

    monkeypatch.setattr(runtime, "_backup_world_files", fail)
    with pytest.raises(FileNotFoundError):
        runtime.backup()
    assert "save resume\n" in runtime.procs[0].sent  # still resumed

    got = [e for e in read_events(events_dir) if e["type"].startswith("backup")]
    assert [(e["type"], e.get("skipped")) for e in got] == [
        ("backup_completed", False), ("backup_completed", True), ("backup_failed", None)]
    assert all(e["level"] == "w" for e in got)
    assert "World directory does not exist" in got[2]["error"]


# ---------------------------------------------------------------- lifecycle: update

def test_update_failed_and_installed_events(tmp_path, events_dir, monkeypatch):
    active, versions = tmp_path / "active", tmp_path / "versions"
    (active / "current").mkdir(parents=True)
    (active / ".version").write_text("1.0.0.1")
    (versions / "1.0.0.2").mkdir(parents=True)
    (versions / "1.0.0.2" / "bedrock_server.exe").write_bytes(b"")
    monkeypatch.setattr(paths, "_path_to_active_dir", str(active))
    monkeypatch.setattr(paths, "_path_to_versions_dir", str(versions))
    monkeypatch.setattr(paths, "_path_to_backup_dir", str(tmp_path / "backup"))

    # first attempt: the copy fails
    real_copytree = update.shutil.copytree

    def failing_copytree(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(update.shutil, "copytree", failing_copytree)
    with pytest.raises(OSError):
        update.try_update()
    os.remove(active / ".updating_to")

    monkeypatch.setattr(update.shutil, "copytree", real_copytree)
    assert update.try_update() is True

    got = read_events(events_dir)
    assert [e["type"] for e in got] == ["update_failed", "update_installed"]
    assert got[0]["from_version"] == "1.0.0.1" and got[0]["to_version"] == "1.0.0.2" and "disk full" in got[0]["error"]
    assert got[1]["from_version"] == "1.0.0.1" and got[1]["to_version"] == "1.0.0.2"


# ---------------------------------------------------------------- lifecycle: run_mc_server

class StopLoop(Exception):
    pass


class FakeRuntime:
    def __init__(self, path_to_exe=None, process=None):
        self.process = process or FakeProcess()
        self.commands = []
        self.stop_reasons = []
        self.started_ = False

    def start(self):
        self.started_ = True

    def send_command(self, command):
        self.commands.append(command)

    def backup(self):
        pass

    def stop(self, reason="manual"):
        self.stop_reasons.append(reason)


@pytest.fixture
def app(monkeypatch):
    import run_mc_server
    from mc import alerts
    monkeypatch.setattr(run_mc_server.mc, "ServerRuntime", FakeRuntime)
    monkeypatch.setattr(run_mc_server.mc.paths, "get_path_to_minecraft_server_exe", lambda *a, **k: "x.exe")
    monkeypatch.setattr(run_mc_server.mc.paths, "get_current_version", lambda *a, **k: "1.0.0.1")
    monkeypatch.setattr(run_mc_server.mc.update, "_get_most_recent_downloaded_version", lambda: "1.0.0.2")
    monkeypatch.setattr(run_mc_server.mc.update, "try_update", lambda: True)
    monkeypatch.setattr(run_mc_server, "time", types.SimpleNamespace(sleep=lambda s: None, monotonic=lambda: 0.0))
    yield run_mc_server
    run_mc_server._current_runtime = None
    alerts.set_in_game_sender(None)


@pytest.mark.parametrize("update_, reason", [(True, "update"), (False, "daily_restart")])
def test_restart_sequence_events(app, events_dir, update_, reason):
    app._set_runtime(FakeRuntime())
    old = app._current_runtime
    app.restart_sequence("whatever", update=update_)
    (e,) = read_events(events_dir)  # server_stop/start come from the real ServerRuntime, faked here
    assert e["type"] == "restart_scheduled" and e["reason"] == reason and e["countdown_seconds"] == 900
    assert old.stop_reasons == [reason]
    assert app._current_runtime is not old and app._current_runtime.started_


def test_maintain_loop_crash_and_update_available(app, events_dir, monkeypatch):
    crashed = FakeProcess()
    crashed.returncode = 7
    app._set_runtime(FakeRuntime(process=crashed))
    monkeypatch.delenv("MC_DAILY_RESTART_UTC", raising=False)
    monkeypatch.setattr(app.mc.update, "need_update", lambda: True)
    monkeypatch.setattr(app, "slow_update", lambda: None)

    def sleep(seconds):
        if seconds == 1:  # the end of the first loop iteration
            raise StopLoop()

    monkeypatch.setattr(app, "time", types.SimpleNamespace(sleep=sleep, monotonic=lambda: 0.0))
    with pytest.raises(StopLoop):
        app.maintain_loop()

    got = read_events(events_dir)
    assert [e["type"] for e in got] == ["server_crash", "update_available"]
    assert got[0]["exit_code"] == 7 and got[0]["version"] == "1.0.0.1"
    assert got[1]["from_version"] == "1.0.0.1" and got[1]["to_version"] == "1.0.0.2"


@pytest.mark.parametrize("raised, reason", [(None, "stop_command"), (KeyboardInterrupt, "keyboard_interrupt"),
                                            (RuntimeError, "error")])
def test_main_app_start_and_exit(app, events_dir, monkeypatch, raised, reason):
    loggers = [logging.getLogger(n) for n in ("mc", "out", app.__name__)]
    before = [(lg, list(lg.handlers), lg.level) for lg in loggers]
    monkeypatch.setattr(app, "ThreadSafeFileLogger", logging.NullHandler)
    monkeypatch.setattr(app.mc.alerts, "create_handler", logging.NullHandler)

    def run():
        if raised is not None:
            raise raised()

    monkeypatch.setattr(app, "_run", run)
    try:
        if raised is None:
            app.main()
        else:
            with pytest.raises(raised):
                app.main()
    finally:
        for lg, handlers, level in before:
            lg.handlers[:] = handlers
            lg.setLevel(level)

    got = read_events(events_dir)
    assert [e["type"] for e in got] == ["app_start", "app_exit"]
    assert got[1]["reason"] == reason
