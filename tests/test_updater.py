"""Updater against a mocked GitHub API."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from flowpilot.services.updater import UpdateError, check_for_update, download_update, is_newer, parse_version

PAYLOAD = b"MZ fake installer bytes" * 1000
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def release(tag="v9.1.0", with_sha=True, **extra):
    assets = [{"name": "FlowPilot-Setup.exe", "size": len(PAYLOAD),
               "browser_download_url": "https://github.com/x/releases/download/v9.1.0/FlowPilot-Setup.exe"},
              {"name": "FlowPilot-portable.zip", "browser_download_url": "https://x/zip"}]
    if with_sha:
        assets.append({"name": "FlowPilot-Setup.exe.sha256",
                       "browser_download_url": "https://github.com/x/releases/download/v9.1.0/FlowPilot-Setup.exe.sha256"})
    return {"tag_name": tag, "body": "## What's new\n- Faster", "published_at": "2026-10-12T10:00:00Z",
            "html_url": "https://github.com/x/releases/tag/v9.1.0", "assets": assets, **extra}


def transport(rel: dict | None, status: int = 200, bad_hash: bool = False, web: bool = False):
    """``web=False``: the web address fails, so the API (``rel`` / ``status``) is used."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["User-Agent"].startswith("FlowPilot/")
        if req.url.host == "github.com" and req.url.path.endswith("/releases/latest"):
            if not web:
                return httpx.Response(500)
            if rel is None:
                return httpx.Response(302, headers={"location": "https://github.com/x/y/releases"})
            return httpx.Response(302, headers={"location": f"https://github.com/x/y/releases/tag/{rel['tag_name']}"})
        if req.url.path.endswith("/releases/latest"):
            return httpx.Response(status, json=rel or {})
        if req.url.path.endswith("/release-notes.md"):
            return httpx.Response(200, text="## What's new\n- Faster")
        if req.url.path.endswith(".sha256"):
            return httpx.Response(200, text=("0" * 64 if bad_hash else SHA) + "  FlowPilot-Setup.exe\n")
        if req.url.path.endswith(".exe"):
            return httpx.Response(200, content=PAYLOAD)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_versions():
    assert parse_version("v1.2.10") == (1, 2, 10)
    assert is_newer("v0.10.0", "0.9.9") and is_newer("1.0", "0.9") and not is_newer("v0.2.0", "0.2.0")
    assert is_newer("0.2.1", "0.2") and not is_newer("0.2.0-beta", "0.2.0")


async def test_check_newer_and_current():
    info = await check_for_update("0.2.0", transport=transport(release()))
    assert info is not None and info.version == "9.1.0" and info.installer_name == "FlowPilot-Setup.exe"
    assert info.sha256_url.endswith(".sha256") and "Faster" in info.notes and info.published == "2026-10-12"
    assert await check_for_update("9.1.0", transport=transport(release())) is None
    assert await check_for_update("0.1.0", transport=transport(release(prerelease=True))) is None
    assert await check_for_update("0.1.0", transport=transport(None, status=404)) is None
    with pytest.raises(UpdateError, match="limiting"):
        await check_for_update("0.1.0", transport=transport(None, status=403))


async def test_download_verifies_checksum(tmp_path):
    info = await check_for_update("0.2.0", transport=transport(release()))
    seen = []
    path = await download_update(info, tmp_path, lambda d, t: seen.append((d, t)), transport=transport(release()))
    assert path.read_bytes() == PAYLOAD and seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    with pytest.raises(UpdateError, match="damaged"):
        await download_update(info, tmp_path / "b", transport=transport(release(), bad_hash=True))
    assert not (tmp_path / "b" / "FlowPilot-Setup.exe").exists()
    no_installer = await check_for_update("0.2.0", transport=transport({**release(), "assets": []}))
    with pytest.raises(UpdateError, match="no Windows installer"):
        await download_update(no_installer, tmp_path)


async def test_web_check_is_not_rate_limited(tmp_path):
    """The normal web redirect is used first; the API (which GitHub limits) is never asked."""
    calls = []
    base = transport(release(), status=403, web=True)

    async def track(req):
        calls.append(str(req.url))
        return await base.handle_async_request(req)

    t = httpx.MockTransport(track)
    info = await check_for_update("0.2.0", repo="x/y", transport=t)
    assert info is not None and info.version == "9.1.0" and "Faster" in info.notes
    assert info.installer_url == "https://github.com/x/y/releases/download/v9.1.0/FlowPilot-Setup.exe"
    assert not any("api.github.com" in c for c in calls)
    assert await check_for_update("9.1.0", repo="x/y", transport=t) is None
    assert await check_for_update("0.1.0", repo="x/y", transport=transport(None, web=True)) is None
    path = await download_update(info, tmp_path, transport=t)
    assert path.read_bytes() == PAYLOAD
