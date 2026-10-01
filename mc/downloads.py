import requests
import re
import os
import shutil
from mc import paths
import logging
import zipfile

_log = logging.getLogger(__name__)

API_VERSION = "v1.0"
API_URL_TEMPLATE = "https://net-secondary.web.minecraft-services.net/api/{api_version}/download/links"
WINDOWS_DOWNLOAD_TYPE = "serverBedrockWindows"
SERVER_EXE_NAME = "bedrock_server.exe"

# looking for, want to get the link
pattern = re.compile(r"bedrock-server-\d+\.\d+\.\d+\.\d+\.zip")
# same, but capturing the version
_version_pattern = re.compile(r"bedrock-server-(\d+\.\d+\.\d+\.\d+)\.zip")
# a full, acceptable download url
_download_url_pattern = re.compile(r"https://\S+/bedrock-server-\d+\.\d+\.\d+\.\d+\.zip")


def get_version_from_download_link(download_link: str) -> str | None:
    # e.g. https://www.minecraft.net/bedrockdedicatedserver/bin-win/bedrock-server-1.21.30.03.zip
    if not isinstance(download_link, str):
        _log.error(f"Could not get version from download link: {download_link!r}")
        return None

    m = _version_pattern.search(download_link)
    if m is None:
        _log.error(f"Could not get version from download link: {download_link}")
        return None

    return m.group(1)


def _validate_download_url(url) -> str | None:
    """Return the url if it is an https bedrock-server-<d.d.d.d>.zip link, otherwise log and return None."""
    if not isinstance(url, str) or not _download_url_pattern.fullmatch(url):
        _log.error(f"Download link is not a valid https bedrock-server-<version>.zip link: {url!r}")
        return None
    return url


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
    _log.warning("Could not get new download link, trying old (legacy) method...")
    return get_latest_download_link_old()


def get_latest_download_link_new(api_version: str = API_VERSION):
    # post June 2025 links have new links GET dynamically
    # get with short timeout, it seems like it goes down frequently (or is heavily rate limited)
    try:
        r = requests.get(
            API_URL_TEMPLATE.format(api_version=api_version),
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.minecraft.net/"
            }
        )
    except requests.exceptions.RequestException as e:
        _log.error(f"Could not get download link, request failed: {e}")
        return None
    if r.status_code == 404:
        _log.error(f"Could not get download link, status code 404: API version {api_version} may have been retired")
        return None
    if r.status_code != 200:
        _log.error(f"Could not get download link, status code: {r.status_code}")
        return None

    try:
        resp_json = r.json()
    except ValueError:
        _log.error("Could not get download link, response was not valid JSON")
        return None

    try:
        links = resp_json["result"]["links"]
    except (KeyError, TypeError):
        _log.error("Could not get download link, response JSON does not contain expected keys")
        return None
    if not isinstance(links, list):
        _log.error("Could not get download link, response JSON 'links' is not a list")
        return None

    correct_link = None
    found_types = []
    for link in links:  # obj
        if not isinstance(link, dict):
            _log.warning(f"Skipping unexpected entry in links: {link!r}")
            continue
        try:
            download_type = link['downloadType']
            found_types.append(download_type)
            if download_type == WINDOWS_DOWNLOAD_TYPE:
                correct_link = link["downloadUrl"]
        except KeyError as e:
            _log.warning(f"Could not get download link, link entry does not contain expected keys: {e}")
            continue

    if correct_link is None:
        _log.error(f"Could not get download link, no link found for {WINDOWS_DOWNLOAD_TYPE}, "
                   f"downloadTypes present: {found_types}")
        return None

    return _validate_download_url(correct_link)


def get_latest_download_link_old():
    # LEGACY, likely obsolete: pre June 2025 links were burned into html. Only a last-resort fallback, must never raise.
    _log.info("Using legacy HTML download link lookup (likely obsolete)")
    try:
        return _get_latest_download_link_old()
    except Exception as e:  # noqa  # last resort, never raise
        _log.error(f"Legacy download link lookup failed: {e}")
        return None


