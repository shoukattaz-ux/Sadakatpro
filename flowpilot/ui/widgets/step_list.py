"""The vertical list of step cards in the flow editor."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from flowpilot.core.steps import StepBase
from flowpilot.ui.icons import has_icon, pixmap
from flowpilot.ui.theme.palette import Palette, ThemeManager, mix, pal
from flowpilot.ui.theme.shadow import paint_clay

CATEGORY_COLORS = {"Navigation": "primary", "Interaction": "secondary", "Waiting": "warning",
                   "Data & variables": "success", "Logic": "#B07CF2", "Utilities": "muted", "Ending / Browser": "danger"}
ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_INDEX = Qt.ItemDataRole.UserRole + 1
INDENT = 26
ROW_H = 74


def category_color(p: Palette, category: str) -> str:
    key = CATEGORY_COLORS.get(category, "primary")
    return key if key.startswith("#") else getattr(p, key)


class StepDelegate(QStyledItemDelegate):
    def __init__(self, view: StepListWidget) -> None:
        super().__init__(view)
        self.view = view

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802
        step = self.view.step_at(index.row())
        extra = 18 if step is not None and step.note else 0
        compact = step is not None and step.BLOCK in ("else", "end_if", "end_loop", "end_try", "catch")
        return QSize(200, (52 if compact else ROW_H) + extra)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        step = self.view.step_at(index.row())
        if step is None:
            return
        palette = pal()
        row = index.row()
        depth = self.view.depths[row] if row < len(self.view.depths) else 0
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        problems = self.view.problems.get(row, [])
        run_state = self.view.run_marks.get(row, "")
        r = QRectF(option.rect).adjusted(10 + depth * INDENT, 7, -12, -8)
        outline = None
        if run_state == "running":
            outline = palette.primary
        elif run_state == "failed" or problems:
            outline = palette.danger
        elif selected:
            outline = mix(palette.primary, palette.card, 0.35)
        fill = palette.card if step.enabled else mix(palette.card, palette.bg, 0.6)
        if selected:
            fill = mix(fill, palette.primary, 0.07)
        paint_clay(p, r, 18, palette, fill=fill, depth=0.55, outline=outline)

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = category_color(palette, step.CATEGORY)
        x = r.left() + 12
        cy = r.top() + (r.height() - (18 if step.note else 0)) / 2
        # number
        num_font = QFont(option.font)
        num_font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        p.setFont(num_font)
        p.setPen(QColor(palette.muted))
        p.drawText(QRectF(x, cy - 10, 26, 20), int(Qt.AlignmentFlag.AlignCenter), str(row + 1))
        x += 30
        # icon bubble
        bubble = QRectF(x, cy - 17, 34, 34)
        bg = QColor(color)
        bg.setAlpha(40 if palette.name == "light" else 70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(bubble, 12, 12)
        icon_name = step.ICON if has_icon(step.ICON) else "circle"
        p.drawPixmap(QPoint(int(bubble.left() + 8), int(bubble.top() + 8)), pixmap(icon_name, color, 18))
        x += 46
        # collapse chevron for blocks
        right = r.right() - 12
        if step.BLOCK in ("if", "loop", "try"):
            chev = pixmap("chevron-right" if step.collapsed else "chevron-down", palette.muted, 16)
            p.drawPixmap(QPoint(int(right - 16), int(cy - 8)), chev)
            right -= 26
        # badges on the right
        badges: list[tuple[str, str]] = []
        if run_state == "ok":
            badges.append(("✔", palette.success))
        elif run_state == "failed":
            badges.append(("✖ failed", palette.danger))
        elif run_state == "running":
            badges.append(("running…", palette.primary))
        if not step.enabled:
            badges.append(("disabled", palette.muted))
        if step.label:
            badges.append((f"# {step.label}", palette.secondary))
        if problems:
            badges.append(("needs attention", palette.danger))
        if step.ADVANCED:
            badges.append(("advanced", palette.warning))
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.5))
        small.setWeight(QFont.Weight.DemiBold)
        p.setFont(small)
        fm = QFontMetrics(small)
        for text, c in badges:
            w = fm.horizontalAdvance(text) + 16
            br = QRectF(right - w, cy - 11, w, 22)
            bc = QColor(c)
            bc.setAlpha(40)
            p.setBrush(bc)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(br, 11, 11)
            p.setPen(QColor(c))
            p.drawText(br, int(Qt.AlignmentFlag.AlignCenter), text)
            right -= w + 6
        # title and summary
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        compact = step.BLOCK in ("else", "end_if", "end_loop", "end_try", "catch")
        text_color = QColor(palette.text if step.enabled else palette.muted)
        avail = max(40.0, right - x - 8)
        if compact:
            p.setFont(title_font)
            p.setPen(text_color)
            p.drawText(QRectF(x, cy - 11, avail, 22), int(Qt.AlignmentFlag.AlignVCenter),
                       QFontMetrics(title_font).elidedText(step.summary(), Qt.TextElideMode.ElideRight, int(avail)))
        else:
            p.setFont(small)
            p.setPen(QColor(color))
            p.drawText(QRectF(x, cy - 20, avail, 16), int(Qt.AlignmentFlag.AlignVCenter), step.TITLE.upper())
            p.setFont(title_font)
            p.setPen(text_color)
            p.drawText(QRectF(x, cy - 3, avail, 22), int(Qt.AlignmentFlag.AlignVCenter),
                       QFontMetrics(title_font).elidedText(step.summary(), Qt.TextElideMode.ElideRight, int(avail)))
        if step.note:
            p.setFont(small)
            p.setPen(QColor(palette.muted))
            p.drawText(QRectF(r.left() + 88, r.bottom() - 24, r.width() - 100, 18), int(Qt.AlignmentFlag.AlignVCenter),
                       fm.elidedText("💬 " + step.note.replace("\n", " "), Qt.TextElideMode.ElideRight,
                                     int(r.width() - 110)))
        p.restore()


class StepListWidget(QListWidget):
    """Shows FlowDocument steps; reordering by drag & drop emits :attr:`reordered`."""

    reordered = Signal(list)  # step ids in the new order
    collapse_toggled = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.steps: list[StepBase] = []
        self.depths: list[int] = []
        self.problems: dict[int, list[str]] = {}
        self.run_marks: dict[int, str] = {}
        self.setItemDelegate(StepDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setStyleSheet("QListWidget { background: transparent; } QListWidget::item:selected { background: transparent; }")
        self.model().rowsMoved.connect(self._on_rows_moved)
        ThemeManager.instance().changed.connect(lambda *_: self.viewport().update())

    def step_at(self, row: int) -> StepBase | None:
        item = self.item(row)
        if item is None:
            return None
        idx = item.data(ROLE_INDEX)
        return self.steps[idx] if isinstance(idx, int) and 0 <= idx < len(self.steps) else None

    def set_steps(self, steps: list[StepBase], depths: list[int], problems: dict[int, list[str]],
                  hidden: set[int], keep_selection: list[int] | None = None) -> None:
        self.steps, self.depths, self.problems = steps, depths, problems
        selected = keep_selection if keep_selection is not None else self.selected_rows()
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        for i, s in enumerate(steps):
            it = QListWidgetItem()
            it.setData(ROLE_ID, s.id)
            it.setData(ROLE_INDEX, i)
            if problems.get(i):
                it.setToolTip("\n".join(problems[i]))
            elif s.note:
                it.setToolTip(s.note)
            self.addItem(it)
            it.setHidden(i in hidden)
        for r in selected:
            if 0 <= r < self.count():
                self.item(r).setSelected(True)
        if selected and 0 <= selected[-1] < self.count():
            self.setCurrentRow(selected[-1], self.selectionModel().SelectionFlag.NoUpdate)
        self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)
        self.itemSelectionChanged.emit()

    def selected_rows(self) -> list[int]:
        return sorted(self.row(i) for i in self.selectedItems())

    def mark(self, row: int, state: str) -> None:
        if state:
            self.run_marks[row] = state
        else:
            self.run_marks.pop(row, None)
        it = self.item(row)
        if it is not None:
            self.scrollToItem(it)
        self.viewport().update()

    def clear_marks(self) -> None:
        self.run_marks.clear()
        self.viewport().update()

    def _on_rows_moved(self, *_: object) -> None:
        ids = [self.item(i).data(ROLE_ID) for i in range(self.count())]
        self.reordered.emit(ids)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        item = self.itemAt(e.position().toPoint())
        if item is not None:
            step = self.step_at(self.row(item))
            rect: QRect = self.visualItemRect(item)
            # click on the chevron area toggles the block
            if step is not None and step.BLOCK in ("if", "loop", "try") and e.position().x() > rect.right() - 46:
                self.collapse_toggled.emit(self.row(item))
                return
        super().mousePressEvent(e)
