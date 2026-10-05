import os
import sys
import types

import pytest

# the code only uses dotenv.load_dotenv at import time; stub it if python-dotenv is not installed in the test env
try:
    import dotenv  # noqa
except ImportError:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: False)

# run_mc_server.py lives in the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def events_dir(tmp_path, monkeypatch):
    """Every test writes events to its own temp dir, never to the real data/events."""
    from mc import paths
    path = tmp_path / "events"
    monkeypatch.setattr(paths, "_path_to_events_dir", str(path))
    return path
