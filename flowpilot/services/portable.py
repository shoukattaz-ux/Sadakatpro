"""Export everything to one password-protected file and import it on another PC.

Unlike a backup (a copy of the raw database), an export is *portable*: secrets such as API
keys and account passwords are encrypted on this PC with Windows DPAPI, which only works
for the same Windows user on the same machine. So on export they are decrypted and the
whole data set is sealed with a password the user chooses; on import they are decrypted
with that password and encrypted again for the new PC.

File layout (a zip, extension ``.flowpilot``)::

    manifest.json   format, app version, counts, key-derivation parameters (no secrets)
    data.enc        Fernet token of the JSON data, key = scrypt(password, salt)
"""

from __future__ import annotations

import base64
import json
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from loguru import logger
from sqlalchemy import DateTime, Table, delete, insert, select

from flowpilot import __version__
from flowpilot.config import AppPaths, AppSettings, SettingsStore
from flowpilot.security import SecretBox, SecretError
from flowpilot.services.backup import create_backup
from flowpilot.storage.db import Database
from flowpilot.storage.models import (
    Account,
    Connection,
    Flow,
    FlowVersion,
    Group,
    Run,
    RunItem,
    Schedule,
    SelectorHeal,
)

FORMAT = "flowpilot-export"
FORMAT_VERSION = 1
EXTENSION = ".flowpilot"
MIN_PASSWORD = 4

#: Tables in insert order (parents first). Run history is optional.
CORE_TABLES: list[type] = [Connection, Group, Account, Flow, FlowVersion, Schedule, SelectorHeal]
HISTORY_TABLES: list[type] = [Run, RunItem]
#: Run item columns that point at files on the old PC.
_FILE_COLUMNS = ("screenshot_path", "html_path", "trace_path")


class PortableError(RuntimeError):
    """A user-friendly problem with an export file or password."""


@dataclass
class ExportSummary:
    path: Path
    counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportSummary:
    counts: dict[str, int]
    mode: str
    backup: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        labels = {"connections": "connection(s)", "groups": "group(s)", "accounts": "account(s)",
                  "flows": "flow(s)", "schedules": "schedule(s)", "runs": "run(s) of history"}
        parts = [f"{self.counts[k]} {v}" for k, v in labels.items() if self.counts.get(k)]
        return ", ".join(parts) or "nothing new"


# --------------------------------------------------------------------------- crypto
def _derive(password: str, salt: bytes, n: int = 2**15, r: int = 8, p: int = 1) -> Fernet:
    key = Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


# --------------------------------------------------------------------------- helpers
def _table(model: type) -> Table:
    return model.__table__  # type: ignore[attr-defined,no-any-return]


def _dump_rows(db: Database, model: type) -> list[dict[str, Any]]:
    table = _table(model)
    with db.engine.connect() as conn:
        rows = conn.execute(select(table).order_by(table.c.id)).mappings().all()
    out = []
    for row in rows:
        out.append({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()})
    return out


def _clean_row(model: type, row: dict[str, Any]) -> dict[str, Any]:
    """Keep known columns only and turn ISO strings back into datetimes."""
    table = _table(model)
    clean: dict[str, Any] = {}
    for col in table.columns:
        if col.name not in row:
            continue
        value = row[col.name]
        if isinstance(col.type, DateTime) and isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                value = None
        clean[col.name] = value
    return clean


def _key(model: type) -> str:
    return _table(model).name


