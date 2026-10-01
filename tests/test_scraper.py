import io
import os
import zipfile

import pytest
import requests

from mc import downloads, paths, update, versions

GOOD_URL = "https://www.minecraft.net/bedrockdedicatedserver/bin-win/bedrock-server-1.26.52.3.zip"

SAMPLE = {"result": {"links": [
    {"downloadType": "serverBedrockWindows", "downloadUrl": GOOD_URL},
    {"downloadType": "serverBedrockLinux",
     "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.26.52.3.zip"},
    {"downloadType": "serverBedrockPreviewWindows",
     "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-win-preview/bedrock-server-1.26.60.1.zip"},
    {"downloadType": "serverBedrockPreviewLinux",
     "downloadUrl": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux-preview/bedrock-server-1.26.60.1.zip"},
    {"downloadType": "serverJar", "downloadUrl": "https://piston-data.mojang.com/server.jar"},
]}}


# ---------------------------------------------------------------- versions

def test_parse_version():
    assert versions.parse_version("1.26.52.3") == (1, 26, 52, 3)
    assert versions.parse_version("1.21.30.03") == (1, 21, 30, 3)
    for bad in ["", "junk", "1.26.52.3_inprogress", "1..2", ".1", "1.2.", "v1.2", "1.2.x", None, 5]:
        assert versions.parse_version(bad) is None


def test_numeric_ordering_beats_string_ordering():
    assert "1.26.9.1" > "1.26.52.3"  # the string bug
    assert versions.parse_version("1.26.9.1") < versions.parse_version("1.26.52.3")
    assert versions.is_newer("1.26.52.3", "1.26.9.1")
    assert not versions.is_newer("1.26.9.1", "1.26.52.3")
    assert not versions.is_newer("1.26.52.3", "1.26.52.3")


def test_is_newer_edge_cases():
    assert versions.is_newer("1.0.0.1", None)
    assert not versions.is_newer(None, "1.0.0.1")
    assert not versions.is_newer("junk", "1.0.0.1")
    assert versions.is_newer("1.0.0.1", "junk")


@pytest.fixture
def versions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_path_to_versions_dir", str(tmp_path))
    monkeypatch.setenv("MC_KEEP_DOWNLOADED_VERSIONS", "2")
    return tmp_path


def test_most_recent_is_numeric_and_prunes_oldest(versions_dir):
    for name in ["1.26.9.1", "1.26.52.3", "1.26.10.0", "1.25.1.1"]:
        (versions_dir / name).mkdir()
    assert update._get_most_recent_downloaded_version() == "1.26.52.3"
    assert sorted(os.listdir(versions_dir)) == ["1.26.10.0", "1.26.52.3"]


def test_unparseable_dirs_ignored_and_never_deleted(versions_dir):
    for name in ["1.26.9.1", "1.26.99.9_inprogress", "junk", "zzz", "1.26.8.1", "1.26.7.1"]:
        (versions_dir / name).mkdir()
    (versions_dir / "9.9.9.9").write_text("a file, not a dir")
    assert update._get_most_recent_downloaded_version() == "1.26.9.1"
    left = set(os.listdir(versions_dir))
    assert {"1.26.99.9_inprogress", "junk", "zzz", "9.9.9.9"} <= left
    assert "1.26.7.1" not in left and "1.26.8.1" in left


def test_most_recent_none_when_only_junk(versions_dir):
    (versions_dir / "1.2.3.4_inprogress").mkdir()
    assert update._get_most_recent_downloaded_version() is None


# ---------------------------------------------------------------- link lookup

class FakeResp:
    def __init__(self, status=200, payload=None, text="", bad_json=False, body=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text
        self._bad_json = bad_json
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._bad_json:
            raise ValueError("bad json")
        return self._payload

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), 7):
            yield self._body[i:i + 7]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def patch_get(monkeypatch, resp=None, exc=None):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(downloads.requests, "get", fake_get)
    return calls


def test_link_parsing_sample(monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload=SAMPLE))
    assert downloads.get_latest_download_link_new() == GOOD_URL
    assert "/api/v1.0/download/links" in calls[0][0]
    assert downloads.get_version_from_download_link(GOOD_URL) == "1.26.52.3"


def test_version_from_link_bad_input():
    assert downloads.get_version_from_download_link("https://x/y.zip") is None
    assert downloads.get_version_from_download_link(None) is None
    assert downloads.get_version_from_download_link("https://a/bedrock-server-1.21.30.03.zip") == "1.21.30.03"