def _get_latest_download_link_old():
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
    except requests.exceptions.RequestException as e:
        _log.error(f"Could not get download link, request failed: {e}")
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
        return _validate_download_url(full_links[0])


class _DownloadError(Exception):
    """A download or validation failure we expect and describe ourselves (no traceback needed)."""


def _remove_file(path: str | None):
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_dir(path: str | None):
    if path is None:
        return
    shutil.rmtree(path, ignore_errors=True)


def _validate_zip(zip_path: str):
    if not zipfile.is_zipfile(zip_path):
        raise _DownloadError("Downloaded file is not a zip file")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        bad = zip_ref.testzip()
        if bad is not None:
            raise _DownloadError(f"Downloaded zip is corrupt, first bad file: {bad}")
        if SERVER_EXE_NAME not in zip_ref.namelist():
            raise _DownloadError(f"Downloaded zip does not contain {SERVER_EXE_NAME}")


def download_and_extract(download_link: str) -> bool:
    """
    We want to download the file to the versions directory, and then extract it to the versions directory, before
    deleting the zip file.

    We want to extract it to a directory with the same name as the version.

    The download is streamed to a .partial file, validated (zip integrity, expected size, contains the server exe),
    extracted into a <version>_inprogress directory, and only renamed into place once that holds the server exe.
    Any failure removes the temporary files and returns False.

    :param download_link:
    :return:
    """

    partial_path = None
    download_path = None
    extract_dir_confirmed = None  # don't want to delete the wrong directory

    try:
        version = get_version_from_download_link(download_link)
        if version is None:
            _log.error(f"Couldn't get version from download link, so not downloading: {download_link}")
            return False

        versions_dir = paths.get_path_to_versions_dir()
        final_dir = os.path.join(versions_dir, version)
        if os.path.exists(final_dir):
            _log.error(f"Version directory already exists, not downloading: {final_dir}")
            return False

        extract_dir = os.path.join(versions_dir, version + "_inprogress")
        if os.path.exists(extract_dir):
            _log.warning(f"Removing stale in-progress directory from a previous attempt: {extract_dir}")
            shutil.rmtree(extract_dir)

        zip_path = os.path.join(versions_dir, f"bedrock-server-{version}.zip")
        partial_path = zip_path + ".partial"

        # download
        _log.info(f"Sending download request")
        with requests.get(
            download_link,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.minecraft.net/en-us/download/server/bedrock"
            },
            stream=True,
            timeout=(10, 60),
        ) as r:
            if r.status_code != 200:
                raise _DownloadError(f"Could not download file, status code: {r.status_code}")

            expected_size = None
            content_length = r.headers.get("Content-Length")
            if content_length is not None:
                try:
                    expected_size = int(content_length)
                except ValueError:
                    _log.warning(f"Ignoring invalid Content-Length: {content_length!r}")

            _log.info(f"Downloading to: {partial_path}")
            written = 0
            with open(partial_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 ** 2):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)

        if expected_size is not None and written != expected_size:
            raise _DownloadError(f"Downloaded size {written} does not match Content-Length {expected_size}")

        os.replace(partial_path, zip_path)
        download_path = zip_path
        partial_path = None
        _log.info(f"Downloaded to: {download_path}")

        # validate before extracting
        _validate_zip(download_path)

        # extract
        extract_dir_confirmed = extract_dir
        os.mkdir(extract_dir)
        with zipfile.ZipFile(download_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        if not os.path.isfile(os.path.join(extract_dir, SERVER_EXE_NAME)):
            raise _DownloadError(f"{SERVER_EXE_NAME} missing after extraction")

        # delete zip
        os.remove(download_path)
        download_path = None

        # rename directory
        os.rename(extract_dir, final_dir)
        extract_dir_confirmed = None

        return True

    except _DownloadError as e:
        _log.error(str(e))
    except Exception as e:
        _log.error(f"Unexpected exception downloading and extracting", exc_info=e)

    # failure cleanup, best effort
    _remove_file(partial_path)
    _remove_file(download_path)
    _remove_dir(extract_dir_confirmed)
    return False


if __name__ == '__main__':
    download_and_extract(get_latest_download_link())
