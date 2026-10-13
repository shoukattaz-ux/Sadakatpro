"""Update banner and dialog (GitHub Releases)."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QTextBrowser, QVBoxLayout, QWidget

from flowpilot import __version__
from flowpilot.services.updater import UpdateError, UpdateInfo, can_self_update, download_update, launch_installer
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import run_async
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, muted_label, title_label


class UpdateBanner(ClayCard):
    """'FlowPilot x.y is available' strip shown at the top of the window."""

    update_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=20, padding=6, fill="selection", depth=0.6)
        row = QHBoxLayout()
        self.text = QLabel("")
        f = self.text.font()
        f.setBold(True)
        self.text.setFont(f)
        row.addWidget(self.text, 1)
        update = ClayButton("Update now", "download", "primary", compact=True)
        update.clicked.connect(self.update_clicked.emit)
        later = ClayButton("Later", compact=True)
        later.clicked.connect(self.hide)
        skip = ClayButton("Skip this version", None, "ghost", compact=True)
        skip.clicked.connect(self.skip_clicked.emit)
        for b in (update, later, skip):
            row.addWidget(b)
        self.body.addLayout(row)
        self.hide()

    def show_for(self, info: UpdateInfo) -> None:
        self.text.setText(f"✨ FlowPilot {info.version} is available (you have {__version__}).")
        self.show()


class UpdateDialog(QDialog):
    def __init__(self, app: AppContext, info: UpdateInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.info = info
        self.setWindowTitle("Update FlowPilot")
        self.resize(600, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(title_label(f"FlowPilot {info.version}", "subtitle"))
        root.addWidget(muted_label(f"Released {info.published or 'recently'} · you have {__version__}. Your flows, "
                                   "accounts and settings are kept."))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or f"Improvements and fixes — [see what's new]({info.page_url}).")
        root.addWidget(notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        root.addWidget(self.progress)
        self.status = muted_label("")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        page = ClayButton("Release page", "globe", compact=True)
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info.page_url)))
        buttons.addWidget(page)
        buttons.addStretch(1)
        close = ClayButton("Later")
        close.clicked.connect(self.reject)
        self.install_btn = ClayButton("Download and install" if can_self_update() else "Download installer",
                                      "download", "primary")
        self.install_btn.clicked.connect(self.install)
        buttons.addWidget(close)
        buttons.addWidget(self.install_btn)
        root.addLayout(buttons)
        if not info.installer_url:
            self.install_btn.setEnabled(False)
            self.status.setText("The installer for this version is still being built. Try again in a few minutes.")

    def install(self) -> None:
        if not can_self_update():
            # running from source or not on Windows: just open the download
            QDesktopServices.openUrl(QUrl(self.info.installer_url or self.info.page_url))
            self.status.setText("Opened the download in your browser. Running from source? Use 'git pull' instead.")
            return
        if self.app.services.engine.is_busy():
            self.status.setText("Stop all runs first — the update restarts FlowPilot.")
            return
        self.install_btn.setEnabled(False)
        self.progress.show()
        self.status.setText("Downloading…")

        def progress(done: int, total: int) -> None:
            if total:
                self.progress.setValue(int(done * 100 / total))
                self.status.setText(f"Downloading… {done / 1048576:.1f} of {total / 1048576:.1f} MB")

        def done(path: object) -> None:
            self.status.setText("Installing… FlowPilot will restart by itself.")
            try:
                launch_installer(path)  # type: ignore[arg-type]
            except OSError as exc:
                self.status.setText(f"Could not start the installer: {exc}")
                self.install_btn.setEnabled(True)
                return
            window = self.parent().window() if self.parent() else None
            if window is not None and hasattr(window, "quit_app"):
                window._really_quit = True  # noqa: SLF001
            QApplication.quit()

        def failed(exc: BaseException) -> None:
            self.install_btn.setEnabled(True)
            self.status.setText("✖ " + (str(exc) if isinstance(exc, UpdateError) else f"Update failed: {exc}"))

        run_async(download_update(self.info, progress=progress), done, failed)
