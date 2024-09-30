import time

import mc
import os
from threading import Thread, RLock
import logging
import datetime
import dotenv

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


def slow_update():
    # assume we know we need an update and have a runtime
    global _current_runtime

    if _current_runtime is None:
        raise RuntimeError("No runtime to update")

    # send a message to the server that we will be updating in 15 minutes
    _current_runtime.send_command("say Server will be restarting in 15 minutes for an update!")

    # sleep for 10 minutes
    time.sleep(600)

    # send a message to the server that we will be updating in 5 minutes
    _current_runtime.send_command("say Server will be restarting in 5 minutes for an update!!")

    # sleep for 4 minutes
    time.sleep(240)

    # send a message to the server that we will be updating in 1 minute
    _current_runtime.send_command("say Server will be restarting in 1 minute for an update!!!")

    # sleep for a minute
    time.sleep(60)

    # send a message to the server that we will be updating now
    _current_runtime.send_command("say Server is restarting for an update!!!!")

    time.sleep(0.5)

    # stop the server
    _current_runtime.stop()

    _current_runtime = None

    # update the version
    update_success = False
    while not update_success:
        update_success = mc.update.try_update()
        if not update_success:
            _log.critical("Update failed, trying again in 5 seconds...")
            time.sleep(5)

    # get the path to the executable
    path_to_exe = mc.paths.get_path_to_minecraft_server_exe()

    # create the runtime
    _current_runtime = mc.ServerRuntime(path_to_exe)

    # start the runtime
    _current_runtime.start()


def maintain_loop():
    global _current_runtime

    while True:
        if _current_runtime is not None:
            # health check that the server is still running
            if _current_runtime.process is not None and _current_runtime.process.poll() is not None:
                _log.critical("Server process has died unceremoniously, restarting after a delay...")
                try:
                    _current_runtime.stop()
                except Exception:  # noqa
                    pass
                _current_runtime = None

                path_to_exe = mc.paths.get_path_to_minecraft_server_exe()
                _current_runtime = mc.ServerRuntime(path_to_exe)
                time.sleep(5)
                _current_runtime.start()

            # check if we need to update
            if mc.update.need_update():
                slow_update()
                # at the end of slow_update, we will have a new runtime
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
        version = mc.downloads.get_version_from_download_link(version_link)
        most_recent_downloaded_version = mc.update._get_most_recent_downloaded_version()  # noqa
        if version != most_recent_downloaded_version:
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
    _current_runtime = mc.ServerRuntime(path_to_exe)

    # start the runtime
    _current_runtime.start()

    # quick enable coordinates
    _current_runtime.send_command("gamerule showcoordinates true")

    # start the maintain loop thread
    maintain_thread = Thread(target=maintain_loop, daemon=True)
    maintain_thread.start()

    while True:
        try:
            command = input()
            if command == "stop":
                _current_runtime.stop()
                break
            _current_runtime.send_command(command)
        except Exception as e:
            _log.error(f"Error writing command: {e}")
            continue
        except KeyboardInterrupt as e:
            _log.info("Exiting...")
            try:
                _current_runtime.stop()
            except BaseException:  # noqa
                pass

            raise e


if __name__ == "__main__":
    main()
