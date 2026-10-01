"""
Commands sent to the server every time it starts, read from a plain text file.

The file is created from the versioned template (startup_commands.template.txt) if it does not exist. The path comes
from MC_STARTUP_COMMANDS_FILE, defaulting to startup_commands.txt in the project root.

File format, one entry per line:
    - blank lines and lines starting with # are ignored
    - $$sleep(N) waits N seconds (N may be fractional) before the next line
    - anything else is sent to the server as a command

We only pipe text into the server's stdin, so we cannot see how the server responds. Use $$sleep to space out commands.

"""

import logging
import os
import re
import shutil
import time
from typing import Callable

_log = logging.getLogger(__name__)

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE_PATH = os.path.join(_ROOT_DIR, "startup_commands.template.txt")
_DEFAULT_PATH = os.path.join(_ROOT_DIR, "startup_commands.txt")

_DIRECTIVE_PREFIX = "$$"
_SLEEP_RE = re.compile(r"^\$\$sleep\(\s*(\d+(?:\.\d+)?)\s*\)$", re.IGNORECASE)


def get_path_to_startup_commands_file() -> str:
    env_var = os.environ.get("MC_STARTUP_COMMANDS_FILE")
    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "").strip()
        if env_var:
            return os.path.abspath(os.path.normpath(env_var))
    return _DEFAULT_PATH


def ensure_startup_commands_file() -> str:
    """
    Return the path to the startup commands file, creating it from the template if it does not exist.

    """
    path = get_path_to_startup_commands_file()
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(_TEMPLATE_PATH, path)
            _log.info(f"Created startup commands file from template: {path}")
        except OSError as e:
            _log.error(f"Could not create startup commands file {path}: {e}")
    return path


def parse_startup_commands(text: str) -> list[tuple[str, str | float]]:
    """
    Parse file contents into ("command", text) and ("sleep", seconds) steps. Unknown $$ directives are skipped.

    """
    steps: list[tuple[str, str | float]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith(_DIRECTIVE_PREFIX):
            match = _SLEEP_RE.match(line)
            if match:
                steps.append(("sleep", float(match.group(1))))
            else:
                _log.warning(f"Unknown or malformed startup directive, skipping: {line}")
            continue

        steps.append(("command", line))
    return steps


def run_startup_commands(send_command: Callable[[str], None], is_running: Callable[[], bool]):
    """
    Send every startup command in order. Intended to be run in a daemon thread, since $$sleep blocks.

    :param send_command: Called with each command string
    :param is_running: Checked before each step, so we stop quietly if the server stopped in the meantime
    """
    path = ensure_startup_commands_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            steps = parse_startup_commands(f.read())
    except OSError as e:
        _log.error(f"Could not read startup commands file {path}: {e}")
        return

    for kind, value in steps:
        if not is_running():
            _log.info("Server stopped before startup commands finished, abandoning the rest")
            return
        if kind == "sleep":
            time.sleep(value)  # noqa
        else:
            try:
                send_command(value)  # noqa
            except Exception as e:
                _log.error(f"Error sending startup command '{value}': {e}")