# --------------------------------------------------------------------------- export
def export_all(db: Database, secrets: SecretBox, settings: AppSettings, path: Path, password: str,
               include_history: bool = False) -> ExportSummary:
    """Write every flow, account, connection, schedule and setting to one sealed file."""
    if len(password) < MIN_PASSWORD:
        raise PortableError(f"Choose a password with at least {MIN_PASSWORD} characters.")
    warnings: list[str] = []

    def plain(stored: str, what: str) -> str:
        try:
            return secrets.decrypt(stored)
        except SecretError:
            warnings.append(f"The secret of {what} could not be read on this PC and was left empty.")
            return ""

    data: dict[str, Any] = {}
    for model in CORE_TABLES + (HISTORY_TABLES if include_history else []):
        data[_key(model)] = _dump_rows(db, model)
    for conn in data["connections"]:
        conn["api_key"] = plain(conn.pop("api_key_encrypted", ""), f"connection “{conn.get('name')}”")
    for acc in data["accounts"]:
        stored = acc.pop("variables_encrypted_json", "")
        variables: dict[str, str] = {}
        if stored:
            try:
                variables = json.loads(plain(stored, f"account “{acc.get('name')}”") or "{}")
            except ValueError:
                variables = {}
        acc["variables"] = variables
    for item in data.get("run_items", []):
        for col in _FILE_COLUMNS:
            item[col] = ""
    sdump = settings.model_dump(mode="json")
    sdump["ai_api_key"] = plain(sdump.pop("ai_api_key_encrypted", ""), "the AI key")
    data["settings"] = sdump

    counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    salt = os.urandom(16)
    kdf = {"name": "scrypt", "n": 2**15, "r": 8, "p": 1, "salt": base64.b64encode(salt).decode()}
    token = _derive(password, salt).encrypt(json.dumps(data).encode("utf-8"))
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": __version__,
                "created": datetime.now().isoformat(timespec="seconds"), "counts": counts,
                "history": include_history, "kdf": kdf}

    path = Path(path)
    if path.suffix.lower() != EXTENSION:
        path = path.with_name(path.name + EXTENSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("data.enc", token)
    tmp.replace(path)
    logger.info("Exported all data to {} ({})", path, counts)
    return ExportSummary(path, counts, warnings)


# --------------------------------------------------------------------------- import
def read_manifest(path: Path) -> dict[str, Any]:
    """Return the (unencrypted) manifest, or raise :class:`PortableError`."""
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise PortableError("This file is not a FlowPilot export.") from exc
    if manifest.get("format") != FORMAT:
        raise PortableError("This file is not a FlowPilot export.")
    if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise PortableError("This export was made by a newer FlowPilot. Update FlowPilot first "
                            "(Settings → About → Check for updates), then import again.")
    return manifest


def read_export(path: Path, password: str) -> dict[str, Any]:
    """Decrypt and return the data of an export file."""
    manifest = read_manifest(path)
    kdf = manifest.get("kdf", {})
    try:
        salt = base64.b64decode(kdf["salt"])
        fernet = _derive(password, salt, int(kdf.get("n", 2**15)), int(kdf.get("r", 8)), int(kdf.get("p", 1)))
        with zipfile.ZipFile(path) as zf:
            token = zf.read("data.enc")
        return json.loads(fernet.decrypt(token))  # type: ignore[no-any-return]
    except InvalidToken as exc:
        raise PortableError("Wrong password — this export cannot be opened with it.") from exc
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise PortableError("The export file is damaged and cannot be read.") from exc


def import_all(paths: AppPaths, db: Database, secrets: SecretBox, settings_store: SettingsStore, repos: Any,
               path: Path, password: str, mode: str = "replace") -> ImportSummary:
    """Restore an export. ``replace`` makes this PC an exact copy of the exported data;
    ``merge`` adds what is missing and keeps everything already here.

    A safety backup of the current data is made first. All database changes happen in one
    transaction — if anything fails, nothing is changed.
    """
    if mode not in ("replace", "merge"):
        raise ValueError(mode)
    data = read_export(path, password)
    backup: Path | None = None
    try:
        backup = create_backup(paths, repos, paths.backups /
                               f"before-import-{datetime.now():%Y%m%d-%H%M%S}.zip")
    except Exception as exc:  # pragma: no cover - a failed safety copy must not block the import
        logger.warning("Safety backup before import failed: {}", exc)

    # secrets: plain text in the file → encrypted for this PC
    for conn in data.get("connections", []):
        conn["api_key_encrypted"] = secrets.encrypt(str(conn.pop("api_key", "") or ""))
    for acc in data.get("accounts", []):
        variables = acc.pop("variables", {}) or {}
        acc["variables_encrypted_json"] = secrets.encrypt(json.dumps(variables)) if variables else ""

    if mode == "replace":
        counts = _replace(db, data)
    else:
        counts = _merge(db, data)

    warnings: list[str] = []
    if "settings" in data and mode == "replace":
        _restore_settings(settings_store, secrets, data["settings"])
    if any(c.get("type") == "local" for c in data.get("connections", [])):
        warnings.append("Profiles of the built-in “Local Chrome” connection keep their cookies in a folder on "
                        "the old PC; on this PC they start empty (log in once again).")
    logger.info("Imported {} ({}): {}", path, mode, counts)
    return ImportSummary(counts, mode, backup, warnings)


def _restore_settings(store: SettingsStore, secrets: SecretBox, raw: dict[str, Any]) -> None:
    raw = dict(raw)
    ai_key = str(raw.pop("ai_api_key", "") or "")
    raw["ai_api_key_encrypted"] = secrets.encrypt(ai_key) if ai_key else ""
    known = {k: v for k, v in raw.items() if k in AppSettings.model_fields}
    try:
        incoming = AppSettings.model_validate(known)
    except Exception as exc:  # keep current settings if the file holds something odd
        logger.warning("Settings in export ignored: {}", exc)
        return
    incoming.setup_completed = True
    # update the live object in place: the UI holds references to it
    current = store.settings
    for name in AppSettings.model_fields:
        setattr(current, name, getattr(incoming, name))
    store.save()


def _replace(db: Database, data: dict[str, Any]) -> dict[str, int]:
    # run history on this PC is kept unless the export carries its own
    models = CORE_TABLES + (HISTORY_TABLES if "runs" in data else [])
    counts: dict[str, int] = {}
    with db.engine.begin() as conn:
        for model in reversed(models):  # children first
            conn.execute(delete(_table(model)))
        ids: dict[str, set[int]] = {}
        for model in models:
            name = _key(model)
            rows = [_clean_row(model, r) for r in data.get(name, [])]
            rows = [_fix_refs(name, r, ids) for r in rows]
            rows = [r for r in rows if r is not None]
            if rows:
                conn.execute(insert(_table(model)), rows)
            ids[name] = {r["id"] for r in rows if r.get("id") is not None}
            counts[name] = len(rows)
    return counts


def _fix_refs(table: str, row: dict[str, Any], ids: dict[str, set[int]]) -> dict[str, Any] | None:
    """Drop dangling references so a slightly inconsistent export still imports."""

    def ok(ref: str, value: Any) -> bool:
        return value is None or value in ids.get(ref, set())

    if table == "accounts":
        if not ok("connections", row.get("connection_id")):
            row["connection_id"] = None
        if not ok("groups", row.get("group_id")):
            row["group_id"] = None
    elif table in ("flow_versions", "schedules"):
        if not ok("flows", row.get("flow_id")) or row.get("flow_id") is None:
            return None
    elif table in ("selector_heals", "runs"):
        if not ok("flows", row.get("flow_id")):
            row["flow_id"] = None
    elif table == "run_items":
        if not ok("runs", row.get("run_id")) or row.get("run_id") is None:
            return None
        if not ok("accounts", row.get("account_id")):
            row["account_id"] = None
    return row


def _merge(db: Database, data: dict[str, Any]) -> dict[str, int]:
    counts = {"connections": 0, "groups": 0, "accounts": 0, "flows": 0, "schedules": 0}
    maps: dict[str, dict[int, int]] = {k: {} for k in ("connections", "groups", "accounts", "flows")}

    with db.engine.begin() as conn:
        def existing(model: type) -> list[dict[str, Any]]:
            return [dict(r) for r in conn.execute(select(_table(model))).mappings().all()]

        def add(model: type, row: dict[str, Any]) -> int:
            row = _clean_row(model, row)
            row.pop("id", None)
            return int(conn.execute(insert(_table(model)).values(**row)).inserted_primary_key[0])

        def match(model: type, rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any], table: str,
                  prepare: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None) -> None:
            have = {key(r): r["id"] for r in existing(model)}
            for row in rows:
                k = key(row)
                if k in have:
                    maps[table][row["id"]] = have[k]
                    continue
                prepared = prepare(dict(row)) if prepare else dict(row)
                if prepared is None:
                    continue
                new_id = add(model, prepared)
                have[k] = new_id
                maps[table][row["id"]] = new_id
                counts[table] += 1

        match(Connection, data.get("connections", []), lambda r: (r["type"], r.get("base_url", ""), r["name"]),
              "connections")
        match(Group, data.get("groups", []), lambda r: r["name"], "groups")

        def account_key(r: dict[str, Any]) -> Any:
            return (r.get("connection_id"), r.get("remote_profile_id") or "", r["name"] if not
                    r.get("remote_profile_id") else "")

        def prep_account(r: dict[str, Any]) -> dict[str, Any]:
            r["group_id"] = maps["groups"].get(r.get("group_id"))  # type: ignore[arg-type]
            return r

        incoming_accounts = []
        for r in data.get("accounts", []):
            r = dict(r)
            r["connection_id"] = maps["connections"].get(r.get("connection_id"))  # type: ignore[arg-type]
            incoming_accounts.append(r)
        match(Account, incoming_accounts, account_key, "accounts", prep_account)

        # flows: identical name + steps = same flow; same name, other steps = add a copy
        have_flows = existing(Flow)
        names = {f["name"] for f in have_flows}
        same = {(f["name"], f["steps_json"]): f["id"] for f in have_flows}
        for row in data.get("flows", []):
            steps = row.get("steps_json", "[]")
            hit = same.get((row["name"], steps), same.get((f"{row['name']} (imported)", steps)))
            if hit is not None:
                maps["flows"][row["id"]] = hit
                continue
            new = dict(row)
            if new["name"] in names:
                new["name"] = f"{new['name']} (imported)"
            names.add(new["name"])
            maps["flows"][row["id"]] = add(Flow, new)
            counts["flows"] += 1
            for v in data.get("flow_versions", []):
                if v.get("flow_id") == row["id"]:
                    add(FlowVersion, {**v, "flow_id": maps["flows"][row["id"]]})

        have_scheds = {(r["name"], r["flow_id"], r["cron_or_interval"], r["target_json"])
                       for r in existing(Schedule)}
        for row in data.get("schedules", []):
            flow_id = maps["flows"].get(row.get("flow_id"))  # type: ignore[arg-type]
            if flow_id is None:
                continue
            try:
                target = json.loads(row.get("target_json") or "{}")
            except ValueError:
                target = {}
            for key, table in (("group_ids", "groups"), ("group_order", "groups"), ("account_ids", "accounts")):
                if isinstance(target.get(key), list):
                    target[key] = [maps[table][i] for i in target[key] if i in maps[table]]
            sig = (row.get("name", ""), flow_id, row.get("cron_or_interval", "{}"), json.dumps(target))
            if sig in have_scheds:
                continue
            have_scheds.add(sig)
            add(Schedule, {**row, "flow_id": flow_id, "target_json": sig[3], "next_run": None})
            counts["schedules"] += 1
    return counts
