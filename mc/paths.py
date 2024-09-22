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


def get_current_version(fail_on_updating: bool = False) -> str | None:
    """
    Get the current version of the server, or None if it is not found.

    We do this by first looking for the .updating_to file it the active directory. If it exists, we log a warning and
    return the contents of the file, which should be the version we are updating to.

    If the .updating_to file does not exist, we look for the .version file in the active directory. If it exists, we
    return the contents of the file, which should be the current version.

    If neither file exists, we return None.

    """

    active_dir = get_path_to_active_dir()

    updating_to_file = os.path.join(active_dir, ".updating_to")
    if os.path.exists(updating_to_file):
        if fail_on_updating:
            raise RuntimeError("Server is updating, cannot get current version")
        with open(updating_to_file, "r") as f:
            version = f.read().strip()
        _log.warning(f"Found .updating_to file, returning version: {version}")
        return version

    version_file = os.path.join(active_dir, ".version")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            version = f.read().strip()
        return version

    return None


def get_path_to_minecraft_server_exe(fail_on_updating: bool = True) -> str:
    """
    Get the path to the minecraft server exe in the active directory. This is most commonly run when we want to start
     the server, and so we normally want to fail if the server crashed while updating.

    :param fail_on_updating: If True, we will raise an error if the server is updating. If False, we will return the
    path to the exe even if the server is updating.
    :type fail_on_updating: bool

    :return: The path to the minecraft server exe
    :rtype: str

    """
    try:
        version = get_current_version(
            fail_on_updating=fail_on_updating
        )
    except RuntimeError as e:
        raise RuntimeError("Server is updating, cannot get path to minecraft server exe") from e

    # try to find folder the same as the version
    return os.path.join(get_path_to_active_dir(), version, "bedrock_server.exe")
