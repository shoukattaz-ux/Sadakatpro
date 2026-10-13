"""Auto-update from GitHub Releases.

Publishing an update = pushing a tag such as ``v0.3.0`` to GitHub. The release workflow
(``.github/workflows/release.yml``) builds ``FlowPilot-Setup.exe`` (+ ``.sha256``) and attaches
them to a GitHub Release. Installed copies check the latest release, download the installer,
verify its SHA-256 checksum and run it silently; the installer replaces the app and starts it
again. User data lives in ``%APPDATA%\\FlowPilot`` and is never touched by an update.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from flowpilot import __version__

GITHUB_REPO = os.environ.get("FLOWPILOT_UPDATE_REPO", "shoukattaz-ux/Sadakatpro")
LATEST_API = "https://api.github.com/repos/{repo}/releases/latest"
#: The normal web address redirects to the newest release's tag; unlike the API it has no
#: 60-checks-per-hour limit (which people sharing an internet address hit all the time).
LATEST_WEB = "https://github.com/{repo}/releases/latest"
DOWNLOAD = "https://github.com/{repo}/releases/download/{tag}/{name}"
INSTALLER_NAME = "FlowPilot-Setup.exe"
NOTES_NAME = "release-notes.md"
INSTALLER_RE = re.compile(r"^FlowPilot-Setup(?:-[\d.]+)?\.exe$", re.I)
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": f"FlowPilot/{__version__}"}


class UpdateError(RuntimeError):
    """Plain-language update problem."""


@dataclass
class UpdateInfo:
    version: str
    notes: str
    published: str
    page_url: str
    installer_url: str = ""
    installer_name: str = ""
    installer_size: int = 0
    sha256_url: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10). Pre-release suffixes are ignored."""
    nums = re.findall(r"\d+", text.split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def can_self_update() -> bool:
    """Only the installed Windows build can replace itself."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=30.0, follow_redirects=True, transport=transport)


async def check_for_update(current: str = __version__, repo: str = GITHUB_REPO,
                           transport: httpx.AsyncBaseTransport | None = None) -> UpdateInfo | None:
    """Return the newer release, or None when this version is the latest."""
    async with _client(transport) as client:
        try:
            tag = await _latest_tag_from_web(client, repo)
        except (httpx.HTTPError, UpdateError) as exc:
            logger.debug("Web update check failed ({}), asking the GitHub API", exc)
            return await _check_api(client, current, repo)
        if tag is None or not is_newer(tag, current):
            return None
        info = UpdateInfo(version=tag.lstrip("vV"), notes="", published="",
                          page_url=f"https://github.com/{repo}/releases/tag/{tag}",
                          installer_url=DOWNLOAD.format(repo=repo, tag=tag, name=INSTALLER_NAME),
                          installer_name=INSTALLER_NAME,
                          sha256_url=DOWNLOAD.format(repo=repo, tag=tag, name=INSTALLER_NAME + ".sha256"))
        try:
            notes = await client.get(DOWNLOAD.format(repo=repo, tag=tag, name=NOTES_NAME))
            if notes.status_code == 200:
                info.notes = notes.text.strip()
        except httpx.HTTPError:
            pass
        return info


async def _latest_tag_from_web(client: httpx.AsyncClient, repo: str) -> str | None:
    """The tag of the newest published release, read from the redirect of /releases/latest."""
    resp = await client.get(LATEST_WEB.format(repo=repo), follow_redirects=False)
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "")
        match = re.search(r"/releases/tag/([^/?#]+)", location)
        if match:
            return httpx.URL("https://x/" + match.group(1)).path.lstrip("/")  # undo %-escapes
        if location.rstrip("/").endswith("/releases"):
            return None  # no release published yet
    if resp.status_code == 404:
        return None
    raise UpdateError(f"Unexpected answer from GitHub ({resp.status_code}).")


async def _check_api(client: httpx.AsyncClient, current: str, repo: str) -> UpdateInfo | None:
    try:
        resp = await client.get(LATEST_API.format(repo=repo))
    except httpx.HTTPError as exc:
        raise UpdateError("Could not reach GitHub to check for updates. Check your internet connection.") from exc
    if resp.status_code == 404:
        return None  # no release published yet
    if resp.status_code in (403, 429):
        raise UpdateError("GitHub is limiting update checks right now. Try again in an hour.")
    if resp.status_code >= 400:
        raise UpdateError(f"GitHub answered with an error ({resp.status_code}) while checking for updates.")
    data = resp.json()
    tag = str(data.get("tag_name") or "")
    if data.get("draft") or data.get("prerelease") or not is_newer(tag, current):
        return None
    info = UpdateInfo(version=tag.lstrip("vV"), notes=str(data.get("body") or "").strip(),
                      published=str(data.get("published_at") or "")[:10], page_url=str(data.get("html_url") or ""))
    assets = data.get("assets") or []
    for a in assets:
        name = str(a.get("name", ""))
        if INSTALLER_RE.match(name) and not info.installer_url:
            info.installer_url = str(a.get("browser_download_url", ""))
            info.installer_name = name
            info.installer_size = int(a.get("size") or 0)
    for a in assets:
        if str(a.get("name", "")).lower() == f"{info.installer_name.lower()}.sha256":
            info.sha256_url = str(a.get("browser_download_url", ""))
    return info


def _expected_hash(text: str) -> str:
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    if not match:
        raise UpdateError("The update's checksum file is not valid.")
    return match.group(0).lower()


async def download_update(info: UpdateInfo, dest_dir: Path | None = None,
                          progress: Callable[[int, int], None] | None = None,
                          transport: httpx.AsyncBaseTransport | None = None) -> Path:
    """Download the installer and verify its SHA-256 checksum. Returns the file path."""
    if not info.installer_url:
        raise UpdateError("This release has no Windows installer attached yet. Try again later.")
    dest_dir = dest_dir or Path(tempfile.mkdtemp(prefix="flowpilot-update-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (info.installer_name or "FlowPilot-Setup.exe")
    digest = hashlib.sha256()
    async with _client(transport) as client:
        expected = ""
        if info.sha256_url:
            r = await client.get(info.sha256_url)
            if r.status_code >= 400:
                raise UpdateError("Could not download the update's checksum.")
            expected = _expected_hash(r.text)
        try:
            async with client.stream("GET", info.installer_url) as resp:
                if resp.status_code == 404:
                    raise UpdateError("This release has no Windows installer attached yet. Try again later.")
                if resp.status_code >= 400:
                    raise UpdateError(f"Download failed (HTTP {resp.status_code}).")
                total = int(resp.headers.get("content-length") or info.installer_size or 0)
                done = 0
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        fh.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except httpx.HTTPError as exc:
            target.unlink(missing_ok=True)
            raise UpdateError("The download was interrupted. Please try again.") from exc
    if expected and digest.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("The downloaded update is damaged (checksum mismatch) and was deleted. Please try again.")
    if not expected:
        logger.warning("Update {} has no checksum file; installing without verification", info.version)
    return target


def launch_installer(path: Path) -> Any:
    """Start the installer silently; it closes FlowPilot, updates it and starts it again."""
    args = [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/SP-"]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    logger.info("Starting installer {}", path)
    return subprocess.Popen(args, creationflags=flags, close_fds=True)
