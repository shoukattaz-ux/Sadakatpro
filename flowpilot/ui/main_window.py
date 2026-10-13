"""Main window: clay sidebar navigation, pages, tray icon and app-level prompts."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from flowpilot import __version__
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, run_async
from flowpilot.ui.dialogs.setup_wizard import SetupWizard
from flowpilot.ui.dialogs.update_dialog import UpdateBanner, UpdateDialog
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
        self.compact = False

    def set_compact(self, compact: bool) -> None:
        """Icon only (small screens); the page name stays in the tooltip."""
        self.compact = compact
        self.setMinimumWidth(48 if compact else 190)
        self.updateGeometry()
        self.update()

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
        if self.compact:
            if self.icon_name:
                p.drawPixmap(QPoint(int(rect.center().x() - 10), int(rect.center().y() - 10)),
                             pixmap(self.icon_name, color, 20))
            return
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
    """The FlowPilot logo (resources/icons/flowpilot.svg) at every size Qt asks for."""
    from flowpilot.config import resource_path

    svg = resource_path("icons", "flowpilot.svg")
    icon = QIcon()
    if svg.exists():
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        for size in (16, 24, 32, 48, 64, 128, 256):
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(p, QRectF(0, 0, size, size))
            p.end()
            icon.addPixmap(pm)
        return icon
    pm = QPixmap(64, 64)  # fallback: simple tile
    pm.fill(QColor("#7C6CF2"))
    icon.addPixmap(pm)
    return icon


class MainWindow(QMainWindow):
    PAGES: list[type[Page]] = [DashboardPage, AccountsPage, FlowsPage, RunsPage, SchedulerPage, SettingsPage]

    def __init__(self, app: AppContext) -> None:
        super().__init__()
        self.app = app
        self._really_quit = False
        self.setWindowTitle("FlowPilot")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(QSize(900, 560))
        self._compact: bool | None = None
        root = QWidget()
        root.setObjectName("PageRoot")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(10, 10, 0, 10)
        lay.setSpacing(0)

        side = ClayCard(radius=26, padding=8)
        side.setFixedWidth(250)
        self.side = side
        logo_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        name = self.app_name = QLabel("FlowPilot")
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
        bottom = self.side_bottom = QHBoxLayout()
        self.theme_btn = IconButton("moon", "Switch light / dark theme", size=32)
        self.theme_btn.clicked.connect(self.toggle_theme)
        help_btn = IconButton("help", "Run the setup wizard again", size=32)
        help_btn.clicked.connect(self.show_wizard)
        bottom.addWidget(self.theme_btn)
        bottom.addWidget(help_btn)
        bottom.addStretch(1)
        side.body.addLayout(bottom)
        self.version_label = muted_label(f"v{__version__}")
        side.body.addWidget(self.version_label)
        lay.addWidget(side)
        right = QWidget()
        right.setObjectName("Transparent")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        self.update_banner = UpdateBanner()
        self.update_banner.update_clicked.connect(self._open_update)
        self.update_banner.skip_clicked.connect(self._skip_update)
        right_lay.addWidget(self.update_banner)
        right_lay.addWidget(self.stack, 1)
        lay.addWidget(right, 1)
        self._update_info = None
        self.nav.idClicked.connect(self.show_page)
        self.nav.button(0).setChecked(True)
        ThemeManager.instance().changed.connect(lambda *_: self._update_theme_icon())
        self._update_theme_icon()
        self._setup_tray()
        app.services.notifier.desktop = self._desktop_notify
        self.fit_to_screen()

    # ------------------------------------------------------------------ size
    COMPACT_BELOW = 1280

    def fit_to_screen(self) -> None:
        """Open at a size that fits the screen (laptops with display scaling are often ~1100 px wide)."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        width, height = 1440, 900
        if avail is not None and avail.width() > 0:
            width, height = min(width, avail.width() - 16), min(height, avail.height() - 40)
        self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))
        self._apply_compact()

    def show_fitted(self) -> None:
        """Show the window; maximized when the screen is small."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None and screen.availableGeometry().width() < 1500:
            self.showMaximized()
        else:
            self.show()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_compact()

    def _apply_compact(self) -> None:
        compact = self.width() < self.COMPACT_BELOW
        if compact == self._compact:
            return
        self._compact = compact
        self.side.setFixedWidth(76 if compact else 250)
        self.app_name.setVisible(not compact)
        self.version_label.setVisible(not compact)
        self.side_bottom.setDirection(QBoxLayout.Direction.TopToBottom if compact
                                      else QBoxLayout.Direction.LeftToRight)
        for btn in self.nav.buttons():
            btn.set_compact(compact)  # type: ignore[attr-defined]

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
        """Setup wizard on first launch; offer to resume interrupted batches; check for updates."""
        if self.app.settings.auto_check_updates:
            QTimer.singleShot(6000, self.check_updates)
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

    # ------------------------------------------------------------------ updates
    def check_updates(self, manual: bool = False, on_result: Any = None) -> None:
        """Ask GitHub for a newer release; shows the banner (or a message when ``manual``)."""
        from flowpilot.services.updater import check_for_update

        def done(info: Any) -> None:
            self._update_info = info
            skipped = self.app.settings.skipped_update_version
            if info is not None and (manual or info.version != skipped):
                self.update_banner.show_for(info)
                if manual:
                    self._open_update()
            if on_result:
                on_result(info, None)

        def failed(exc: BaseException) -> None:
            if on_result:
                on_result(None, exc)

        run_async(check_for_update(), done, failed)

    def _open_update(self) -> None:
        if self._update_info is not None:
            UpdateDialog(self.app, self._update_info, self).exec()

    def _skip_update(self) -> None:
        if self._update_info is not None:
            self.app.settings.skipped_update_version = self._update_info.version
            self.app.save_settings()
        self.update_banner.hide()

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
