"""Main window: clay sidebar navigation, pages, tray icon and app-level prompts."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from flowpilot import __version__
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, run_async
from flowpilot.ui.dialogs.setup_wizard import SetupWizard
from flowpilot.ui.icons import pixmap
from flowpilot.ui.pages.accounts_page import AccountsPage
from flowpilot.ui.pages.base import Page
from flowpilot.ui.pages.dashboard_page import DashboardPage
from flowpilot.ui.pages.flows_page import FlowsPage
from flowpilot.ui.pages.runs_page import RunsPage
from flowpilot.ui.pages.scheduler_page import SchedulerPage
from flowpilot.ui.pages.settings_page import SettingsPage
from flowpilot.ui.theme.palette import ThemeManager, pal
from flowpilot.ui.theme.shadow import paint_clay
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, IconButton, muted_label


class NavButton(ClayButton):
    """Sidebar entry; the active page looks pressed into the clay."""

    def __init__(self, text: str, icon: str) -> None:
        super().__init__(text, icon, "neutral")
        self.setCheckable(True)
        self.setMinimumWidth(190)

    def colors(self, p: Any) -> tuple[str | None, str]:
        if self.isChecked():
            return p.card, p.primary
        return (p.card if self._hover > 0.05 else None), p.text

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        # left-aligned icon + text; the active page is drawn pressed into the clay
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = pal()
        rect = QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)
        if self.isChecked():
            paint_clay(p, rect, 16, palette, fill=palette.bg, depth=0.5, pressed=True)
        elif self._hover > 0.05:
            c = QColor(palette.primary)
            c.setAlpha(int(22 * self._hover))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(rect, 16, 16)
        color = palette.primary if self.isChecked() else palette.text
        x = rect.left() + 16
        if self.icon_name:
            p.drawPixmap(QPoint(int(x), int(rect.center().y() - 10)), pixmap(self.icon_name, color, 20))
        f = self.font()
        f.setWeight(f.Weight.DemiBold if self.isChecked() else f.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(QRectF(x + 32, rect.top(), rect.width() - 50, rect.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#7C6CF2"))
    p.drawRoundedRect(4, 4, 56, 56, 18, 18)
    p.drawPixmap(16, 16, pixmap("zap", "#FFFFFF", 32))
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    PAGES: list[type[Page]] = [DashboardPage, AccountsPage, FlowsPage, RunsPage, SchedulerPage, SettingsPage]

    def __init__(self, app: AppContext) -> None:
        super().__init__()
        self.app = app
        self._really_quit = False
        self.setWindowTitle("FlowPilot")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(QSize(1100, 700))
        root = QWidget()
        root.setObjectName("PageRoot")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(10, 10, 0, 10)
        lay.setSpacing(0)

        side = ClayCard(radius=26, padding=8)
        side.setFixedWidth(250)
        logo_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        name = QLabel("FlowPilot")
        f = name.font()
        f.setPointSizeF(f.pointSizeF() + 6)
        f.setBold(True)
        name.setFont(f)
        logo_row.addWidget(logo)
        logo_row.addWidget(name)
        logo_row.addStretch(1)
        side.body.addLayout(logo_row)
        side.body.addSpacing(10)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.stack = QStackedWidget()
        self.pages: list[Page] = []
        for i, page_cls in enumerate(self.PAGES):
            page = page_cls(app)
            self.pages.append(page)
            self.stack.addWidget(page)
            btn = NavButton(page_cls.title, page_cls.icon)
            btn.setShortcut(f"Ctrl+{i + 1}")
            btn.setToolTip(f"{page_cls.title} (Ctrl+{i + 1})")
            self.nav.addButton(btn, i)
            side.body.addWidget(btn)
        side.body.addStretch(1)
        bottom = QHBoxLayout()
        self.theme_btn = IconButton("moon", "Switch light / dark theme", size=32)
        self.theme_btn.clicked.connect(self.toggle_theme)
        help_btn = IconButton("help", "Run the setup wizard again", size=32)
        help_btn.clicked.connect(self.show_wizard)
        bottom.addWidget(self.theme_btn)
        bottom.addWidget(help_btn)
        bottom.addStretch(1)
        side.body.addLayout(bottom)
        side.body.addWidget(muted_label(f"v{__version__}"))
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.nav.idClicked.connect(self.show_page)
        self.nav.button(0).setChecked(True)
        ThemeManager.instance().changed.connect(lambda *_: self._update_theme_icon())
        self._update_theme_icon()
        self._setup_tray()
        app.services.notifier.desktop = self._desktop_notify

    # ------------------------------------------------------------------ navigation
    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        btn = self.nav.button(index)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        page = self.pages[index]
        page.on_show()

    def page(self, cls: type[Page]) -> Page:
        return next(p for p in self.pages if isinstance(p, cls))

    def toggle_theme(self) -> None:
        s = self.app.settings
        current = ThemeManager.instance().palette.name
        s.theme = "light" if current == "dark" else "dark"  # type: ignore[assignment]
        ThemeManager.instance().apply(s.theme, s.font_size)
        self.app.save_settings()

    def _update_theme_icon(self) -> None:
        self.theme_btn.icon_name = "sun" if pal().name == "dark" else "moon"
        self.theme_btn.update()

    # ------------------------------------------------------------------ prompts
    def first_launch(self) -> None:
        """Setup wizard on first launch; offer to resume interrupted batches."""
        if not self.app.settings.setup_completed and not self.app.repos.connections.list():
            self.show_wizard()
        interrupted = self.app.services.engine.interrupted_runs()
        for run in interrupted:
            items = self.app.repos.runs.items(run.id)
            left = sum(1 for it in items if it.status not in ("done", "failed", "stopped", "skipped"))
            if left == 0:
                self.app.services.engine.discard_interrupted(run.id)
                continue
            if confirm(self, "Continue unfinished run?", f"The run “{run.flow_name}” (#{run.id}) was interrupted "
                       f"when FlowPilot closed. {left} account(s) did not finish.\n\nContinue it now?", "Continue"):
                run_async(self.app.services.engine.resume(run.id), parent=self, error_title="Could not resume")
                self.show_page(0)
            else:
                self.app.services.engine.discard_interrupted(run.id)

    def show_wizard(self) -> None:
        wiz = SetupWizard(self.app, self)
        if wiz.exec():
            self.show_page(self.PAGES.index(FlowsPage))

    # ------------------------------------------------------------------ tray
    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        show = QAction("Open FlowPilot", self)
        show.triggered.connect(self._restore)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.setToolTip("FlowPilot")
        tray.activated.connect(lambda reason: self._restore() if reason == QSystemTrayIcon.ActivationReason.Trigger
                               else None)
        tray.show()
        self.tray = tray

    def _desktop_notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, message, app_icon(), 8000)

    def _restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._really_quit = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        s = self.app.settings
        if s.tray_mode and self.tray is not None and not self._really_quit:
            event.ignore()
            self.hide()
            self.tray.showMessage("FlowPilot is still running", "Schedules keep running. Right-click the tray icon "
                                  "to quit.", app_icon(), 5000)
            return
        if self.app.services.engine.is_busy() and not confirm(
                self, "Runs in progress", "Browsers are still running. Quit anyway? They will be closed, and you can "
                "continue the run next time.", "Quit", danger=True):
            event.ignore()
            self._really_quit = False
            return
        flows = self.page(FlowsPage)
        if isinstance(flows, FlowsPage):
            flows.save()
        event.accept()
        QApplication.instance().quit()  # type: ignore[union-attr]
