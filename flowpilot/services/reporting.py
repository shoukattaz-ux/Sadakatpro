"""Exports of run results and log retention."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from flowpilot.config import AppPaths
from flowpilot.storage.models import RunItem
from flowpilot.storage.repositories import Repositories

COLUMNS = ["run_id", "account", "group", "status", "error", "started", "finished", "duration_s", "attempts",
           "screenshot", "results"]


def item_rows(items: list[RunItem]) -> list[dict[str, Any]]:
    rows = []
    for it in items:
        try:
            results = json.loads(it.results_json or "{}")
        except ValueError:
            results = {}
        results.pop("vars", None)
        rows.append({
            "run_id": it.run_id, "account": it.account_name, "group": it.group_name, "status": it.status,
            "error": it.error, "started": it.started_at.isoformat(sep=" ") if it.started_at else "",
            "finished": it.finished_at.isoformat(sep=" ") if it.finished_at else "", "duration_s": it.duration_s,
            "attempts": it.attempts, "screenshot": it.screenshot_path,
            "results": json.dumps(results, ensure_ascii=False) if results else "",
            **{f"result.{k}": v for k, v in results.items()},
        })
    return rows


def export_items(items: list[RunItem], path: Path) -> Path:
    """Write run items to ``.csv`` or ``.xlsx`` (decided by the file extension)."""
    rows = item_rows(items)
    columns = COLUMNS + sorted({k for r in rows for k in r if k.startswith("result.")})
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Results"
        ws.append(columns)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rows:
            ws.append([str(r.get(c, "")) if r.get(c) is not None else "" for c in columns])
        wb.save(path)
    else:
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return path


def apply_retention(repos: Repositories, paths: AppPaths, days: int) -> int:
    """Delete runs, screenshots, traces and log files older than ``days``. Returns runs removed."""
    if days <= 0:
        return 0
    items = repos.runs.delete_older_than(days)
    for it in items:
        for f in (it.screenshot_path, it.html_path, it.trace_path):
            if f:
                Path(f).unlink(missing_ok=True)
    cutoff = time.time() - days * 86400
    for folder in (paths.screenshots, paths.traces, paths.logs):
        for f in folder.rglob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        for d in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()  # only removes empty folders
            except OSError:
                pass
    run_ids = {it.run_id for it in items}
    if run_ids:
        logger.info("Log retention removed {} old run(s)", len(run_ids))
    return len(run_ids)
