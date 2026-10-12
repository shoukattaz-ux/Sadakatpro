"""Claymorphism colour palettes and the ThemeManager."""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    card: str
    card_alt: str
    input: str
    primary: str
    secondary: str
    success: str
    warning: str
    danger: str
    text: str
    muted: str
    shadow_dark: str  # rgba string
    shadow_light: str
    border: str
    selection: str

    def c(self, key: str) -> QColor:
        """Colour by attribute name (accepts ``#hex`` and ``rgba(r,g,b,a)`` strings)."""
        return to_qcolor(getattr(self, key))


LIGHT = Palette(
    name="light", bg="#EEF1F8", card="#F6F8FD", card_alt="#E9EDF7", input="#E6EAF4",
    primary="#7C6CF2", secondary="#5EC8D8", success="#6BCB8B", warning="#F5B75C", danger="#F27C7C",
    text="#2E3350", muted="#8A90A8", shadow_dark="rgba(150,163,190,0.55)", shadow_light="rgba(255,255,255,0.95)",
    border="#DDE2EE", selection="#DCD7FC",
)

DARK = Palette(
    name="dark", bg="#23263A", card="#2C3048", card_alt="#272B41", input="#24283C",
    primary="#7A6EDB", secondary="#5BAFBD", success="#66B482", warning="#D9A75C", danger="#D57A7A",
    text="#E8EAF6", muted="#9AA0BC", shadow_dark="rgba(8,9,18,0.55)", shadow_light="rgba(255,255,255,0.06)",
    border="#363B57", selection="#433D7A",
)

#: status -> palette attribute for pills and accents
STATUS_COLORS = {
    "queued": "muted", "starting": "secondary", "running": "primary", "waiting": "warning", "done": "success",
    "failed": "danger", "stopped": "muted", "skipped": "muted", "paused": "warning", "ok": "success",
}


def to_qcolor(value: str) -> QColor:
    value = value.strip()
    if value.startswith("rgba"):
        r, g, b, a = (float(x) for x in value[value.index("(") + 1: value.index(")")].split(","))
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(a)
        return color
    return QColor(value)


def mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours (t=0 -> a, t=1 -> b)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


class ThemeManager(QObject):
    """Holds the active palette; widgets repaint when :attr:`changed` fires."""

    changed = Signal(object)
    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self.mode = "light"
        self.palette: Palette = LIGHT
        self.font_size = 10

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    def resolve(self, mode: str) -> Palette:
        if mode == "system":
            hints = QGuiApplication.styleHints()
            scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Light
            return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
        return DARK if mode == "dark" else LIGHT

    def apply(self, mode: str, font_size: int | None = None) -> None:
        from flowpilot.ui.theme.qss import apply_app_style

        self.mode = mode
        self.palette = self.resolve(mode)
        if font_size:
            self.font_size = font_size
        apply_app_style(self.palette, self.font_size)
        self.changed.emit(self.palette)


def pal() -> Palette:
    return ThemeManager.instance().palette


def with_primary(p: Palette, color: str) -> Palette:
    return replace(p, primary=color)
