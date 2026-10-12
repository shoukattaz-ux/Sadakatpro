"""Common page layout: big title, subtitle, actions on the right, then content."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from flowpilot.ui.app_context import AppContext
from flowpilot.ui.widgets.clay import muted_label, title_label


class Page(QWidget):
    title = "Page"
    subtitle = ""
    icon = "circle"

    def __init__(self, app: AppContext) -> None:
        super().__init__()
        self.app = app
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(6)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(title_label(self.title))
        if self.subtitle:
            titles.addWidget(muted_label(self.subtitle))
        header.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(4)
        header.addLayout(self.actions)
        root.addLayout(header)
        self.content = QVBoxLayout()
        self.content.setSpacing(4)
        root.addLayout(self.content, 1)
        app.bridge.data_changed.connect(self.on_data_changed)

    def on_data_changed(self, what: str) -> None:
        """Override to refresh when other pages change data."""

    def on_show(self) -> None:
        """Called each time the page becomes visible."""
