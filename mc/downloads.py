import requests
import re
import os
from mc import paths
import logging
import zipfile

_log = logging.getLogger(__name__)

# looking for, want to get the link
pattern = re.compile(r"bedrock-server-\d+\.\d+\.\d+\.\d+\.zip")

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
    """
    Get the latest download link for the Bedrock server from Minecraft's website.
    This will try to get the latest link, and if it fails, it will try to get the old link.
    :return: The download link or None if it fails.
    """
    # first try the new API
    download_link = get_latest_download_link_new()
    if download_link is not None:
        return download_link

    # if that fails, try the old HTML method
    _log.warning("Could not get new download link, trying old method...")
    return get_latest_download_link_old()


def get_latest_download_link_new(api_version: str = "v1.0"):
    # post June 2025 links have new links GET dynamically
    # get with short timeout, it seems like it goes down frequently (or is heavily rate limited)
    try:
        r = requests.get(
            f"https://net-secondary.web.minecraft-services.net/api/{api_version}/download/links",
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.minecraft.net/"
            }
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        _log.error("Could not get download link, connection error...")
        return None
    if r.status_code != 200:
        _log.error(f"Could not get download link, status code: {r.status_code}")
        return None

    resp_json = r.json()

    try:
        result = resp_json["result"]
        links = result["links"]
    except KeyError:
        _log.error("Could not get download link, response JSON does not contain expected keys", exc_info=True)
        return None

    correct_link = None
    for link in links:  # obj
        try:
            if link['downloadType'] == "serverBedrockWindows":
                correct_link = link["downloadUrl"]
        except KeyError as e:
            _log.warning(f"Could not get download link, response JSON does not contain expected keys:"
                         f" {e}", exc_info=True)
            continue

    if correct_link is None:
        _log.error("Could not get download link, no link found for serverBedrockWindows")
        return None

    return correct_link


def get_latest_download_link_old():
    # pre June 2025 links were burned into html
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
    # we want to match on any new download link, and so we want to find the indices where we see a version.zip
    # e.g. 1.21.30.03.zip, and then use that to determine which is the windows download link
    matches = pattern.findall(r.text)

    # now, to get the full links, we need to get the https:// part of the link

    full_links = []

    for match in matches:
        # walking backwards, find https://
        start = r.text.rfind("https://", 0, r.text.find(match))
        if start == -1:
            _log.error("Could not get download link, no https:// found")
            continue
        full_link = r.text[start:r.text.find(match) + len(match)]
        full_links.append(full_link)

    # now that we have links, throw out bad ones
    illegal_chars = ">< \n"  # would appear if our search was bad
    to_remove = []
    for link in full_links:
        if any(char in link for char in illegal_chars):
            to_remove.append(link)
    for link in to_remove:
        full_links.remove(link)

    # now throw out any that don't contain the string "win"
    to_remove = []
    for link in full_links:
        if "win" not in link:
            to_remove.append(link)
    for link in to_remove:
        full_links.remove(link)

    # now throw out any that contain "preview"
    to_remove = []
    for link in full_links:
        if "preview" in link:
            to_remove.append(link)
    for link in to_remove:
        full_links.remove(link)

    # now dedupe via set
    full_links = list(set(full_links))

    # we should hopefully have one link left
    if len(full_links) > 1:
        _log.error("Could not get download link, too many matches in HTML")
        return None
    elif len(full_links) == 0:
        _log.error("Could not get download link, no matches in HTML")
        return None
    else:
        return full_links[0]


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
        _log.info(f"Sending download request")
        r = requests.get(
            download_link,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.minecraft.net/en-us/download/server/bedrock"
            }
        )
        if r.status_code != 200:
            _log.error(f"Could not download file, status code: {r.status_code}")
            return False

        # save to versions directory
        download_path = paths.get_path_to_versions_dir()
        download_path = os.path.join(download_path, f"bedrock-server-{version}.zip")
        _log.info(f"Downloading to: {download_path}")
        with open(download_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 ** 2):
                f.write(chunk)
        _log.info(f"Downloaded to: {download_path}")

        extract_dir = os.path.join(paths.get_path_to_versions_dir(), version + "_inprogress")
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

        # rename directory
        os.rename(extract_dir, extract_dir.replace("_inprogress", ""))

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
