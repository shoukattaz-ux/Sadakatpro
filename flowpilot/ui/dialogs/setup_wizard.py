"""First-launch wizard: add a connection → test → sync profiles → record a first flow."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget, QWizard, QWizardPage

from flowpilot.adapters import ADAPTERS, create_adapter
from flowpilot.services.profile_sync import sync_profiles
from flowpilot.storage.models import Connection
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import run_async
from flowpilot.ui.dialogs.connection_dialog import TYPE_HELP
from flowpilot.ui.widgets.clay import ClayButton, muted_label


class _Welcome(QWizardPage):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle("Welcome to FlowPilot 👋")
        self.setSubTitle("Automate repetitive web tasks on many browser profiles — without writing code.")
        lay = QVBoxLayout(self)
        for line in ("1. Connect your anti-detect browser (AdsPower, GoLogin, Dolphin, Multilogin, Octo or your own).",
                     "2. Load your browser profiles.",
                     "3. Record what you do in the browser — FlowPilot turns it into steps.",
                     "4. Run it on as many profiles as you like, with human-like behaviour."):
            lay.addWidget(muted_label(line))
        lay.addSpacing(16)
        lay.addWidget(muted_label("Moving from another PC? Import the file you made there with "
                                  "Settings → Data → Export all data, and everything comes back."))
        imp = ClayButton("Import from another PC…", "upload", "secondary")
        imp.clicked.connect(self._import)
        lay.addWidget(imp)
        lay.addStretch(1)

    def _import(self) -> None:
        from flowpilot.ui.dialogs.transfer_dialog import ImportDialog

        dialog = ImportDialog(self.wiz.app, self.wiz)
        if dialog.exec() and dialog.imported:
            self.wiz.imported = True
            self.wiz.accept()


class _ConnectionPage(QWizardPage):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle("Connect your anti-detect browser")
        self.setSubTitle("Open your anti-detect browser and turn on its Local API, then fill this in.")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.type_box = QComboBox()
        for key, cls in ADAPTERS.items():
            self.type_box.addItem(cls.display_name, key)
        self.url = QLineEdit()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.help = muted_label("")
        form.addRow("Browser", self.type_box)
        form.addRow("API address", self.url)
        form.addRow("API key", self.key)
        lay.addLayout(form)
        lay.addWidget(self.help)
        self.test_btn = ClayButton("Test connection", "plug", "secondary")
        self.test_btn.clicked.connect(self.test)
        lay.addWidget(self.test_btn)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        lay.addStretch(1)
        self.ok = False
        self.detected: dict[str, Any] = {}
        self.type_box.currentIndexChanged.connect(self._on_type)
        self._on_type()

    def _on_type(self, *_: Any) -> None:
        key = self.type_box.currentData()
        cls = ADAPTERS[key]
        self.url.setText(cls.default_base_url)
        self.url.setEnabled(key != "local")
        self.key.setEnabled(key != "local")
        self.help.setText(TYPE_HELP.get(key, "") + ("\nFor the Custom type, finish the setup in Settings → "
                                                    "Connections." if key == "custom" else ""))
        self.ok = False
        self.detected = {}
        self.result.setText("")
        self.completeChanged.emit()

    def test(self) -> None:
        self.result.setText("Testing…")
        key = self.type_box.currentData()

        async def go() -> Any:
            adapter = create_adapter(key, self.url.text().strip(), self.key.text().strip(), {})
            try:
                result = await adapter.test_connection()
                if result.ok and hasattr(adapter, "detected_settings"):
                    self.detected = adapter.detected_settings()
                return result
            finally:
                await adapter.aclose()

        def done(res: Any) -> None:
            self.ok = res.ok
            self.result.setProperty("role", "success" if res.ok else "error")
            self.result.setText(("✔ " if res.ok else "✖ ") + res.message)
            self.result.style().polish(self.result)
            self.completeChanged.emit()

        run_async(go(), done, lambda exc: done(type("R", (), {"ok": False, "message": str(exc)})()))

    def isComplete(self) -> bool:  # noqa: N802
        return self.ok

    def validatePage(self) -> bool:  # noqa: N802
        key = self.type_box.currentData()
        conn = Connection(type=key, name=ADAPTERS[key].display_name, base_url=self.url.text().strip(),
                          extra_json=json.dumps(self.detected if key == "auto" else {}))
        self.wiz.connection = self.wiz.app.repos.connections.save(conn, api_key=self.key.text().strip())
        self.wiz.app.bridge.data_changed.emit("connections")
        return True


class _SyncPage(QWizardPage):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle("Load your browser profiles")
        self.setSubTitle("FlowPilot reads the list of profiles from your anti-detect browser. "
                         "Each profile becomes an 'account'.")
        lay = QVBoxLayout(self)
        btn = ClayButton("Sync profiles now", "refresh", "primary")
        btn.clicked.connect(self.sync)
        lay.addWidget(btn)
        self.result = muted_label("")
        lay.addWidget(self.result)
        lay.addStretch(1)

    def sync(self) -> None:
        conn = self.wiz.connection
        if conn is None:
            return
        self.result.setText("Loading…")
        adapter = self.wiz.app.services.adapters.get(conn)

        def done(res: Any) -> None:
            self.result.setText(f"✔ {res.total} profile(s) found, {res.created} added.")
            self.wiz.app.bridge.data_changed.emit("accounts")
            self.wiz.app.bridge.data_changed.emit("groups")

        run_async(sync_profiles(self.wiz.app.repos, adapter, conn), done,
                  lambda exc: self.result.setText(f"✖ {exc}"))


class _RecordPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Record your first flow")
        self.setSubTitle("Almost done!")
        lay = QVBoxLayout(self)
        for line in ("On the Flows page:",
                     "• choose a Test profile at the top,",
                     "• press Record and simply use the website in the browser that opens,",
                     "• press Stop — your actions are now steps you can edit.",
                     "",
                     "Tip: for passwords, FlowPilot automatically uses {{account.password}} so each account "
                     "types its own password. Add it under Accounts → Edit / variables."):
            lay.addWidget(muted_label(line))
        lay.addStretch(1)


class SetupWizard(QWizard):
    def __init__(self, app: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.connection: Connection | None = None
        self.imported = False
        self.setWindowTitle("FlowPilot setup")
        self.resize(640, 520)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(_Welcome(self))
        self.addPage(_ConnectionPage(self))
        self.addPage(_SyncPage(self))
        self.addPage(_RecordPage())
        self.setButtonText(QWizard.WizardButton.FinishButton, "Go to Flows")
        self.finished.connect(self._done)

    def _done(self, _result: int) -> None:
        self.app.settings.setup_completed = True
        self.app.save_settings()
