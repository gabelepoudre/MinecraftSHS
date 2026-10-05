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
from mc import paths, config, retention, manifest, startup_commands, events
import zipfile

_print_log = logging.getLogger("out")
_log = logging.getLogger(__name__)


def _current_version_or_none() -> str | None:
    try:
        return paths.get_current_version()
    except Exception:  # noqa
        return None


class ServerRuntime:
    def __init__(self, path_to_exe: str):
        if not os.path.isfile(path_to_exe):
            raise FileNotFoundError(f"Could not find executable: {path_to_exe}")

        self.path_to_exe = path_to_exe
        self._current_level_name = None
        self.process = None
        self._stdout_thread = None
        self._stderr_thread = None

        self.__lock = RLock()

    def __del__(self):
        try:
            self.stop(reason="exit")
        except Exception:  # noqa
            pass

    def __stdout_packer(self):
        _print_log.info("Starting stdout packer")
        try:
            for line in self.process.stdout:
                _print_log.info(f"{line}")
                events.parse_console_line(line)  # player joins/leaves etc. to the event log, never raises
        except Exception as e:
            _log.error(f"Error reading stdout: {e}, dying...")

    def __stderr_packer(self):
        _print_log.info("Starting stderr packer")
        try:
            for line in self.process.stderr:
                _print_log.error(f"{line}")
        except Exception as e:
            _log.error(f"Error reading stderr: {e}, dying...")

    def start(self):
        """

        :return:
        """
        self.get_current_level_name()  # initialize level name before starting

        with self.__lock:
            if self.process is not None:
                raise RuntimeError("Process already running")

            self.process = subprocess.Popen(
                self.path_to_exe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                universal_newlines=True
            )
            # before the output threads start, so it comes before any console event of this run
            events.emit(events.SERVER_START, events.SOURCE_APP, version=_current_version_or_none())

            self._stdout_thread = Thread(target=self.__stdout_packer)
            self._stderr_thread = Thread(target=self.__stderr_packer)
            Thread(target=self._backup_thread, daemon=True).start()
            self._stdout_thread.start()
            self._stderr_thread.start()
            Thread(target=self._startup_commands_thread, daemon=True).start()

    def _startup_commands_thread(self):
        try:
            startup_commands.run_startup_commands(self.send_command, lambda: self.started(blocking=False))
        except Exception as e:
            _log.error(f"Error running startup commands: {e}")

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
                return self.process is not None
        else:
            return self.process is not None

    def send_command(self, message: str):
        if not self.started():
            raise RuntimeError("Server not started")

        with self.__lock:
            self.process.stdin.write((message + "\n"))
            _print_log.info(f">>> {message}")
            self.process.stdin.flush()

    def backup(self):
        # we aren't going to bother trying to read the output, so we will just do it in a dirtier way
        if not self.started():
            raise RuntimeError("Server not started")

        self.send_command("say Backing up server...")
        time.sleep(0.5)

        held = False
        try:
            held = True  # set first: even if the write fails part way, we want to try to resume
            self.send_command("save hold")
            time.sleep(10)
            self.send_command("save query")
            time.sleep(1)
            # okay, now let's just copy whatever files we can
            created = self._backup_world_files()
        except Exception as e:
            events.emit(events.BACKUP_FAILED, events.SOURCE_APP, level=self._current_level_name, error=str(e))
            raise
        finally:
            if held:
                try:
                    self.send_command("save resume")
                except Exception as e:
                    _log.error(f"!!! Failed to send 'save resume' after backup: {e}")

        events.emit(events.BACKUP_COMPLETED, events.SOURCE_APP, level=self._current_level_name, skipped=not created)
        if created:
            self.send_command("say Backup complete!")
            self._prune_world_backups()
        else:
            self.send_command("say No world changes, backup skipped")

    def _backup_world_files(self) -> bool:
        """
        Zip the world directory. Returns False if the world is unchanged since the last backup (nothing written).
        """
        level_name = self.get_current_level_name()
        backup_dir = paths.get_path_to_backup_dir()
        backup_subdir = os.path.join(backup_dir, level_name)
        os.makedirs(backup_subdir, exist_ok=True)

        root_path = os.path.dirname(self.path_to_exe)
        world_path = os.path.join(root_path, "worlds", level_name)
        if not os.path.isdir(world_path):
            raise FileNotFoundError(f"World directory does not exist: {world_path}")

        backup_file = os.path.join(
            backup_subdir,
            datetime.datetime.now().strftime(retention.TIMESTAMP_FORMAT + ".zip")
        )
        partial_file = backup_file + ".partial"
        manifest_file = os.path.join(backup_subdir, manifest.MANIFEST_FILE_NAME)

        with self.__lock:
            new_manifest = manifest.build_manifest(world_path, config.get_manifest_ignore_patterns())
            has_previous_backup = any(
                retention.parse_world_backup_name(n) is not None for n in os.listdir(backup_subdir)
            )
            if has_previous_backup and manifest.load_manifest(manifest_file) == new_manifest:
                _log.info(f"World '{level_name}' unchanged since last backup, skipping")
                return False

            failed = 0
            try:
                # open the zip once, but keep the per-file try/except so one locked file doesn't abort the backup
                with zipfile.ZipFile(partial_file, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zip_ref:
                    for root, dirs, files in os.walk(world_path):
                        for file in files:
                            src = os.path.join(root, file)
                            try:
                                zip_ref.write(src, os.path.relpath(src, world_path))
                            except Exception as e:
                                failed += 1
                                _log.warning(f"Error copying file in backup: {src}: {e}")
                os.replace(partial_file, backup_file)
            except BaseException:
                try:
                    os.remove(partial_file)
                except OSError:
                    pass
                raise

        if failed:
            _log.warning(f"Backup {backup_file} completed with {failed} file(s) skipped")
        try:
            manifest.save_manifest(manifest_file, new_manifest)
        except Exception as e:
            _log.error(f"Failed to save backup manifest: {e}")
        _log.info(f"Created backup: {backup_file}")
        return True

    def _prune_world_backups(self):
        try:
            backup_subdir = os.path.join(paths.get_path_to_backup_dir(), self.get_current_level_name())
            retention.prune_dir(
                backup_subdir,
                retention.parse_world_backup_name,
                config.get_world_backup_policy(),
                dry_run=config.get_prune_dry_run(),
            )
        except Exception as e:
            _log.error(f"Error pruning world backups: {e}")

    def _backup_thread(self):
        while True:  # daemon thread
            try:
                time.sleep(60 * 60)
                if not self.started(blocking=False):
                    _log.info("Server stopped, ending backup thread")
                    return
                self.backup()
            except Exception as e:
                _log.error(f"!!! Error in backup thread: {e}", exc_info=e)

    def stop(self, reason: str = "manual"):
        """
        Stop the server (gracefully, then kill it after 5 seconds).

        :param reason: for the server_stop event: daily_restart, update, manual or exit. No event is written if the
        process had already died (that is a server_crash, logged by the caller)
        """
        if not self.started():
            return

        with self.__lock:
            was_running = self.process.poll() is None
            self.send_command("stop")
            pro: subprocess.Popen = self.process
            self.process = None
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

        if was_running:
            events.emit(events.SERVER_STOP, events.SOURCE_APP, reason=reason)
