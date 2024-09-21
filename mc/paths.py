import os
import logging

_log = logging.getLogger(__name__)

_path_to_data_dir: str | None = None
_path_to_backup_dir: str | None = None
_path_to_active_dir: str | None = None
_path_to_versions_dir: str | None = None


def get_path_to_data_dir() -> str:
    global _path_to_data_dir
    if _path_to_data_dir is not None:
        return _path_to_data_dir

    # check env var MC_DATA_DIR
    env_var = os.environ.get("MC_DATA_DIR")
    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "")
        env_var = os.path.abspath(os.path.normpath(env_var))

        if os.path.isdir(env_var):
            _path_to_data_dir = env_var
            _log.info(f"Using MC_DATA_DIR: {_path_to_data_dir}")
            return _path_to_data_dir
        else:
            _log.warning(f"MC_DATA_DIR is set to '{env_var}', but it is not a directory")

    _path_to_data_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))  # root/data
    _log.info(f"Using default data directory: {_path_to_data_dir}")
    return _path_to_data_dir


def get_path_to_backup_dir() -> str:
    # either the environment variable, or root/data/backup
    global _path_to_backup_dir
    if _path_to_backup_dir is not None:
        return _path_to_backup_dir

    # check env var MC_BACKUP_DIR
    env_var = os.environ.get("MC_BACKUP_DIR")
    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "")
        env_var = os.path.abspath(os.path.normpath(env_var))

        if os.path.isdir(env_var):
            _path_to_backup_dir = env_var
            _log.info(f"Using MC_BACKUP_DIR: {_path_to_backup_dir}")
            return _path_to_backup_dir
        else:
            _log.warning(f"MC_BACKUP_DIR is set to '{env_var}', but it is not a directory")

    _path_to_backup_dir = os.path.join(get_path_to_data_dir(), "backup")
    _log.info(f"Using default backup directory: {_path_to_backup_dir}")
    return _path_to_backup_dir


def get_path_to_active_dir() -> str:
    # either the environment variable, or root/data/active
    global _path_to_active_dir
    if _path_to_active_dir is not None:
        return _path_to_active_dir

    # check env var MC_ACTIVE_DIR
    env_var = os.environ.get("MC_ACTIVE_DIR")

    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "")
        env_var = os.path.abspath(os.path.normpath(env_var))

        if os.path.isdir(env_var):
            _path_to_active_dir = env_var
            _log.info(f"Using MC_ACTIVE_DIR: {_path_to_active_dir}")
            return _path_to_active_dir
        else:
            _log.warning(f"MC_ACTIVE_DIR is set to '{env_var}', but it is not a directory")

    _path_to_active_dir = os.path.join(get_path_to_data_dir(), "active")
    _log.info(f"Using default active directory: {_path_to_active_dir}")
    return _path_to_active_dir


def get_path_to_versions_dir() -> str:
    # either the environment variable, or root/data/versions
    global _path_to_versions_dir
    if _path_to_versions_dir is not None:
        return _path_to_versions_dir

    # check env var MC_VERSIONS_DIR
    env_var = os.environ.get("MC_VERSIONS_DIR")
    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "")
        env_var = os.path.abspath(os.path.normpath(env_var))

        if os.path.isdir(env_var):
            _path_to_versions_dir = env_var
            _log.info(f"Using MC_VERSIONS_DIR: {_path_to_versions_dir}")
            return _path_to_versions_dir
        else:
            _log.warning(f"MC_VERSIONS_DIR is set to '{env_var}', but it is not a directory")

    _path_to_versions_dir = os.path.join(get_path_to_data_dir(), "versions")
    _log.info(f"Using default versions directory: {_path_to_versions_dir}")
    return _path_to_versions_dir


def get_most_recent_version_dir_in_dir(directory: str) -> str | None:
    """
    """

def get_path_to_minecraft_server_exe() -> str:
    return os.path.join(get_path_to_active_dir(), "bedrock_server.exe")
