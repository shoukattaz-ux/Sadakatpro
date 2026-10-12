"""Settings: connections, defaults, behaviour, appearance, data, notifications, about."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from flowpilot import __version__
from flowpilot.adapters import ADAPTERS, create_adapter
from flowpilot.config import HUMANIZE_PRESETS, apply_humanize_preset, location_pointer
from flowpilot.services.backup import create_backup, restore_backup
from flowpilot.services.licensing import check_license
from flowpilot.services.reporting import apply_retention
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, run_async, show_error
from flowpilot.ui.dialogs.connection_dialog import ConnectionDialog
from flowpilot.ui.pages.base import Page
from flowpilot.ui.theme.palette import ThemeManager
from flowpilot.ui.widgets.clay import (
    ClayButton,
    ClayCard,
    ClaySpinBox,
    ClayToggle,
    EmptyState,
    HelpIcon,
    muted_label,
    title_label,
)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    widget.setObjectName("Transparent")
    area.setWidget(widget)
    return area


class _Form(QWidget):
    """A card with labelled rows bound to settings values (saved on every change)."""

    def __init__(self, app: AppContext) -> None:
        super().__init__()
        self.app = app
        self.setObjectName("Transparent")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 8, 0, 8)
        self.card: ClayCard | None = None
        self.form: QFormLayout | None = None

    def section(self, title: str, subtitle: str = "") -> None:
        self.card = ClayCard()
        self.card.body.addWidget(title_label(title, "subtitle"))
        if subtitle:
            self.card.body.addWidget(muted_label(subtitle))
        self.form = QFormLayout()
        self.form.setVerticalSpacing(8)
        self.form.setHorizontalSpacing(18)
        self.card.body.addLayout(self.form)
        self.lay.addWidget(self.card)

    def row(self, label: str, widget: QWidget, help_text: str = "") -> QWidget:
        assert self.form is not None
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(label))
        if help_text:
            h.addWidget(HelpIcon(help_text))
        h.addStretch(1)
        self.form.addRow(holder, widget)
        return widget

    def toggle(self, label: str, getter: Callable[[], bool], setter: Callable[[bool], None], help_text: str = "") -> ClayToggle:
        t = ClayToggle(getter())
        t.toggled.connect(lambda v: (setter(v), self.app.save_settings()))
        self.row(label, t, help_text)
        return t

    def number(self, label: str, getter: Callable[[], float], setter: Callable[[float], None], lo: float, hi: float,
               step: float = 1, decimals: int = 0, suffix: str = "", help_text: str = "") -> ClaySpinBox:
        s = ClaySpinBox(getter(), lo, hi, step, decimals, suffix)
        s.valueChanged.connect(lambda v: (setter(int(v) if decimals == 0 else v), self.app.save_settings()))
        self.row(label, s, help_text)
        return s

    def choice(self, label: str, options: list[tuple[str, str]], getter: Callable[[], str],
               setter: Callable[[str], None], help_text: str = "") -> QComboBox:
        box = QComboBox()
        for text, value in options:
            box.addItem(text, value)
        box.setCurrentIndex(max(0, box.findData(getter())))
        box.currentIndexChanged.connect(lambda _: (setter(box.currentData()), self.app.save_settings()))
        self.row(label, box, help_text)
        return box

    def finish(self) -> None:
        self.lay.addStretch(1)


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Connections to your anti-detect browsers and how FlowPilot behaves."
    icon = "settings"

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        self.tabs = QTabWidget()
        self.content.addWidget(self.tabs)
        self.tabs.addTab(self._connections_tab(), "Connections")
        self.tabs.addTab(_scroll(self._defaults_tab()), "Defaults")
        self.tabs.addTab(_scroll(self._behavior_tab()), "Behavior")
        self.tabs.addTab(_scroll(self._appearance_tab()), "Appearance")
        self.tabs.addTab(_scroll(self._data_tab()), "Data")
        self.tabs.addTab(_scroll(self._notifications_tab()), "Notifications")
        self.tabs.addTab(_scroll(self._about_tab()), "About")

    # ------------------------------------------------------------------ connections
    def _connections_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Transparent")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        top.addWidget(muted_label("Add each anti-detect browser you use. You can add several."), 1)
        add = ClayButton("Add connection", "plus", "primary")
        add.clicked.connect(lambda: self._edit_connection(None))
        top.addWidget(add)
        lay.addLayout(top)
        self.conn_host = QWidget()
        self.conn_host.setObjectName("Transparent")
        self.conn_list = QVBoxLayout(self.conn_host)
        self.conn_list.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(self.conn_host), 1)
        self._refresh_connections()
        return w

    def _refresh_connections(self) -> None:
        while self.conn_list.count():
            item = self.conn_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        conns = self.app.repos.connections.list()
        if not conns:
            empty = EmptyState("plug", "No connections yet",
                               "Add your anti-detect browser (AdsPower, GoLogin, Dolphin, Multilogin, Octo or your own) "
                               "so FlowPilot can open its profiles.", "Add connection")
            assert empty.button is not None
            empty.button.clicked.connect(lambda: self._edit_connection(None))
            self.conn_list.addWidget(empty)
            self.conn_list.addStretch(1)
            return
        for conn in conns:
            card = ClayCard(hoverable=True)
            row = QHBoxLayout()
            text = QVBoxLayout()
            text.addWidget(title_label(conn.name, "subtitle"))
            cls = ADAPTERS.get(conn.type)
            text.addWidget(muted_label(f"{cls.display_name if cls else conn.type} · {conn.base_url or 'local'}"))
            status = QLabel("")
            status.setWordWrap(True)
            text.addWidget(status)
            row.addLayout(text, 1)
            test = ClayButton("Test", "plug", "secondary", compact=True)
            edit = ClayButton("Edit", "edit", compact=True)
            delete = ClayButton("", "trash", "danger", compact=True, tooltip="Delete connection")
            test.clicked.connect(lambda _=False, c=conn, s=status: self._test(c, s))
            edit.clicked.connect(lambda _=False, c=conn: self._edit_connection(c))
            delete.clicked.connect(lambda _=False, c=conn: self._delete(c))
            for b in (test, edit, delete):
                row.addWidget(b)
            card.body.addLayout(row)
            self.conn_list.addWidget(card)
        self.conn_list.addStretch(1)

    def _edit_connection(self, conn: Any) -> None:
        if ConnectionDialog(self.app, conn, self).exec():
            self._refresh_connections()

    def _delete(self, conn: Any) -> None:
        if confirm(self, "Delete connection", f"Delete “{conn.name}”? Accounts using it will need a new connection.",
                   "Delete", danger=True):
            self.app.repos.connections.delete(conn.id)
            self.app.bridge.data_changed.emit("connections")
            self._refresh_connections()

    def _test(self, conn: Any, label: QLabel) -> None:
        label.setProperty("role", "muted")
        label.setText("Testing…")
        label.style().polish(label)
        repos = self.app.repos

        async def go() -> Any:
            adapter = create_adapter(conn.type, conn.base_url, repos.connections.api_key(conn), repos.connections.extra(conn))
            try:
                return await adapter.test_connection()
            finally:
                await adapter.aclose()

        def done(result: Any) -> None:
            label.setProperty("role", "success" if result.ok else "error")
            label.setText(("✔ " if result.ok else "✖ ") + result.message)
            label.style().polish(label)

        run_async(go(), done, lambda exc: done(type("R", (), {"ok": False, "message": str(exc)})()))

    def on_data_changed(self, what: str) -> None:
        if what == "connections":
            self._refresh_connections()

    # ------------------------------------------------------------------ defaults
    def _defaults_tab(self) -> QWidget:
        s = self.app.settings
        f = _Form(self.app)
        f.section("Steps", "Used when a step does not set its own value.")
        f.number("Timeout", lambda: s.step_timeout_s, lambda v: setattr(s, "step_timeout_s", v), 1, 600, 1, 0, " s",
                 "How long a step waits for an element before it fails.")
        f.number("Retries", lambda: s.step_retries, lambda v: setattr(s, "step_retries", int(v)), 0, 20,
                 help_text="How many extra tries a failing step gets.")
        f.number("Seconds between retries", lambda: s.step_retry_delay_s, lambda v: setattr(s, "step_retry_delay_s", v),
                 0, 120, 0.5, 1, " s")
        f.section("When something fails")
        f.toggle("Take a screenshot", lambda: s.screenshot_on_error, lambda v: setattr(s, "screenshot_on_error", v))
        f.toggle("Save the page HTML", lambda: s.html_on_error, lambda v: setattr(s, "html_on_error", v),
                 "Helps to understand what the page looked like.")
        f.toggle("Record a Playwright trace", lambda: s.trace_on_error, lambda v: setattr(s, "trace_on_error", v),
                 "A detailed recording for experts (bigger files, a bit slower).")

        f.section("Human-like behaviour", "Makes mouse, typing and scrolling look natural.")
        h = s.humanize
        preset = QComboBox()
        for name in ["fast", "normal", "careful", "custom"]:
            preset.addItem(name.title(), name)
        preset.setCurrentIndex(max(0, preset.findData(h.preset)))
        f.row("Preset", preset, "Fast = quick but less natural. Careful = slow and very natural.")
        controls: list[tuple[ClaySpinBox, str]] = []

        def setter(attr: str) -> Callable[[float], None]:
            def apply(v: float) -> None:
                setattr(s.humanize, attr, v)
                if s.humanize.preset != "custom":
                    s.humanize.preset = "custom"
                    preset.blockSignals(True)
                    preset.setCurrentIndex(preset.findData("custom"))
                    preset.blockSignals(False)
            return apply

        f.toggle("Curved mouse movement", lambda: h.mouse_enabled, lambda v: setattr(s.humanize, "mouse_enabled", v),
                 "Moves the mouse along natural curves and clicks a random spot inside the element.")
        for label, attr, lo, hi, step, dec, suf, tip in [
            ("Mouse speed", "mouse_speed", 0.2, 5, 0.1, 1, "×", "1.0 = normal speed"),
            ("Overshoot chance", "overshoot_chance", 0, 1, 0.05, 2, "", "How often the mouse slightly overshoots"),
            ("Typing: fastest key", "typing_min_ms", 0, 2000, 10, 0, " ms", ""),
            ("Typing: slowest key", "typing_max_ms", 0, 3000, 10, 0, " ms", ""),
            ("Pause between words (chance)", "word_pause_chance", 0, 1, 0.05, 2, "", ""),
            ("Typo + correction (chance)", "typo_chance", 0, 0.3, 0.01, 2, "", "Off (0) by default"),
            ("Wait between steps: min", "step_delay_min", 0, 60, 0.1, 1, " s", ""),
            ("Wait between steps: max", "step_delay_max", 0, 120, 0.1, 1, " s", ""),
            ("Scroll back up (chance)", "scroll_back_chance", 0, 1, 0.05, 2, "", ""),
        ]:
            spin = f.number(label, lambda a=attr: getattr(s.humanize, a), setter(attr), lo, hi, step, dec, suf, tip)
            controls.append((spin, attr))
        f.toggle("Small mouse movements while waiting", lambda: h.idle_jitter,
                 lambda v: setattr(s.humanize, "idle_jitter", v))

        def on_preset(_: int) -> None:
            name = preset.currentData()
            if name in HUMANIZE_PRESETS:
                s.humanize = apply_humanize_preset(s.humanize, name)
                for spin, attr in controls:
                    spin.blockSignals(True)
                    spin.box.blockSignals(True)
                    spin.setValue(getattr(s.humanize, attr))
                    spin.box.blockSignals(False)
                    spin.blockSignals(False)
            else:
                s.humanize.preset = "custom"
            self.app.save_settings()

        preset.currentIndexChanged.connect(on_preset)
        f.finish()
        return f

    # ------------------------------------------------------------------ behaviour
    def _behavior_tab(self) -> QWidget:
        s = self.app.settings
        f = _Form(self.app)
        f.section("Browser")
        f.toggle("Switch to new tabs automatically", lambda: s.auto_switch_new_tab,
                 lambda v: setattr(s, "auto_switch_new_tab", v), "When a click opens a popup/new tab, continue there.")
        f.choice("Pop-up messages (alert/confirm)", [("Accept (press OK)", "accept"), ("Dismiss (press Cancel)", "dismiss")],
                 lambda: s.dialog_action, lambda v: setattr(s, "dialog_action", v))
        f.toggle("Close the browser when a flow ends", lambda: s.close_browser_at_end,
                 lambda v: setattr(s, "close_browser_at_end", v),
                 "Failed or stopped accounts are always closed. This is about successful ones.")
        f.section("Runs")
        f.number("Retry failed accounts", lambda: s.retry_failed_accounts,
                 lambda v: setattr(s, "retry_failed_accounts", int(v)), 0, 10,
                 help_text="Run the whole flow again if the browser crashed or could not start.")
        f.number("Maximum time per account", lambda: s.max_run_minutes, lambda v: setattr(s, "max_run_minutes", v),
                 1, 1440, 5, 0, " min", "An account taking longer is stopped and marked failed.")
        f.toggle("Skip accounts that already succeeded today", lambda: s.skip_succeeded_today,
                 lambda v: setattr(s, "skip_succeeded_today", v))
        f.section("Protect your PC")
        f.toggle("Wait when the PC is busy", lambda: s.resource_guard, lambda v: setattr(s, "resource_guard", v),
                 "Before opening another browser, wait if CPU or memory use is too high.")
        f.number("CPU limit", lambda: s.resource_guard_cpu, lambda v: setattr(s, "resource_guard_cpu", v), 30, 100, 5, 0, " %")
        f.number("Memory limit", lambda: s.resource_guard_ram, lambda v: setattr(s, "resource_guard_ram", v), 30, 100, 5, 0, " %")
        f.toggle("Keep running in the tray when the window is closed", lambda: s.tray_mode,
                 lambda v: setattr(s, "tray_mode", v), "Needed for schedules to run while the window is closed.")
        f.finish()
        return f

    # ------------------------------------------------------------------ appearance
    def _appearance_tab(self) -> QWidget:
        s = self.app.settings
        f = _Form(self.app)
        f.section("Look and feel")

        def set_theme(v: str) -> None:
            s.theme = v  # type: ignore[assignment]
            ThemeManager.instance().apply(v, s.font_size)

        f.choice("Theme", [("Light", "light"), ("Dark", "dark"), ("Same as Windows", "system")],
                 lambda: s.theme, set_theme)

        def set_font(v: float) -> None:
            s.font_size = int(v)
            ThemeManager.instance().apply(s.theme, s.font_size)

        f.number("Text size", lambda: s.font_size, set_font, 8, 18, 1, 0, " pt", "Make all text bigger or smaller.")
        f.finish()
        return f

    # ------------------------------------------------------------------ data
    def _data_tab(self) -> QWidget:
        s = self.app.settings
        paths = self.app.services.paths
        f = _Form(self.app)
        f.section("Data folder", "Your database, screenshots, logs and exports are stored here.")
        folder = QLabel(str(paths.root))
        folder.setWordWrap(True)
        f.row("Location", folder)
        btns = QWidget()
        bl = QHBoxLayout(btns)
        bl.setContentsMargins(0, 0, 0, 0)
        open_btn = ClayButton("Open folder", "folder", compact=True)
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.root))))
        move_btn = ClayButton("Change…", "edit", compact=True)
        move_btn.clicked.connect(self._change_location)
        bl.addWidget(open_btn)
        bl.addWidget(move_btn)
        bl.addStretch(1)
        f.row("", btns)
        f.section("Backup", "One zip file with your flows, accounts, connections and settings.")
        b = QWidget()
        bl2 = QHBoxLayout(b)
        bl2.setContentsMargins(0, 0, 0, 0)
        backup = ClayButton("Create backup", "download", "primary", compact=True)
        backup.clicked.connect(self._backup)
        restore = ClayButton("Restore…", "upload", compact=True)
        restore.clicked.connect(self._restore)
        bl2.addWidget(backup)
        bl2.addWidget(restore)
        bl2.addStretch(1)
        f.row("", b)
        f.section("Clean-up")
        f.number("Delete run history older than", lambda: s.log_retention_days,
                 lambda v: setattr(s, "log_retention_days", int(v)), 0, 3650, 1, 0, " days",
                 "0 = keep forever. Screenshots and logs of old runs are deleted too.")
        clean = ClayButton("Clean up now", "trash", compact=True)
        clean.clicked.connect(self._cleanup)
        f.row("", clean)
        f.finish()
        return f

    def _backup(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Save backup", str(self.app.services.paths.backups /
                                                                       "FlowPilot-backup.zip"), "Zip (*.zip)")
        if not target:
            return
        try:
            path = create_backup(self.app.services.paths, self.app.repos, Path(target))
        except Exception as exc:
            show_error(self, "Backup failed", exc)
            return
        info(self, "Backup created", f"Saved to:\n{path}")

    def _restore(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Choose a FlowPilot backup", str(self.app.services.paths.backups),
                                             "Zip (*.zip)")
        if not src or not confirm(self, "Restore backup", "This replaces ALL current flows, accounts and settings with "
                                  "the backup. A safety copy of the current data is kept. FlowPilot will close "
                                  "afterwards; start it again.", "Restore", danger=True):
            return
        if self.app.services.engine.is_busy():
            info(self, "Runs in progress", "Stop all runs first.")
            return
        self.app.services.db.dispose()
        try:
            restore_backup(self.app.services.paths, Path(src))
        except Exception as exc:
            show_error(self, "Restore failed", exc)
            return
        info(self, "Restored", "The backup was restored. FlowPilot will now close — please start it again.")
        QApplication.quit()

    def _cleanup(self) -> None:
        days = self.app.settings.log_retention_days
        if days <= 0:
            info(self, "Clean-up", "Set a number of days first (0 keeps everything).")
            return
        removed = apply_retention(self.app.repos, self.app.services.paths, days)
        self.app.bridge.data_changed.emit("runs")
        info(self, "Clean-up", f"Removed {removed} old run(s).")

    def _change_location(self) -> None:
        target = QFileDialog.getExistingDirectory(self, "Choose a new data folder")
        if not target:
            return
        src = self.app.services.paths.root
        dst = Path(target) / "FlowPilot" if Path(target).name != "FlowPilot" else Path(target)
        if not confirm(self, "Move data", f"Copy all FlowPilot data to:\n{dst}\n\nFlowPilot will use the new folder "
                       "after a restart. The old folder is kept.", "Copy and switch"):
            return
        try:
            if dst.exists() and any(dst.iterdir()):
                if not confirm(self, "Folder not empty", "That folder already contains files. Use it anyway "
                               "(nothing is copied)?", "Use it"):
                    return
            else:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            pointer = location_pointer()
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(str(dst), "utf-8")
        except OSError as exc:
            show_error(self, "Could not move data", exc)
            return
        info(self, "Done", "Restart FlowPilot to use the new folder.")

    # ------------------------------------------------------------------ notifications
    def _notifications_tab(self) -> QWidget:
        s = self.app.settings
        f = _Form(self.app)
        f.section("Desktop")
        f.toggle("Show a Windows notification when a batch finishes", lambda: s.notify_desktop,
                 lambda v: setattr(s, "notify_desktop", v))
        f.section("Telegram (optional)", "Get a message on your phone. Create a bot with @BotFather, "
                  "then send it a message and get your chat ID (e.g. with @userinfobot).")
        token = QLineEdit(s.telegram_bot_token)
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("123456:ABC-DEF…")
        token.editingFinished.connect(lambda: (setattr(s, "telegram_bot_token", token.text().strip()),
                                               self.app.save_settings()))
        chat = QLineEdit(s.telegram_chat_id)
        chat.editingFinished.connect(lambda: (setattr(s, "telegram_chat_id", chat.text().strip()),
                                              self.app.save_settings()))
        f.row("Bot token", token)
        f.row("Chat ID", chat)
        test = ClayButton("Send test message", "message", compact=True)

        def send() -> None:
            run_async(self.app.services.notifier.send_telegram("FlowPilot test message ✔", token.text().strip(),
                                                               chat.text().strip()),
                      lambda ok: info(self, "Telegram", "Message sent." if ok else "Telegram did not accept it. "
                                      "Check the token and chat ID."))

        test.clicked.connect(send)
        f.row("", test)
        f.finish()
        return f

    # ------------------------------------------------------------------ about
    def _about_tab(self) -> QWidget:
        f = _Form(self.app)
        f.section("FlowPilot", "No-code browser automation for anti-detect browser profiles.")
        f.row("Version", QLabel(__version__))
        lic = check_license()
        f.row("License", QLabel(f"{lic.plan} — {lic.message}" if lic.message else lic.plan))
        f.row("Data folder", QLabel(str(self.app.services.paths.root)))
        logs = ClayButton("Open logs folder", "folder", compact=True)
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.app.services.paths.logs))))
        f.row("", logs)
        f.finish()
        return f
