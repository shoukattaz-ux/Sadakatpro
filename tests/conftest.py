"""Shared fixtures: isolated data folder, services, a local test website."""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("FLOWPILOT_NO_KEYRING", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SITE = Path(__file__).parent / "site"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FLOWPILOT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture()
async def services(data_dir):
    from flowpilot.services.container import Services

    svc = Services.create(data_dir)
    yield svc
    # stop the Playwright driver and local browsers inside the test's event loop
    await svc.engine.shutdown()
    await svc.adapters.aclose()
    svc.db.dispose()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass


@pytest.fixture(scope="session")
def site_url():
    handler = functools.partial(_QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def chromium_available() -> bool:
    from flowpilot.adapters.local import find_browser

    return bool(find_browser())


needs_browser = pytest.mark.skipif(not chromium_available(), reason="no Chrome/Chromium available")
