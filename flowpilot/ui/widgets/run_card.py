"""Live card for one account in a running batch."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from flowpilot.ui.widgets.clay import ClayButton, ClayCard, IconButton, StatusPill, muted_label

ACTIVE = {"starting", "running", "waiting"}


class RunCard(ClayCard):
    pause_clicked = Signal(int)
    resume_clicked = Signal(int)
    stop_clicked = Signal(int)
    logs_clicked = Signal(int)
    front_clicked = Signal(int)

    WIDTH = 320

    def __init__(self, item_id: int, account: str, group: str, total_steps: int, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=22, padding=10, hoverable=True)
        self.item_id = item_id
        self.total_steps = max(1, total_steps)
        self.status = "queued"
        self.started: float | None = None
        self.finished: float | None = None
        self.logs: list[str] = []
        self.setFixedWidth(self.WIDTH)
        top = QHBoxLayout()
        name_col = QWidget()
        name_col.setObjectName("Transparent")
        nl = QHBoxLayout(name_col)
        nl.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(account)
        f = self.name.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        self.name.setFont(f)
        nl.addWidget(self.name)
        top.addWidget(name_col, 1)
        self.pill = StatusPill("queued")
        top.addWidget(self.pill)
        self.body.addLayout(top)
        self.group = muted_label(group or "No group", wrap=False)
        self.body.addWidget(self.group)
        self.thumb = QLabel()
        self.thumb.setFixedHeight(0)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb_path = ""
        self.body.addWidget(self.thumb)
        self.step = QLabel("Waiting to start…")
        self.step.setWordWrap(True)
        self.step.setMinimumHeight(34)
        self.body.addWidget(self.step)
        self.progress = QProgressBar()
        self.progress.setRange(0, self.total_steps)
        self.progress.setValue(0)
        self.progress.setFixedHeight(10)
        self.body.addWidget(self.progress)
        info_row = QHBoxLayout()
        self.elapsed = muted_label("0:00", wrap=False)
        info_row.addWidget(self.elapsed)
        info_row.addStretch(1)
        self.body.addLayout(info_row)
        self.manual = QLabel("")
        self.manual.setWordWrap(True)
        self.manual.setProperty("role", "error")
        self.continue_btn = ClayButton("Continue", "play", "success", compact=True)
        self.continue_btn.clicked.connect(lambda: self.resume_clicked.emit(self.item_id))
        self.manual.hide()
        self.continue_btn.hide()
        self.body.addWidget(self.manual)
        self.body.addWidget(self.continue_btn)
        btns = QHBoxLayout()
        self.pause_btn = IconButton("pause", "Pause this account", size=30)
        self.pause_btn.clicked.connect(self._toggle_pause)
        stop = IconButton("square", "Stop this account", "danger", size=30)
        stop.clicked.connect(lambda: self.stop_clicked.emit(self.item_id))
        logs = IconButton("list", "Show log", size=30)
        logs.clicked.connect(lambda: self.logs_clicked.emit(self.item_id))
        front = IconButton("maximize", "Bring browser to front", size=30)
        front.clicked.connect(lambda: self.front_clicked.emit(self.item_id))
        for b in (self.pause_btn, stop, logs, front):
            btns.addWidget(b)
        btns.addStretch(1)
        self.body.addLayout(btns)
        self.paused = False

    def _toggle_pause(self) -> None:
        if self.paused:
            self.resume_clicked.emit(self.item_id)
        else:
            self.pause_clicked.emit(self.item_id)

    def set_status(self, status: str, message: str = "", manual: str = "") -> None:
        if status in ACTIVE and self.started is None:
            self.started = time.monotonic()
        if status in ("done", "failed", "stopped", "skipped") and self.finished is None:
            self.finished = time.monotonic()
        self.status = status
        self.paused = status == "waiting" and message == "Paused"
        self.pause_btn.icon_name = "play" if self.paused else "pause"
        self.pause_btn.setToolTip("Resume this account" if self.paused else "Pause this account")
        self.pause_btn.update()
        self.pill.set_status(status, text="Paused" if self.paused else None)
        if manual:
            self.pill.set_status("waiting", text="Needs you")
            self.manual.setText(f"✋ {manual}")
        self.manual.setVisible(bool(manual))
        self.continue_btn.setVisible(bool(manual))
        if status == "done":
            self.progress.setValue(self.total_steps)
            self.step.setText(message or "Finished successfully.")
        elif status == "failed":
            self.step.setText(f"✖ {message}" if message else "Failed.")
            self.step.setProperty("role", "error")
            self.step.style().polish(self.step)
        elif message and status in ("queued", "starting", "stopped"):
            self.step.setText(message)
        self.tick()

    def set_step(self, index: int, total: int, title: str) -> None:
        self.total_steps = max(1, total)
        self.progress.setRange(0, self.total_steps)
        self.progress.setValue(min(index, self.total_steps))
        self.step.setText(f"{index + 1}/{total} · {title}")

    def set_thumbnail(self, path: str) -> None:
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.thumb_path = path
        w = self.WIDTH - 2 * (self.SHADOW + 10)
        scaled = pm.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        if scaled.height() > 160:
            scaled = scaled.copy(0, 0, w, 160)
        self.thumb.setFixedHeight(scaled.height())
        self.thumb.setPixmap(scaled)
        self.thumb.setToolTip(path)

    def add_log(self, line: str) -> None:
        self.logs.append(line)
        if len(self.logs) > 500:
            del self.logs[:100]

    def duration(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or time.monotonic()) - self.started

    def tick(self) -> None:
        secs = int(self.duration())
        self.elapsed.setText(f"⏱ {secs // 60}:{secs % 60:02d}")