@pytest.mark.parametrize("resp", [
    FakeResp(bad_json=True),
    FakeResp(payload=[1, 2]),
    FakeResp(payload=None),
    FakeResp(payload={"result": {}}),
    FakeResp(payload={"result": {"links": "nope"}}),
    FakeResp(payload={"result": {"links": [{"downloadType": "serverJar", "downloadUrl": "https://a/b.jar"}]}}),
    FakeResp(payload={"result": {"links": ["x", 3, {"downloadUrl": GOOD_URL}]}}),
    FakeResp(payload={"result": {"links": [{"downloadType": "serverBedrockWindows"}]}}),
    FakeResp(status=404),
    FakeResp(status=500),
])
def test_link_new_bad_responses_return_none(monkeypatch, resp):
    patch_get(monkeypatch, resp)
    assert downloads.get_latest_download_link_new() is None


def test_no_windows_entry_logs_present_types(monkeypatch, caplog):
    payload = {"result": {"links": [{"downloadType": "serverBedrockWinX", "downloadUrl": GOOD_URL},
                                    {"downloadType": "serverJar", "downloadUrl": "https://a/b.jar"}]}}
    patch_get(monkeypatch, FakeResp(payload=payload))
    with caplog.at_level("ERROR"):
        assert downloads.get_latest_download_link_new() is None
    assert "serverBedrockWinX" in caplog.text and "serverJar" in caplog.text


def test_404_logs_clearly(monkeypatch, caplog):
    patch_get(monkeypatch, FakeResp(status=404))
    with caplog.at_level("ERROR"):
        downloads.get_latest_download_link_new()
    assert "404" in caplog.text


@pytest.mark.parametrize("url", [
    "http://www.minecraft.net/bin-win/bedrock-server-1.26.52.3.zip",
    "https://www.minecraft.net/bin-win/evil.zip",
    "https://www.minecraft.net/bin-win/bedrock-server-1.26.52.zip",
    "https://www.minecraft.net/bin-win/bedrock-server-1.26.52.3.zip?x=1",
    "ftp://a/bedrock-server-1.26.52.3.zip",
    "",
    None,
    123,
])
def test_bad_urls_rejected(monkeypatch, url):
    payload = {"result": {"links": [{"downloadType": "serverBedrockWindows", "downloadUrl": url}]}}
    patch_get(monkeypatch, FakeResp(payload=payload))
    assert downloads.get_latest_download_link_new() is None


def test_request_exceptions_return_none(monkeypatch):
    patch_get(monkeypatch, exc=requests.exceptions.RequestException("boom"))
    assert downloads.get_latest_download_link_new() is None
    assert downloads.get_latest_download_link_old() is None


def test_old_never_raises(monkeypatch):
    patch_get(monkeypatch, exc=RuntimeError("anything"))
    assert downloads.get_latest_download_link_old() is None
    patch_get(monkeypatch, FakeResp(text=None))  # r.text.find on None would raise
    assert downloads.get_latest_download_link_old() is None


def test_old_html_still_works(monkeypatch):
    html = f'<a href="{GOOD_URL.replace("www.minecraft.net", "x.net")}">win</a>'
    patch_get(monkeypatch, FakeResp(text=html))
    assert downloads.get_latest_download_link_old().endswith("bedrock-server-1.26.52.3.zip")


def test_fallback_to_old_when_new_fails(monkeypatch):
    monkeypatch.setattr(downloads, "get_latest_download_link_new", lambda: None)
    monkeypatch.setattr(downloads, "get_latest_download_link_old", lambda: GOOD_URL)
    assert downloads.get_latest_download_link() == GOOD_URL


# ---------------------------------------------------------------- download validation

def make_zip(files=None) -> bytes:
    files = {"bedrock_server.exe": b"MZ", "server.properties": b"a=b"} if files is None else files
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_download_success(versions_dir, monkeypatch):
    body = make_zip()
    calls = patch_get(monkeypatch, FakeResp(body=body, headers={"Content-Length": str(len(body))}))
    assert downloads.download_and_extract(GOOD_URL) is True
    assert calls[0][1]["stream"] is True and calls[0][1]["timeout"] == (10, 60)
    assert os.listdir(versions_dir) == ["1.26.52.3"]
    assert (versions_dir / "1.26.52.3" / "bedrock_server.exe").is_file()


