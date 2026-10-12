"""Backup and restore of all user data as a single zip file."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flowpilot import __version__
from flowpilot.config import AppPaths
from flowpilot.storage.repositories import Repositories

MANIFEST = "flowpilot-backup.json"


def create_backup(paths: AppPaths, repos: Repositories, target: Path | None = None) -> Path:
    """Zip the database (consistent copy), settings, encryption key and every flow as JSON."""
    target = target or paths.backups / f"FlowPilot-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "flowpilot.db"
        src = sqlite3.connect(str(paths.db_file))
        dst = sqlite3.connect(str(db_copy))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps({"app": "FlowPilot", "version": __version__,
                                              "created": datetime.now().isoformat(timespec="seconds")}))
            zf.write(db_copy, "flowpilot.db")
            if paths.settings_file.exists():
                zf.write(paths.settings_file, "settings.json")
            key = paths.root / "secret.key"
            if key.exists():
                zf.write(key, "secret.key")
            for flow in repos.flows.list():
                data = {"name": flow.name, "description": flow.description, "steps": json.loads(flow.steps_json)}
                safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in flow.name)[:60]
                zf.writestr(f"flows/{flow.id:04d} {safe}.json", json.dumps(data, indent=2))
    return target


def restore_backup(paths: AppPaths, archive: Path) -> None:
    """Replace the current data with a backup. The app must restart afterwards."""
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        if MANIFEST not in names or "flowpilot.db" not in names:
            raise ValueError("This file is not a FlowPilot backup.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = paths.backups / f"before-restore-{stamp}"
        safety.mkdir(parents=True, exist_ok=True)
        for f in (paths.db_file, paths.settings_file, paths.root / "secret.key"):
            if f.exists():
                shutil.copy2(f, safety / f.name)
        for suffix in ("-wal", "-shm"):
            Path(str(paths.db_file) + suffix).unlink(missing_ok=True)
        with zf.open("flowpilot.db") as src, paths.db_file.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        if "settings.json" in names:
            paths.settings_file.write_bytes(zf.read("settings.json"))
        if "secret.key" in names:
            (paths.root / "secret.key").write_bytes(zf.read("secret.key"))
