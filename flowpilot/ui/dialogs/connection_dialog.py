"""Add / edit an anti-detect browser connection, with a Test connection button."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from flowpilot.adapters import ADAPTERS, create_adapter
from flowpilot.storage.models import Connection
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import run_async
from flowpilot.ui.widgets.clay import ClayButton, HelpIcon, muted_label, title_label

TYPE_HELP = {
    "adspower": "AdsPower → API (left menu) shows the address. Turn on the Local API first.",
    "gologin": "Keep the GoLogin app open. Profiles are read from your GoLogin account.",
    "dolphin": "Keep Dolphin Anty open. The API token is in your Dolphin profile → API.",
    "multilogin": "Keep the Multilogin X agent running. Needs a token and the folder ID.",
    "octo": "Keep Octo Browser open. The API token is in Octo → Settings.",
    "custom": "For any other browser (or your own): fill in where the start/stop/list endpoints are.",
    "local": "Uses Chrome/Chromium on this PC. Good for trying FlowPilot and building flows.",
}


class ConnectionDialog(QDialog):
    def __init__(self, app: AppContext, conn: Connection | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.conn = conn
        self.setWindowTitle("Browser connection")
        self.resize(620, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(title_label("Anti-detect browser connection", "subtitle"))
        self.help = muted_label("")
        root.addWidget(self.help)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.type_box = QComboBox()
        for key, cls in ADAPTERS.items():
            self.type_box.addItem(cls.display_name, key)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. AdsPower on this PC")
        self.base_url = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        show = ClayButton("Show", "eye", compact=True)
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(show)
        self.key_help = muted_label("")
        form.addRow("Browser type", self.type_box)
        form.addRow("Display name", self.name)
        form.addRow(self._with_help("API address", "The local API base URL of your anti-detect browser."), self.base_url)
        form.addRow(self._with_help("API key", "Stored encrypted on this PC."), key_row)
        form.addRow("", self.key_help)
        root.addLayout(form)

        root.addWidget(title_label("Extra settings", "subtitle"))
        self.extra_area = QScrollArea()
        self.extra_area.setWidgetResizable(True)
        self.extra_host = QWidget()
        self.extra_host.setObjectName("Transparent")
        self.extra_form = QFormLayout(self.extra_host)
        self.extra_area.setWidget(self.extra_host)
        root.addWidget(self.extra_area, 1)
        self.extra_inputs: dict[str, Any] = {}

        self.result = QLabel("")
        self.result.setWordWrap(True)
        root.addWidget(self.result)
        buttons = QHBoxLayout()
        self.test_btn = ClayButton("Test connection", "plug", "secondary")
        self.test_btn.clicked.connect(self.test)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = ClayButton("Save", "check", "primary")
        save.clicked.connect(self.save)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        self.type_box.currentIndexChanged.connect(self._on_type)
        if conn is not None:
            self.type_box.setCurrentIndex(max(0, self.type_box.findData(conn.type)))
            self.name.setText(conn.name)
            self.base_url.setText(conn.base_url)
            try:
                self.api_key.setText(app.repos.connections.api_key(conn))
            except Exception:
                self.api_key.setText("")
            self._on_type(keep_url=True, values=app.repos.connections.extra(conn))
        else:
            self._on_type()

    @staticmethod
    def _with_help(text: str, help_text: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(text))
        lay.addWidget(HelpIcon(help_text))
        return w

    def _on_type(self, *_: Any, keep_url: bool = False, values: dict[str, Any] | None = None) -> None:
        key = self.type_box.currentData()
        cls = ADAPTERS[key]
        self.help.setText(TYPE_HELP.get(key, ""))
        self.key_help.setText(cls.api_key_help)
        if not keep_url:
            self.base_url.setText(cls.default_base_url)
        if not self.name.text().strip() or not keep_url:
            if self.conn is None:
                self.name.setText(cls.display_name)
        self.base_url.setEnabled(key != "local")
        self.api_key.setEnabled(key != "local")
        while self.extra_form.rowCount():
            self.extra_form.removeRow(0)
        self.extra_inputs.clear()
        values = values or {}
        for f in cls.extra_fields:
            if f.choices:
                w: Any = QComboBox()
                w.addItems(f.choices)
                w.setCurrentText(str(values.get(f.key, f.default)))
            else:
                w = QLineEdit(str(values.get(f.key, f.default)))
                if f.secret:
                    w.setEchoMode(QLineEdit.EchoMode.Password)
            w.setToolTip(f.help)
            self.extra_inputs[f.key] = w
            self.extra_form.addRow(self._with_help(f.label, f.help or f.label), w)
        self.result.setText("")

    def values(self) -> tuple[Connection, str]:
        extra = {k: (w.currentText() if isinstance(w, QComboBox) else w.text()) for k, w in self.extra_inputs.items()}
        conn = self.conn or Connection()
        conn.type = self.type_box.currentData()
        conn.name = self.name.text().strip() or ADAPTERS[conn.type].display_name
        conn.base_url = self.base_url.text().strip()
        conn.extra_json = json.dumps(extra)
        return conn, self.api_key.text().strip()

    def test(self) -> None:
        conn, key = self.values()
        self.result.setProperty("role", "muted")
        self.result.setText("Testing…")
        self.result.style().polish(self.result)
        self.test_btn.setEnabled(False)

        async def go() -> Any:
            adapter = create_adapter(conn.type, conn.base_url, key, json.loads(conn.extra_json))
            try:
                return await adapter.test_connection()
            finally:
                await adapter.aclose()

        def done(result: Any) -> None:
            self.test_btn.setEnabled(True)
            self.result.setProperty("role", "success" if result.ok else "error")
            self.result.setText(("✔ " if result.ok else "✖ ") + result.message)
            self.result.style().polish(self.result)

        def failed(exc: BaseException) -> None:
            self.test_btn.setEnabled(True)
            self.result.setProperty("role", "error")
            self.result.setText(f"✖ {exc}")
            self.result.style().polish(self.result)

        run_async(go(), done, failed)

    def save(self) -> None:
        conn, key = self.values()
        self.conn = self.app.repos.connections.save(conn, api_key=key)
        self.app.bridge.data_changed.emit("connections")
        self.accept()