def test_download_removes_stale_inprogress(versions_dir, monkeypatch):
    stale = versions_dir / "1.26.52.3_inprogress"
    (stale / "sub").mkdir(parents=True)
    (stale / "sub" / "junk.txt").write_text("x")
    patch_get(monkeypatch, FakeResp(body=make_zip()))
    assert downloads.download_and_extract(GOOD_URL) is True
    assert os.listdir(versions_dir) == ["1.26.52.3"]
    assert not (versions_dir / "1.26.52.3" / "sub").exists()


@pytest.mark.parametrize("resp", [
    FakeResp(body=b"this is not a zip"),
    FakeResp(body=make_zip({"readme.txt": b"hi"})),  # no server exe
    FakeResp(body=make_zip(), headers={"Content-Length": "999999"}),  # size mismatch
    FakeResp(body=make_zip()[:-10]),  # truncated
    FakeResp(status=503),
])
def test_download_failures_leave_nothing(versions_dir, monkeypatch, resp):
    patch_get(monkeypatch, resp)
    assert downloads.download_and_extract(GOOD_URL) is False
    assert os.listdir(versions_dir) == []


def test_corrupt_member_detected(versions_dir, monkeypatch):
    body = bytearray(make_zip({"bedrock_server.exe": b"A" * 5000}))
    idx = body.index(b"A" * 100) + 50
    body[idx] = ord("B")  # flip a data byte, CRC no longer matches
    patch_get(monkeypatch, FakeResp(body=bytes(body)))
    assert downloads.download_and_extract(GOOD_URL) is False
    assert os.listdir(versions_dir) == []


def test_download_network_error_before_paths_set(versions_dir, monkeypatch):
    patch_get(monkeypatch, exc=requests.exceptions.ConnectionError("x"))
    assert downloads.download_and_extract(GOOD_URL) is False
    assert downloads.download_and_extract("https://bad/link.zip") is False
    assert os.listdir(versions_dir) == []


# ---------------------------------------------------------------- never downgrade

@pytest.fixture
def upd(versions_dir, tmp_path_factory, monkeypatch):
    active = tmp_path_factory.mktemp("active")
    monkeypatch.setattr(paths, "_path_to_active_dir", str(active))
    monkeypatch.setenv("MC_KEEP_DOWNLOADED_VERSIONS", "5")
    downloaded = []
    monkeypatch.setattr(update.downloads, "download_and_extract", lambda link: downloaded.append(link) or True)
    return versions_dir, active, downloaded


def link_for(v):
    return f"https://www.minecraft.net/bin-win/bedrock-server-{v}.zip"


def test_download_if_required_numeric_and_no_downgrade(upd, monkeypatch):
    vdir, _, downloaded = upd
    (vdir / "1.26.52.3").mkdir()

    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: link_for("1.26.9.1"))
    assert update.download_version_if_required() is None  # older: nothing
    assert downloaded == []

    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: link_for("1.26.52.3"))
    assert update.download_version_if_required() == "1.26.52.3"
    assert downloaded == []

    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: link_for("1.26.100.1"))
    assert update.download_version_if_required() == "1.26.100.1"
    assert downloaded == [link_for("1.26.100.1")]


def test_first_ever_download(upd, monkeypatch):
    _, _, downloaded = upd
    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: link_for("1.26.9.1"))
    assert update.download_version_if_required() == "1.26.9.1"
    assert downloaded == [link_for("1.26.9.1")]


def test_no_downgrade_when_no_downloads_but_newer_current(upd, monkeypatch):
    _, active, downloaded = upd
    (active / ".version").write_text("1.26.52.3")
    monkeypatch.setattr(update.downloads, "get_latest_download_link", lambda: link_for("1.26.9.1"))
    assert update.download_version_if_required() is None
    assert downloaded == []


def test_need_update(upd):
    vdir, active, _ = upd
    assert update.need_update() is True  # no current version
    (vdir / "1.26.9.1").mkdir()
    (vdir / "1.26.52.3").mkdir()
    (active / ".version").write_text("1.26.9.1")
    assert update.need_update() is True
    (active / ".version").write_text("1.26.52.3")
    assert update.need_update() is False
    (active / ".version").write_text("1.27.0.1")  # current newer than anything downloaded
    assert update.need_update() is False


def test_try_update_refuses_downgrade(upd):
    vdir, active, _ = upd
    (vdir / "1.26.9.1").mkdir()
    (active / ".version").write_text("1.26.52.3")
    assert update.try_update() is False
    assert not (active / ".updating_to").exists()
    assert (active / ".version").read_text() == "1.26.52.3"
