import random

from mc import downloads
from mc import paths
import os
import shutil
import logging
import time
import zipfile

_log = logging.getLogger(__name__)


def _get_most_recent_downloaded_version():
    # grab all of the folder names in the versions directory
    versions_dir = paths.get_path_to_versions_dir()
    versions = os.listdir(versions_dir)
    versions = [v for v in versions if os.path.isdir(os.path.join(versions_dir, v))]
    if not versions:
        return None

    versions.sort(reverse=True)

    if len(versions) > 5:
        # if we have more than 5 versions, delete the oldest
        for version in versions[5:]:
            shutil.rmtree(os.path.join(versions_dir, version))

    return versions[0]


def need_update() -> bool:
    # if we don't have a version, we need an update
    # if we have a version and it is not our most recent downloaded version, we need an update
    our_version = paths.get_current_version()
    most_recent_downloaded_version = _get_most_recent_downloaded_version()

    if our_version is None:
        return True
    elif our_version != most_recent_downloaded_version:
        return True
    else:
        return False


def download_version_if_required() -> str | None:
    while True:
        download_link = downloads.get_latest_download_link()
        if download_link is not None:
            break
        else:
            _log.error("Failed to retrieve most recent version, trying again in 60 seconds...")

        time.sleep(60)

    # we have a download link, lets get the version
    version = downloads.get_version_from_download_link(download_link)

    # lets get our most recent downloaded version
    most_recent_downloaded_version = _get_most_recent_downloaded_version()

    if most_recent_downloaded_version is None:
        _log.info("No versions downloaded yet, downloading...")
        downloads.download_and_extract(download_link)
        return version
    elif version != most_recent_downloaded_version:
        _log.info(f"New version available: {version}, downloading...")

        # quick, lets make sure this version doesn't already exist (i.e. our most-recent check gave us a bad result)
        version_path = os.path.join(paths.get_path_to_versions_dir(), version)
        if os.path.exists(version_path):
            _log.error(f"Version already exists, not downloading: {version}")
            return None

        downloads.download_and_extract(download_link)
        return version
    else:
        _log.info(f"Latest version already downloaded: {version}")
        return version


def get_most_recent_update_thread():
    """
    Intended to be run in a daemonic thread

    Note to self, run this after one manual download_version_if_required() before an attempt to update on first start

    """
    while True:
        download_version_if_required()

        # check again in 5-20 minutes (to avoid spamming the server, and maybe make it look more human)
        minutes_to_sleep = 60 * 5 * ((random.random() * 2) + 1)
        _log.debug(f"Sleeping for {minutes_to_sleep:.2f} seconds...")
        time.sleep(minutes_to_sleep)


def try_update() -> bool:
    """
    This function assumes that the server is not running, and that we are in a safe state to update the server.

    """
    try:
        our_version = paths.get_current_version(fail_on_updating=True)
    except RuntimeError as e:
        raise RuntimeError("Server is updating, cannot update...") from e

    if our_version is None:
        _log.info("No version found, which probably means this is first start...")

    most_recent_downloaded_version = _get_most_recent_downloaded_version()
    if most_recent_downloaded_version is None:  # we should have downloaded a version by now
        _log.error("No versions downloaded, cannot update...")
        if our_version is None:
            raise RuntimeError("No versions downloaded, and out version is None, which means we are in a bad state...")
        return False

    if our_version == most_recent_downloaded_version:
        _log.info(f"Server is up to date: {our_version}")
        return False

    # we are updating! first thing first, we need to create the .updating_to file
    active_dir = paths.get_path_to_active_dir()
    updating_to_file = os.path.join(active_dir, ".updating_to")
    with open(updating_to_file, "w") as f:
        f.write(most_recent_downloaded_version)
        _log.info(f"Updating to version: {most_recent_downloaded_version}")

    # step one, copy the most recent downloaded version to the active directory
    src_path = os.path.join(paths.get_path_to_versions_dir(), most_recent_downloaded_version)
    dst_path = os.path.join(active_dir, most_recent_downloaded_version)
    path_to_current = os.path.join(active_dir, "current")

    if our_version:
        if not os.path.exists(path_to_current):
            raise RuntimeError(f"Current version does not exist: {path_to_current}")

    if os.path.exists(dst_path):
        raise RuntimeError(f"Destination path already exists, cannot update: {dst_path}")

    if not os.path.exists(src_path):
        raise RuntimeError(f"Source path does not exist, cannot update: {src_path}")

    _log.info(f"Copying {src_path} to {dst_path}")

    shutil.copytree(src_path, dst_path)

    # step two, make one full backup of the current version ( if we have one )
    if our_version:
        backup_dir = paths.get_path_to_backup_dir()
        update_backup_dir = os.path.join(backup_dir, f"updates")
        this_update_backup_file = os.path.join(
            update_backup_dir,
            f"{our_version}_to_{most_recent_downloaded_version}.zip"
        )
        os.makedirs(update_backup_dir, exist_ok=True)

        _log.info(f"Backing up current version to: {this_update_backup_file}")
        # back up with high compression
        with zipfile.ZipFile(
                this_update_backup_file, 'w', zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zip_ref:
            for root, dirs, files in os.walk(path_to_current):  # noqa  # defined above if we have a current version
                for file in files:
                    src = os.path.join(root, file)
                    dst = os.path.relpath(src, path_to_current)
                    zip_ref.write(src, dst)

    # step three, copy the necessary files from the current version to the new version (blowing away any existing files)
    if our_version:
        files_to_copy = [
            "allowlist.json",
            "permissions.json",
            "server.properties",
        ]

        dirs_to_copy = [
            "worlds",
        ]

        for file in files_to_copy:
            src = os.path.join(path_to_current, file)
            dst = os.path.join(dst_path, file)
            if not os.path.exists(src):
                _log.debug(f"File does not exist, skipping: {src}")
                continue
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copy(src, dst)

        for dir_ in dirs_to_copy:
            src = os.path.join(path_to_current, dir_)
            dst = os.path.join(dst_path, dir_)
            if not os.path.exists(src):
                _log.debug(f"Directory does not exist, skipping: {src}")
                continue
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        _log.info(f"Copied necessary files from current version to new version")

    # step four, delete the previous version
    if our_version:
        shutil.rmtree(path_to_current)

    # step five, rename the new version to current
    os.rename(dst_path, path_to_current)

    # step five, write the .version file
    version_file = os.path.join(active_dir, ".version")
    with open(version_file, "w") as f:
        f.write(most_recent_downloaded_version)
        _log.info(f"Updated to version: {most_recent_downloaded_version}")

    # step six, delete the .updating_to file
    os.remove(updating_to_file)

    return True


if __name__ == '__main__':
    try_update()
