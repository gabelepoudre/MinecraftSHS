"""
World change detection: a manifest is {relative path: [size, mtime_ns]} for every file in the world directory.

"""

import fnmatch
import json
import logging
import os

_log = logging.getLogger(__name__)

MANIFEST_FILE_NAME = "last_manifest.json"


def _ignored(rel: str, patterns: list[str]) -> bool:
    base = os.path.basename(rel)
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p) for p in patterns)


def build_manifest(world_path: str, ignore_patterns: list[str] | None = None) -> dict[str, list[int]]:
    ignore_patterns = ignore_patterns or []
    manifest: dict[str, list[int]] = {}
    for root, dirs, files in os.walk(world_path):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(src, world_path).replace(os.sep, "/")
            if _ignored(rel, ignore_patterns):
                continue
            try:
                st = os.stat(src)
            except OSError as e:
                _log.debug(f"Could not stat file for manifest: {src}: {e}")
                continue
            manifest[rel] = [st.st_size, st.st_mtime_ns]
    return manifest


def load_manifest(path: str) -> dict[str, list[int]] | None:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: list(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError) as e:
        _log.debug(f"Could not load manifest {path}: {e}")
    return None


def save_manifest(path: str, manifest: dict[str, list[int]]) -> None:
    tmp = path + ".partial"
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, path)
