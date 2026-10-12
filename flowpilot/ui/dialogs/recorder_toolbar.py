"""Floating, always-on-top recorder toolbar."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from flowpilot.ui.widgets.clay import ClayButton, ClayCard, muted_label


class RecorderToolbar(QWidget):
    pause_toggled = Signal(bool)
    stop_clicked = Signal()
    add_wait = Signal()
    add_screenshot = Signal()
    add_close = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag: QPoint | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = ClayCard(padding=8, radius=22)
        outer.addWidget(card)
        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #F27C7C; font-size: 16pt;")
        self.title = QLabel("Recording")
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        top.addWidget(self.dot)
        top.addWidget(self.title)
        self.count = muted_label("0 steps", wrap=False)
        top.addStretch(1)
        top.addWidget(self.count)
        card.body.addLayout(top)
        self.status = muted_label("Use the browser normally — every action becomes a step.")
        card.body.addWidget(self.status)
        row = QHBoxLayout()
        self.pause = ClayButton("Pause", "pause", compact=True)
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self._on_pause)
        wait = ClayButton("Add wait", "clock", compact=True)
        wait.clicked.connect(self.add_wait.emit)
        shot = ClayButton("Screenshot", "camera", compact=True)
        shot.clicked.connect(self.add_screenshot.emit)
        close = ClayButton("Close browser", "power", compact=True)
        close.clicked.connect(self.add_close.emit)
        stop = ClayButton("Stop", "stop", "danger", compact=True)
        stop.clicked.connect(self.stop_clicked.emit)
        for b in (self.pause, wait, shot, close, stop):
            row.addWidget(b)
        card.body.addLayout(row)

    def _on_pause(self, paused: bool) -> None:
        self.pause.setText("Resume" if paused else "Pause")
        self.pause.icon_name = "play" if paused else "pause"
        self.title.setText("Paused" if paused else "Recording")
        self.dot.setStyleSheet(f"color: {'#F5B75C' if paused else '#F27C7C'}; font-size: 16pt;")
        self.pause_toggled.emit(paused)

    def set_count(self, n: int) -> None:
        self.count.setText(f"{n} step{'s' if n != 1 else ''}")

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    # drag the frameless window around
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag = None
