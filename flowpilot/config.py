"""Application paths and user settings.

All user data lives in ``%APPDATA%/FlowPilot`` on Windows (``~/.flowpilot`` elsewhere).
The location can be overridden with the ``FLOWPILOT_HOME`` environment variable, which
the test-suite uses to keep each test isolated.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from flowpilot import APP_NAME


def default_data_dir() -> Path:
    """Return the per-user data folder (not created)."""
    override = os.environ.get("FLOWPILOT_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


@dataclass(frozen=True)
class AppPaths:
    """All folders the app writes to."""

    root: Path

    @property
    def db_file(self) -> Path:
        return self.root / "flowpilot.db"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def screenshots(self) -> Path:
        return self.root / "screenshots"

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> "AppPaths":
        for folder in (self.root, self.logs, self.screenshots, self.traces, self.exports, self.backups):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def resource_path(*parts: str) -> Path:
    """Path to a bundled resource; works from source and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath("flowpilot", "resources", *parts)


# --------------------------------------------------------------------------- settings

HumanizePreset = Literal["fast", "normal", "careful", "custom"]


class HumanizeSettings(BaseModel):
    """Values that control how 'human' the automation behaves."""

    preset: HumanizePreset = "normal"
    mouse_enabled: bool = True
    mouse_speed: float = Field(1.0, description="1.0 = normal, 2.0 = twice as fast")
    overshoot_chance: float = 0.25
    typing_min_ms: int = 60
    typing_max_ms: int = 160
    word_pause_chance: float = 0.15
    word_pause_min_ms: int = 200
    word_pause_max_ms: int = 600
    typo_chance: float = 0.0
    scroll_step_min: int = 80
    scroll_step_max: int = 240
    scroll_back_chance: float = 0.1
    idle_jitter: bool = False
    step_delay_min: float = 0.3
    step_delay_max: float = 1.2


HUMANIZE_PRESETS: dict[str, dict[str, float | int | bool]] = {
    "fast": dict(mouse_speed=2.2, overshoot_chance=0.05, typing_min_ms=20, typing_max_ms=60,
                 word_pause_chance=0.03, step_delay_min=0.05, step_delay_max=0.3, scroll_back_chance=0.0),
    "normal": dict(mouse_speed=1.0, overshoot_chance=0.25, typing_min_ms=60, typing_max_ms=160,
                   word_pause_chance=0.15, step_delay_min=0.3, step_delay_max=1.2, scroll_back_chance=0.1),
    "careful": dict(mouse_speed=0.6, overshoot_chance=0.4, typing_min_ms=110, typing_max_ms=280,
                    word_pause_chance=0.3, step_delay_min=0.8, step_delay_max=2.5, scroll_back_chance=0.2),
}


def apply_humanize_preset(h: HumanizeSettings, preset: str) -> HumanizeSettings:
    """Return a copy of ``h`` with the values of a named preset applied."""
    values = HUMANIZE_PRESETS.get(preset, {})
    return h.model_copy(update={**values, "preset": preset})


class AppSettings(BaseModel):
    """Everything the user can change on the Settings page (except connections)."""

    # defaults for steps
    step_timeout_s: float = 15.0
    step_retries: int = 1
    step_retry_delay_s: float = 1.0
    screenshot_on_error: bool = True
    html_on_error: bool = True
    trace_on_error: bool = False
    # behavior
    auto_switch_new_tab: bool = True
    dialog_action: Literal["accept", "dismiss"] = "accept"
    close_browser_at_end: bool = True
    retry_failed_accounts: int = 0
    max_run_minutes: float = 60.0
    skip_succeeded_today: bool = False
    resource_guard: bool = True
    resource_guard_cpu: float = 90.0
    resource_guard_ram: float = 90.0
    # humanize
    humanize: HumanizeSettings = Field(default_factory=HumanizeSettings)
    # appearance
    theme: Literal["light", "dark", "system"] = "light"
    font_size: int = 10
    # data
    log_retention_days: int = 30
    # notifications
    notify_desktop: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # first launch wizard
    setup_completed: bool = False
    tray_mode: bool = False


class SettingsStore:
    """Loads and saves :class:`AppSettings` as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.settings = self._load()

    def _load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            return AppSettings.model_validate(json.loads(self.path.read_text("utf-8")))
        except (ValidationError, ValueError, OSError) as exc:
            logger.warning("Settings file unreadable, using defaults: {}", exc)
            return AppSettings()

    def save(self, settings: AppSettings | None = None) -> None:
        if settings is not None:
            self.settings = settings
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.settings.model_dump_json(indent=2), "utf-8")
        tmp.replace(self.path)
