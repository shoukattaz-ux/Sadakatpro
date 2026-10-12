"""Application style sheet for standard Qt widgets (clay widgets paint themselves)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from flowpilot.ui.theme.palette import Palette, mix

FONT_FAMILIES = ["Inter", "Poppins", "Segoe UI", "Noto Sans", "Helvetica Neue", "Arial"]
_fonts_loaded = False


def load_bundled_fonts() -> None:
    """Load any .ttf/.otf files from resources/fonts (Inter/Poppins can be dropped in there)."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    from flowpilot.config import resource_path

    folder = resource_path("fonts")
    if folder.exists():
        for f in sorted(Path(folder).glob("*.[ot]tf")):
            QFontDatabase.addApplicationFont(str(f))
    _fonts_loaded = True


def app_font(size: int) -> QFont:
    families = set(QFontDatabase.families())
    family = next((f for f in FONT_FAMILIES if f in families), QApplication.font().family())
    font = QFont(family, size)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def build_qss(p: Palette, font_size: int) -> str:
    sel_text = p.text
    return f"""
* {{ color: {p.text}; outline: none; }}
QMainWindow, QDialog, QWizard, #PageRoot {{ background: {p.bg}; }}
QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {p.card}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {font_size + 9}pt; font-weight: 700; }}
QLabel[role="subtitle"] {{ font-size: {font_size + 2}pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.muted}; }}
QLabel[role="error"] {{ color: {p.danger}; }}
QLabel[role="success"] {{ color: {p.success}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit, QTimeEdit {{
    background: {p.input}; border: 1px solid {mix(p.input, p.border, 0.6)}; border-radius: 12px;
    padding: 7px 10px; selection-background-color: {p.selection}; selection-color: {sel_text};
    border-top: 2px solid {mix(p.input, '#000000', 0.06 if p.name == 'light' else 0.25)};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateTimeEdit:focus, QTimeEdit:focus {{ border: 1px solid {p.primary}; border-top: 2px solid {p.primary}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p.muted}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 10px;
    selection-background-color: {p.selection}; selection-color: {sel_text}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateTimeEdit::up-button, QDateTimeEdit::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{
    border: none; background: transparent; width: 0px; }}

QPushButton {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 12px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {p.primary}; }}
QPushButton:pressed {{ background: {p.card_alt}; }}
QToolButton {{ background: transparent; border: none; border-radius: 10px; padding: 5px; }}
QToolButton:hover {{ background: {p.card_alt}; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border-radius: 6px;
    background: {p.input}; border: 1px solid {p.border}; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {p.primary}; border-color: {p.primary}; }}

QTableView, QTreeView, QListView, QListWidget, QTableWidget {{
    background: {p.card}; alternate-background-color: {p.card_alt}; border: none; border-radius: 16px;
    gridline-color: transparent; selection-background-color: {p.selection}; selection-color: {sel_text}; padding: 4px;
}}
QTableView::item, QTreeView::item {{ padding: 6px 6px; border: none; }}
QListView::item {{ border-radius: 10px; padding: 4px; }}
QListView::item:selected, QTableView::item:selected {{ background: {p.selection}; color: {sel_text}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; color: {p.muted}; border: none; padding: 8px 6px;
    font-weight: 600; text-transform: uppercase; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

QTabWidget::pane {{ border: none; background: transparent; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {p.muted}; padding: 9px 16px; margin-right: 6px;
    border-radius: 12px; font-weight: 600; }}
QTabBar::tab:selected {{ background: {p.card}; color: {p.primary}; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle {{ background: {mix(p.muted, p.bg, 0.45)}; border-radius: 4px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {p.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QProgressBar {{ background: {p.input}; border: none; border-radius: 6px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p.primary}; border-radius: 6px; }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {p.selection}; color: {sel_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
QGroupBox {{ border: none; margin-top: 22px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 4px; color: {p.muted}; }}
QWizard QWidget {{ background: transparent; }}
QMessageBox {{ background: {p.bg}; }}
QStatusBar {{ background: transparent; color: {p.muted}; }}
"""


def apply_app_style(p: Palette, font_size: int) -> None:
    app = QApplication.instance()
    if app is None:
        return
    load_bundled_fonts()
    app.setFont(app_font(font_size))  # type: ignore[union-attr]
    qp = QPalette()
    from flowpilot.ui.theme.palette import to_qcolor

    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "input"),
                      (QPalette.ColorRole.AlternateBase, "card_alt"), (QPalette.ColorRole.Text, "text"),
                      (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Button, "card"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selection"),
                      (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.PlaceholderText, "muted"),
                      (QPalette.ColorRole.ToolTipBase, "card"), (QPalette.ColorRole.ToolTipText, "text")):
        qp.setColor(role, to_qcolor(getattr(p, key)))
    app.setPalette(qp)  # type: ignore[union-attr]
    app.setStyleSheet(build_qss(p, font_size))  # type: ignore[union-attr]
