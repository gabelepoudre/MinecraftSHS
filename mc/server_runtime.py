"""
Holds the ServerRuntime class, for interacting with the runtime environment

"""

import os
import shutil
import subprocess
import logging
import time
import datetime
from threading import Thread, RLock
from mc import paths

_print_log = logging.getLogger("out")
_log = logging.getLogger(__name__)


class ServerRuntime:
    def __init__(self, path_to_exe: str):
        if not os.path.isfile(path_to_exe):
            raise FileNotFoundError(f"Could not find executable: {path_to_exe}")

        self.path_to_exe = path_to_exe
        self._current_level_name = None
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None

        self.__lock = RLock()

    def __del__(self):
        try:
            self.stop()
        except Exception:  # noqa
            pass

    def __stdout_packer(self):
        _print_log.info("Starting stdout packer")
        try:
            for line in self._process.stdout:
                _print_log.info(f"{line}")
        except Exception as e:
            _log.error(f"Error reading stdout: {e}, dying...")

    def __stderr_packer(self):
        _print_log.info("Starting stderr packer")
        try:
            for line in self._process.stderr:
                _print_log.error(f"{line}")
        except Exception as e:
            _log.error(f"Error reading stderr: {e}, dying...")

    def start(self):
        """

        :return:
        """
        self.get_current_level_name()  # initialize level name before starting

        with self.__lock:
            if self._process is not None:
                raise RuntimeError("Process already running")

            self._process = subprocess.Popen(
                self.path_to_exe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                universal_newlines=True
            )
            self._stdout_thread = Thread(target=self.__stdout_packer)
            self._stderr_thread = Thread(target=self.__stderr_packer)
            Thread(target=self._backup_thread, daemon=True).start()
            self._stdout_thread.start()
            self._stderr_thread.start()

    def get_current_level_name(self):
        if self._current_level_name is not None:
            return self._current_level_name
        else:
            root_path = os.path.dirname(self.path_to_exe)
            server_properties = os.path.join(root_path, "server.properties")
            if not os.path.isfile(server_properties):
                raise FileNotFoundError(f"Could not find server.properties: {server_properties}")
            with open(server_properties, "r") as f:
                for line in f:
                    if line.startswith("level-name="):
                        self._current_level_name = line.split("=")[1].strip()
                        return self._current_level_name

    def started(self, blocking: bool = True) -> bool:
        """

        :return:
        """

        if blocking:
            with self.__lock:
                return self._process is not None
        else:
            return self._process is not None

    def send_command(self, message: str):
        if not self.started():
            raise RuntimeError("Server not started")

        with self.__lock:
            self._process.stdin.write((message + "\n"))
            _print_log.info(f">>> {message}")
            self._process.stdin.flush()

    def backup(self):
        # we aren't going to bother trying to read the output, so we will just do it in a dirtier way
        if not self.started():
            raise RuntimeError("Server not started")

        self.send_command("say Backing up server...")
        time.sleep(0.5)

        self.send_command("save hold")
        time.sleep(10)
        self.send_command("save query")
        time.sleep(1)
        # okay, now let's just copy whatever files we can

        backup_dir = paths.get_path_to_backup_dir()
        if not os.path.exists(backup_dir):
            os.mkdir(backup_dir)

        backup_subdir = os.path.join(backup_dir, self.get_current_level_name())
        backup_subdir_current_time = os.path.join(backup_subdir, datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))

        root_path = os.path.dirname(self.path_to_exe)
        world_path = os.path.join(root_path, "worlds", self.get_current_level_name())

        # for each file flatly and subdir, try to copy it. If it fails, debug log it and continue
        with self.__lock:
            for root, dirs, files in os.walk(world_path):
                for file in files:
                    try:
                        src = os.path.join(root, file)
                        dst = os.path.join(backup_subdir_current_time, file)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy(src, dst)
                    except Exception as e:
                        _log.debug(f"Error copying file in backup: {e}")

        self.send_command("save resume")
        self.send_command("say Backup complete!")

    def _backup_thread(self):
        while True:  # daemon thread
            try:
                time.sleep(60 * 60)
                self.backup()
            except Exception as e:
                _log.error(f"!!! Error in backup thread: {e}")
                break

    def stop(self):
        if not self.started():
            return

        with self.__lock:
            self.send_command("stop")
            pro: subprocess.Popen = self._process
            self._process = None
            time.sleep(5)  # give it a chance to stop gracefully
            pro.kill()
            pro.wait()
            pro.terminate()
        try:
            self._stdout_thread.join()
        except Exception as e:
            _log.error(f"Error joining print thread: {e}")

        try:
            self._stderr_thread.join()
        except Exception as e:
            _log.error(f"Error joining print thread: {e}")

        self._stdout_thread = None
        self._stderr_thread = None
