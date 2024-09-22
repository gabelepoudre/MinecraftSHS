import requests
import re
import os
from mc import paths
import logging
import zipfile

_log = logging.getLogger(__name__)

# looking for, want to get the link
"""
</label>
<a href="https://minecraft.azureedge.net/bin-win/bedrock-server-1.21.30.03.zip" 
aria-label="Download Minecraft Dedicated Server software for Windows" class="btn btn-disabled-outline mt-4 downloadlink" role="button" data-platform="serverBedrockWindows" tabindex="0" aria-disabled="true">Download </a>
</div>
"""

# need to specifically get the windows installer
_compiled = re.compile(r'href="https://minecraft.azureedge.net/bin-win/bedrock-server-([0-9.]+).zip"')


def get_version_from_download_link(download_link: str):
    # e.g. https://minecraft.azureedge.net/bin-win/bedrock-server-1.21.30.03.zip

    # get the version from the link
    spl = download_link.split("bedrock-server-")
    if len(spl) != 2:
        _log.error(f"Could not get version from download link: {download_link}")
        return None
    try:
        version = spl[1].split(".zip")[0]
    except IndexError:
        _log.error(f"Could not get version from download link: {download_link}")
        return None

    return version


def get_latest_download_link():
    # get with short timeout, it seems like it goes down frequently (or is heavily rate limited)
    try:
        r = requests.get(
            "https://www.minecraft.net/en-us/download/server/bedrock",
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.google.com"
            }
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        _log.error("Could not get download link, connection error...")
        return None
    if r.status_code != 200:
        _log.error(f"Could not get download link, status code: {r.status_code}")
        return None
    matches = _compiled.findall(r.text)
    if not matches:
        _log.error("Could not get download link, no matches in HTML")
        return None
    version = matches[0]
    return f"https://minecraft.azureedge.net/bin-win/bedrock-server-{version}.zip"


def download_and_extract(download_link: str) -> bool:
    """
    We want to download the file to the versions directory, and then extract it to the versions directory, before
    deleting the zip file.

    We want to extract it to a directory with the same name as the version.

    :param download_link:
    :return:
    """

    try:
        version = get_version_from_download_link(download_link)
        if version is None:
            _log.error(f"Couldn't get version from download link, so not downloading: {download_link}")
            return False

        # download
        r = requests.get(download_link)
        if r.status_code != 200:
            _log.error(f"Could not download file, status code: {r.status_code}")
            return False

        # save to versions directory
        download_path = paths.get_path_to_versions_dir()
        download_path = os.path.join(download_path, f"bedrock-server-{version}.zip")
        with open(download_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024**2):
                f.write(chunk)
        _log.info(f"Downloaded to: {download_path}")

        extract_dir = os.path.join(paths.get_path_to_versions_dir(), version)
        if os.path.exists(extract_dir):
            _log.error(f"Extract directory already exists, not extracting: {extract_dir}")
            return False
        extract_dir_confirmed = extract_dir  # don't want to delete the wrong directory
        os.mkdir(extract_dir)

        # extract
        with zipfile.ZipFile(download_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # delete zip
        os.remove(download_path)

        return True

    except Exception as e:
        _log.error(f"Unexpected exception downloading and extracting", exc_info=e)

        try:  # try to delete the zip file if it exists
            os.remove(download_path)  # noqa  # we don't care if this fails
        except Exception:  # noqa  # doesn't matter, quick cleanup
            pass

        try:  # try to delete the extracted directory if it exists
            os.rmdir(extract_dir_confirmed)  # noqa  # we don't care if this fails
        except Exception:  # noqa  # doesn't matter, quick cleanup
            pass

        return False


if __name__ == '__main__':
    download_and_extract(get_latest_download_link())
