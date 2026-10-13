"""Common page layout: big title, subtitle, actions on the right, then content."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget

from flowpilot.ui.app_context import AppContext
from flowpilot.ui.widgets.clay import muted_label, title_label


class Page(QWidget):
    title = "Page"
    subtitle = ""
    icon = "circle"
    #: below this page width, buttons registered with :meth:`shrinkable` show only their icon
    ICONS_ONLY_BELOW = 0

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
        self._labelled: list[tuple[QAbstractButton, str]] = []
        self._narrow: bool | None = None

    # -- small screens ---------------------------------------------------------------
    def shrinkable(self, *buttons: QAbstractButton) -> None:
        """Buttons that drop their text when the page is narrow (the tooltip keeps the name)."""
        for b in buttons:
            self._labelled.append((b, b.text()))
            if not b.toolTip():
                b.setToolTip(b.text())

    def set_label(self, button: QAbstractButton, text: str) -> None:
        """Change a shrinkable button's text (respects the narrow mode)."""
        self._labelled = [(b, text if b is button else t) for b, t in self._labelled]
        button.setText("" if self._narrow else text)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_layout()

    def _fit_layout(self) -> None:
        narrow = self.width() < self.ICONS_ONLY_BELOW
        if narrow != self._narrow:
            self._narrow = narrow
            for b, text in self._labelled:
                b.setText("" if narrow else text)

    def on_data_changed(self, what: str) -> None:
        """Override to refresh when other pages change data."""

    def on_show(self) -> None:
        """Called each time the page becomes visible."""
