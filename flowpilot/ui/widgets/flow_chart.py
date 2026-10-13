"""Flowchart view of a flow: nodes and arrows, synced with the step list."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.flow_layout import Edge, Node, layout_flow
from flowpilot.core.steps import StepBase
from flowpilot.ui.icons import has_icon, pixmap
from flowpilot.ui.theme.palette import ThemeManager, mix, pal
from flowpilot.ui.theme.shadow import paint_clay
from flowpilot.ui.widgets.clay import IconButton, muted_label
from flowpilot.ui.widgets.step_list import category_color

PAD = 40


class NodeItem(QGraphicsItem):
    def __init__(self, chart: FlowChart, node: Node, step: StepBase | None) -> None:
        super().__init__()
        self.chart = chart
        self.node = node
        self.step = step
        self.setPos(node.x, node.y)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if step is not None else Qt.CursorShape.ArrowCursor)
        self._hover = False
        if step is not None:
            tip = step.summary()
            problems = chart.problems.get(node.index)
            if problems:
                tip += "\n⚠ " + "\n⚠ ".join(problems)
            self.setToolTip(tip)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-16, -16, self.node.w + 32, self.node.h + 32)

    def hoverEnterEvent(self, e: Any) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e: Any) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, e: Any) -> None:  # noqa: N802
        if self.step is not None:
            self.chart.step_clicked.emit(self.node.index, bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
        e.accept()

    def mouseDoubleClickEvent(self, e: Any) -> None:  # noqa: N802
        if self.step is not None:
            self.chart.step_activated.emit(self.node.index)

    def paint(self, p: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        palette = pal()
        n = self.node
        rect = QRectF(0, 0, n.w, n.h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.chart.font())
        if n.kind in ("start", "finish", "end"):
            text = {"start": "Start", "finish": "End"}.get(n.kind) or (self.step.summary() if self.step else "")
            fill = palette.primary if n.kind == "start" else (palette.success if n.kind == "finish" else palette.card_alt)
            outline = self._outline(palette)
            paint_clay(p, rect, n.h / 2, palette, fill=fill, depth=0.35, outline=outline)
            p.setPen(QColor("#FFFFFF" if n.kind in ("start", "finish") else palette.muted))
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
            return
        step = self.step
        assert step is not None
        color = category_color(palette, step.CATEGORY)
        fill = palette.card if step.enabled else mix(palette.card, palette.bg, 0.6)
        if self.node.index in self.chart.selected:
            fill = mix(fill, palette.primary, 0.08)
        radius = 14 if n.kind == "decision" else 18
        paint_clay(p, rect, radius, palette, fill=fill, depth=0.5 + (0.15 if self._hover else 0),
                   hovered=self._hover, outline=self._outline(palette))
        # accent stripe
        stripe = QPainterPath()
        stripe.addRoundedRect(QRectF(0, 0, 7, n.h), 3.5, 3.5)
        p.fillPath(stripe, QColor(color))
        if n.kind == "decision":  # small diamond badge
            d = QPolygonF([QPointF(n.w - 22, 10), QPointF(n.w - 12, 20), QPointF(n.w - 22, 30), QPointF(n.w - 32, 20)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(d)
        icon = step.ICON if has_icon(step.ICON) else "circle"
        p.drawPixmap(QPointF(16, n.h / 2 - 10).toPoint(), pixmap(icon, color, 20))
        small = QFont(font)
        small.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        small.setWeight(QFont.Weight.DemiBold)
        p.setFont(small)
        p.setPen(QColor(color))
        p.drawText(QRectF(46, 8, n.w - 90, 16), int(Qt.AlignmentFlag.AlignVCenter),
                   f"{n.index + 1}. {step.TITLE.upper()}")
        title = QFont(font)
        title.setWeight(QFont.Weight.DemiBold)
        p.setFont(title)
        p.setPen(QColor(palette.text if step.enabled else palette.muted))
        fm = QFontMetrics(title)
        p.drawText(QRectF(46, 26, n.w - 56, n.h - 30), int(Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(step.summary(), Qt.TextElideMode.ElideRight, int(n.w - 60)))
        mark = self.chart.run_marks.get(n.index, "")
        if mark in ("ok", "failed", "running"):
            c = {"ok": palette.success, "failed": palette.danger, "running": palette.primary}[mark]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c))
            p.drawEllipse(QPointF(n.w - 12, n.h - 12), 5, 5)

    def _outline(self, palette: Any) -> str | None:
        i = self.node.index
        if self.chart.run_marks.get(i) == "running":
            return palette.primary
        if self.chart.run_marks.get(i) == "failed" or self.chart.problems.get(i):
            return palette.danger
        if i in self.chart.selected:
            return palette.primary
        return None


class EdgeItem(QGraphicsItem):
    def __init__(self, edge: Edge) -> None:
        super().__init__()
        self.edge = edge
        xs = [x for x, _ in edge.points]
        ys = [y for _, y in edge.points]
        self._bounds = QRectF(min(xs) - 40, min(ys) - 20, max(xs) - min(xs) + 80, max(ys) - min(ys) + 40)
        self.setZValue(-1)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, p: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        palette = pal()
        e = self.edge
        color = {"yes": palette.success, "no": palette.danger, "error": palette.danger,
                 "back": palette.secondary}.get(e.style, mix(palette.muted, palette.bg, 0.25))
        pen = QPen(QColor(color), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if e.style in ("error", "back"):
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(pen)
        path = QPainterPath(QPointF(*e.points[0]))
        for pt in e.points[1:]:
            path.lineTo(QPointF(*pt))
        p.drawPath(path)
        # arrow head at the end
        (x1, y1), (x2, y2) = e.points[-2], e.points[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        size = 8
        head = QPolygonF([QPointF(x2, y2),
                          QPointF(x2 - size * math.cos(ang - 0.45), y2 - size * math.sin(ang - 0.45)),
                          QPointF(x2 - size * math.cos(ang + 0.45), y2 - size * math.sin(ang + 0.45))])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(head)
        if e.label:
            # label next to the second point (the turn of the edge)
            lx, ly = e.points[1] if len(e.points) > 2 else e.points[0]
            f = QFont()
            f.setPointSizeF(8.5)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            fm = QFontMetrics(f)
            w = fm.horizontalAdvance(e.label) + 14
            if e.style == "back":
                rect = QRectF(lx - w / 2, (e.points[1][1] + e.points[2][1]) / 2 - 10, w, 20)
            else:
                rect = QRectF(lx + (6 if e.points[-1][0] >= lx else -w - 6), ly - 22, w, 20)
            bg = QColor(color)
            bg.setAlpha(40)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(QColor(color))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), e.label)


class _View(QGraphicsView):
    def __init__(self, chart: FlowChart) -> None:
        super().__init__()
        self.chart = chart
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("QGraphicsView { background: transparent; border: none; }")

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.chart.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(e)


class FlowChart(QWidget):
    """Flowchart of the steps. Click a node to select the step; double-click to edit."""

    step_clicked = Signal(int, bool)  # index, add to selection
    step_activated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.steps: list[StepBase] = []
        self.problems: dict[int, list[str]] = {}
        self.selected: set[int] = set()
        self.run_marks: dict[int, str] = {}
        self._scale = 1.0
        self._fitted = False
        self._items: dict[int, NodeItem] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        hint = muted_label("Click a box to edit it · Ctrl + wheel to zoom · drag to move")
        bar.addWidget(hint, 1)
        for icon, tip, slot in (("minus", "Zoom out", lambda: self.zoom(1 / 1.2)),
                                ("plus", "Zoom in", lambda: self.zoom(1.2)),
                                ("maximize", "Fit the whole flow in the window (click again for 100%)", self.toggle_fit)):
            b = IconButton(icon, tip, size=26)
            b.clicked.connect(slot)
            bar.addWidget(b)
        lay.addLayout(bar)
        self.scene = QGraphicsScene(self)
        self.view = _View(self)
        self.view.setScene(self.scene)
        lay.addWidget(self.view, 1)
        ThemeManager.instance().changed.connect(lambda *_: self.scene.update())

    def set_steps(self, steps: list[StepBase], problems: dict[int, list[str]], selected: list[int]) -> None:
        self.steps, self.problems, self.selected = steps, problems, set(selected)
        self.scene.clear()
        self._items.clear()
        box = layout_flow(steps)
        for e in box.edges:
            self.scene.addItem(EdgeItem(e))
        for n in box.nodes:
            item = NodeItem(self, n, steps[n.index] if 0 <= n.index < len(steps) else None)
            self.scene.addItem(item)
            if n.index >= 0:
                self._items[n.index] = item
        self.scene.setSceneRect(QRectF(-PAD, -PAD, box.w + 2 * PAD, box.h + 2 * PAD))

    def set_selection(self, rows: list[int]) -> None:
        self.selected = set(rows)
        self.scene.update()
        if rows and rows[-1] in self._items:
            self.view.ensureVisible(self._items[rows[-1]], 60, 60)

    def mark(self, index: int, state: str) -> None:
        if state:
            self.run_marks[index] = state
        else:
            self.run_marks.pop(index, None)
        if index in self._items:
            self.view.ensureVisible(self._items[index], 60, 60)
        self.scene.update()

    def clear_marks(self) -> None:
        self.run_marks.clear()
        self.scene.update()

    def zoom(self, factor: float) -> None:
        new = max(0.1, min(2.5, self._scale * factor))
        self.view.scale(new / self._scale, new / self._scale)
        self._scale = new
        self._fitted = False

    def _set_scale(self, scale: float) -> None:
        self.view.resetTransform()
        self._scale = 1.0
        self.zoom(scale)

    def toggle_fit(self) -> None:
        """The toolbar button: show the whole flow; when it already is, back to 100 %."""
        if self._fitted:
            self._set_scale(1.0)
            rect = self.scene.sceneRect()
            self.view.centerOn(rect.center().x(), rect.top() + self.view.viewport().height() / 2)
            return
        self.fit_all()

    def fit_all(self) -> None:
        """Zoom so the whole flow — width and height — is visible."""
        rect = self.scene.sceneRect()
        vp = self.view.viewport()
        if rect.width() <= 0 or rect.height() <= 0 or vp.width() <= 0 or vp.height() <= 0:
            return
        scale = min((vp.width() - 10) / rect.width(), (vp.height() - 10) / rect.height(), 1.5)
        self._set_scale(scale)
        self.view.centerOn(rect.center())
        self._fitted = True

    def fit(self) -> None:
        rect = self.scene.sceneRect()
        if rect.width() <= 0:
            return
        # fit the width (the flow scrolls vertically); never smaller than readable
        sx = (self.view.viewport().width() - 10) / rect.width()
        self._set_scale(max(0.6, min(1.0, sx)))
        self.view.centerOn(rect.center().x(), rect.top() + self.view.viewport().height() / 2 / self._scale)
