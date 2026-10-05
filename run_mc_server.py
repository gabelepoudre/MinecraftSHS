import time

import mc
import os
from threading import Thread, RLock
import logging
import datetime
import dotenv
from mc import events

dotenv.load_dotenv(".env")

_log = logging.getLogger(__name__)

_current_runtime: mc.ServerRuntime | None = None


class ThreadSafeFileLogger(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.lock = RLock()  # noqa

    def emit(self, record):
        """
        Emit a record to the file, which will be in the log dir, and will be to the current day
        """
        current_time = datetime.datetime.now()
        log_dir = mc.paths.get_path_to_logs_dir()
        log_file = os.path.join(log_dir, f"{current_time.strftime('%Y-%m-%d')}.log")

        with self.lock:
            with open(log_file, "a") as f:
                f.write(f"{record.asctime} - {record.name} - {record.levelname} - {record.message}\n")


def _set_runtime(runtime: mc.ServerRuntime | None):
    """
    Set the current runtime and tell the admin alerts which runtime (if any) can receive in-game messages.

    """
    global _current_runtime
    _current_runtime = runtime
    mc.alerts.set_in_game_sender(runtime.send_command if runtime is not None else None)


def _get_daily_restart_time() -> datetime.time | None:
    """
    Parse MC_DAILY_RESTART_UTC (HH:MM, UTC) from the environment. Returns None if unset or invalid (feature disabled).

    """
    raw = os.environ.get("MC_DAILY_RESTART_UTC")
    if raw is None:
        return None

    raw = raw.replace("'", "").replace('"', "").strip()
    if not raw:
        return None

    try:
        return datetime.datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        _log.warning(f"MC_DAILY_RESTART_UTC is set to '{raw}', but it is not a valid HH:MM time, ignoring")
        return None


def _daily_restart_due(restart_time: datetime.time, last_restart: datetime.datetime) -> bool:
    """
    True if today's scheduled restart time (UTC) has passed and we haven't restarted since it.

    """
    now = datetime.datetime.now(datetime.timezone.utc)
    scheduled_today = datetime.datetime.combine(now.date(), restart_time, tzinfo=datetime.timezone.utc)
    return now >= scheduled_today > last_restart


def restart_sequence(reason: str, update: bool):
    """
    Shared graceful restart: warn players over 15 minutes, back up, stop, optionally update, and start a new runtime.

    :param reason: Short human-readable reason shown to players, e.g. "an update"
    :param update: If True, run the update process while the server is stopped
    """
    global _current_runtime

    if _current_runtime is None:
        raise RuntimeError("No runtime to restart")

    # restart_sequence is only used for updates and the daily restart
    event_reason = "update" if update else "daily_restart"
    events.emit(events.RESTART_SCHEDULED, events.SOURCE_APP, reason=event_reason, countdown_seconds=15 * 60)

    # send a message to the server that we will be restarting in 15 minutes
    _current_runtime.send_command(f"say Server will be restarting in 15 minutes for {reason}!")

    # sleep for 10 minutes
    time.sleep(600)

    # send a message to the server that we will be restarting in 5 minutes
    _current_runtime.send_command(f"say Server will be restarting in 5 minutes for {reason}!!")

    # sleep for 4 minutes
    time.sleep(240)

    # send a message to the server that we will be restarting in 1 minute
    _current_runtime.send_command(f"say Server will be restarting in 1 minute for {reason}!!!")

    # sleep for a minute
    time.sleep(60)

    # send a message to the server that we will be restarting now
    _current_runtime.send_command(f"say Server is restarting for {reason}!!!!")

    time.sleep(0.5)

    # backup just in case (the update process also makes its own backup of the current version)
    try:
        _current_runtime.backup()
    except Exception as e:
        _log.error(f"Backup before restart failed, restarting anyway: {e}")

    # stop the server
    mc.alerts.set_in_game_sender(None)
    _current_runtime.stop(reason=event_reason)

    _set_runtime(None)

    if update:
        update_success = False
        while not update_success:
            update_success = mc.update.try_update()
            if not update_success:
                _log.critical("Update failed, trying again in 5 seconds...")
                time.sleep(5)

    # get the path to the executable
    path_to_exe = mc.paths.get_path_to_minecraft_server_exe()

    # create the runtime
    _set_runtime(mc.ServerRuntime(path_to_exe))

    # start the runtime
    _current_runtime.start()


def slow_update():
    # assume we know we need an update and have a runtime
    restart_sequence("an update", update=True)


def maintain_loop():
    global _current_runtime

    daily_restart_time = _get_daily_restart_time()
    if daily_restart_time is not None:
        _log.info(f"Daily restart enabled at {daily_restart_time.strftime('%H:%M')} UTC")
    # starting counts as a restart, so starting after today's time does not immediately restart
    last_restart = datetime.datetime.now(datetime.timezone.utc)
    last_daily_check = 0.0

    while True:
        if _current_runtime is not None:
            # health check that the server is still running
            if _current_runtime.process is not None and _current_runtime.process.poll() is not None:
                _log.critical("Server process has died unceremoniously, restarting after a delay...")
                events.emit(events.SERVER_CRASH, events.SOURCE_APP,
                            version=mc.paths.get_current_version(), exit_code=_current_runtime.process.poll())
                mc.alerts.set_in_game_sender(None)
                try:
                    _current_runtime.stop()
                except Exception:  # noqa
                    pass
                _set_runtime(None)

                path_to_exe = mc.paths.get_path_to_minecraft_server_exe()
                _set_runtime(mc.ServerRuntime(path_to_exe))
                time.sleep(5)
                _current_runtime.start()

            # check if we need to update
            if mc.update.need_update():
                events.emit(events.UPDATE_AVAILABLE, events.SOURCE_APP, from_version=mc.paths.get_current_version(),
                            to_version=mc.update._get_most_recent_downloaded_version())  # noqa
                slow_update()
                last_restart = datetime.datetime.now(datetime.timezone.utc)
                # at the end of slow_update, we will have a new runtime
            elif daily_restart_time is not None and time.monotonic() - last_daily_check >= 60:
                # check the daily restart roughly once a minute
                last_daily_check = time.monotonic()
                if _daily_restart_due(daily_restart_time, last_restart):
                    _log.info("Daily restart time reached, restarting...")
                    restart_sequence("the daily restart", update=False)
                    last_restart = datetime.datetime.now(datetime.timezone.utc)
        else:
            raise RuntimeError("No runtime, cannot continue...")

        time.sleep(1)


def main():
    global _current_runtime

    lib_log = logging.getLogger("mc")
    lib_log.setLevel(logging.DEBUG)

    out_log = logging.getLogger("out")
    out_log.setLevel(logging.INFO)

    _log.setLevel(logging.DEBUG)

    # attach the logger to the console
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    lib_log.addHandler(ch)
    out_log.addHandler(ch)
    _log.addHandler(ch)
    # add a file handler to the logs directory
    fh = ThreadSafeFileLogger()
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    lib_log.addHandler(fh)
    out_log.addHandler(fh)
    _log.addHandler(fh)

    # admin alerts (CRITICAL logs by default): in game, and to a webhook if MC_ALERT_WEBHOOK_URL is set. Attached to the
    # application loggers only, not "out" (raw server output)
    alert_handler = mc.alerts.create_handler()
    lib_log.addHandler(alert_handler)
    _log.addHandler(alert_handler)

    # app_start/app_exit bracket everything, so gaps where the whole app was down are visible in the event log (a
    # killed process or closed console window writes no app_exit, which the gap also shows)
    events.emit(events.APP_START, events.SOURCE_APP)
    exit_reason = "error"
    try:
        _run()
        exit_reason = "stop_command"
    except KeyboardInterrupt:
        exit_reason = "keyboard_interrupt"
        raise
    finally:
        events.emit(events.APP_EXIT, events.SOURCE_APP, reason=exit_reason)


def _run():
    """Update if needed, start the server and the maintain loop, then pass console input to the server until "stop"."""
    global _current_runtime

    # check if we need to update
    if mc.update.need_update():
        _log.info("Updating server...")
        mc.update.download_version_if_required()
        success_update = False
        while not success_update:
            success_update = mc.update.try_update()
            if not success_update:
                raise RuntimeError("Update failed, cannot start server")
    else:
        # get the most recent version from site in case we are not updating
        version_link = mc.downloads.get_latest_download_link()
        version = mc.downloads.get_version_from_download_link(version_link) if version_link is not None else None
        most_recent_downloaded_version = mc.update._get_most_recent_downloaded_version()  # noqa
        if version is None:
            _log.warning("Could not determine the most recent version from site, starting with what we have")
        elif most_recent_downloaded_version is None or mc.versions.is_newer(version, most_recent_downloaded_version):
            _log.warning("Most recent downloaded version does not match most recent version from site. Downloading")
            mc.update.download_version_if_required()
            _log.info("Trying on-start update...")
            success_update = False
            while not success_update:
                success_update = mc.update.try_update()
                if not success_update:
                    raise RuntimeError("Update failed, cannot start server")

    # start a thread to scrape for new updates (decoupled from the actual update process)
    update_thread = Thread(target=mc.update.get_most_recent_update_thread, daemon=True)
    update_thread.start()

    # get the path to the executable
    path_to_exe = mc.paths.get_path_to_minecraft_server_exe()

    # create the runtime
    _set_runtime(mc.ServerRuntime(path_to_exe))

    # start the runtime
    _current_runtime.start()

    # start the maintain loop thread
    maintain_thread = Thread(target=maintain_loop, daemon=True)
    maintain_thread.start()

    while True:
        try:
            command = input()
            if command == "stop":
                _current_runtime.stop(reason="manual")
                break
            _current_runtime.send_command(command)
        except Exception as e:
            _log.error(f"Error writing command: {e}")
            continue
        except KeyboardInterrupt as e:
            _log.info("Exiting...")
            try:
                _current_runtime.stop(reason="exit")
            except BaseException:  # noqa
                pass

            raise e


if __name__ == "__main__":
    main()
