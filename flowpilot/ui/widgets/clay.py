"""Custom painted Claymorphism widgets: ClayCard, ClayButton, ClayToggle, ClaySpinBox,
StatusPill, HelpIcon, plus small helpers (FlowLayout, EmptyState)."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from flowpilot.ui.icons import pixmap
from flowpilot.ui.theme.palette import STATUS_COLORS, Palette, ThemeManager, mix, pal
from flowpilot.ui.theme.shadow import paint_clay, paint_inset


def _repaint_on_theme(widget: QWidget) -> None:
    ThemeManager.instance().changed.connect(lambda *_: widget.update())


# --------------------------------------------------------------------------- card

class ClayCard(QFrame):
    """A puffy rounded surface. Put widgets in ``card.body`` (a QVBoxLayout)."""

    SHADOW = 14

    def __init__(self, parent: QWidget | None = None, radius: int = 24, padding: int = 16,
                 hoverable: bool = False, fill: str | None = None, depth: float = 1.0) -> None:
        super().__init__(parent)
        self.radius = radius
        self.hoverable = hoverable
        self.fill_key = fill
        self.depth = depth
        self.outline: str | None = None
        self._lift = 0.0
        m = self.SHADOW + padding
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(m, m, m, m)
        self.body.setSpacing(10)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._anim = QPropertyAnimation(self, b"lift", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        _repaint_on_theme(self)

    def get_lift(self) -> float:
        return self._lift

    def set_lift(self, value: float) -> None:
        self._lift = value
        self.update()

    lift = Property(float, get_lift, set_lift)

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.hoverable:
            self._anim.stop()
            self._anim.setEndValue(1.0)
            self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.hoverable:
            self._anim.stop()
            self._anim.setEndValue(0.0)
            self._anim.start()
        super().leaveEvent(event)

    def surface_rect(self) -> QRectF:
        s = self.SHADOW
        return QRectF(self.rect()).adjusted(s, s - self._lift * 2, -s, -s - self._lift * 2)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        palette = pal()
        fill = getattr(palette, self.fill_key) if self.fill_key else None
        paint_clay(p, self.surface_rect(), self.radius, palette, fill=fill, depth=self.depth + self._lift * 0.25,
                   hovered=self._lift > 0.5, outline=self.outline)


# --------------------------------------------------------------------------- button

class ClayButton(QPushButton):
    """Clay button. ``variant``: primary / secondary / success / danger / warning / neutral / ghost."""

    MARGIN = 6

    def __init__(self, text: str = "", icon: str | None = None, variant: str = "neutral",
                 parent: QWidget | None = None, tooltip: str = "", compact: bool = False) -> None:
        super().__init__(text, parent)
        self.variant = variant
        self.icon_name = icon
        self.compact = compact
        self._hover = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(140)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        _repaint_on_theme(self)

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self._anim.stop()
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._anim.stop()
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(e)

    def set_variant(self, variant: str) -> None:
        self.variant = variant
        self.update()

    def colors(self, p: Palette) -> tuple[str | None, str]:
        """(fill, text colour)."""
        if self.variant == "ghost":
            return None, p.primary
        if self.variant in ("primary", "secondary", "success", "danger", "warning"):
            return getattr(p, self.variant), "#FFFFFF"
        return p.card, p.text

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) if self.text() else 0
        icon_w = 18 + (8 if self.text() else 0) if self.icon_name else 0
        pad = 12 if self.compact else 18
        h = 34 if self.compact else 42
        return QSize(w + icon_w + pad * 2 + self.MARGIN * 2, h + self.MARGIN * 2 - (4 if self.compact else 0))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = pal()
        fill, text_color = self.colors(palette)
        m = self.MARGIN
        rect = QRectF(self.rect()).adjusted(m, m, -m, -m)
        radius = min(16.0, rect.height() / 2)
        enabled = self.isEnabled()
        pressed = self.isDown() or (self.isCheckable() and self.isChecked())
        if fill is not None:
            if not enabled:
                fill = mix(fill, palette.bg, 0.55)
            elif self._hover > 0 and not pressed:
                fill = mix(fill, "#FFFFFF" if palette.name == "light" else "#000000", 0.06 * self._hover)
            paint_clay(p, rect, radius, palette, fill=fill, depth=0.45 + 0.1 * self._hover, pressed=pressed)
        elif self._hover > 0 or pressed:
            c = QColor(palette.primary)
            c.setAlpha(int(28 * max(self._hover, 1.0 if pressed else 0)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(rect, radius, radius)
        color = text_color if enabled else palette.muted
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        icon_w = 18 if self.icon_name else 0
        gap = 8 if self.icon_name and self.text() else 0
        total = icon_w + gap + text_w
        x = rect.center().x() - total / 2 + (1 if pressed else 0)
        y_off = 1 if pressed else 0
        if self.icon_name:
            pm = pixmap(self.icon_name, color, 18)
            p.drawPixmap(QPoint(int(x), int(rect.center().y() - 9 + y_off)), pm)
            x += icon_w + gap
        if self.text():
            p.setPen(QColor(color))
            f = self.font()
            f.setWeight(f.Weight.DemiBold)
            p.setFont(f)
            p.drawText(QRectF(x, rect.top() + y_off, text_w + 4, rect.height()),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())


class IconButton(ClayButton):
    """Small round clay button with only an icon."""

    def __init__(self, icon: str, tooltip: str = "", variant: str = "neutral", parent: QWidget | None = None,
                 size: int = 34) -> None:
        super().__init__("", icon, variant, parent, tooltip=tooltip, compact=True)
        self._size = size
        self.setFixedSize(size + self.MARGIN * 2, size + self.MARGIN * 2)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._size + self.MARGIN * 2, self._size + self.MARGIN * 2)


# --------------------------------------------------------------------------- toggle

class ClayToggle(QAbstractButton):
    """An animated on/off switch."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None, tooltip: str = "") -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(58, 36)
        if tooltip:
            self.setToolTip(tooltip)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        _repaint_on_theme(self)

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        if not self.signalsBlocked():
            return
        self._pos = 1.0 if checked else 0.0  # no animation when set programmatically with blocked signals

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(58, 36)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = pal()
        track = QRectF(5, 6, 48, 24)
        fill = mix(palette.input, palette.primary, self._pos) if self.isEnabled() else palette.input
        paint_inset(p, track, 12, palette, fill=fill)
        d = 22.0
        x = track.left() + 1 + (track.width() - d - 2) * self._pos
        knob = QRectF(x, track.top() + 1, d, d)
        paint_clay(p, knob, d / 2, palette, fill=palette.card, depth=0.3)


# --------------------------------------------------------------------------- spin box

class ClaySpinBox(QWidget):
    """Big − value + control. Works with integers (``decimals=0``) or decimals."""

    valueChanged = Signal(float)

    def __init__(self, value: float = 1, minimum: float = 0, maximum: float = 999, step: float = 1,
                 decimals: int = 0, suffix: str = "", parent: QWidget | None = None, tooltip: str = "") -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.minus = IconButton("minus", "Decrease", size=32)
        self.plus = IconButton("plus", "Increase", size=32)
        self.box = QDoubleSpinBox()
        self.box.setDecimals(decimals)
        self.box.setRange(minimum, maximum)
        self.box.setSingleStep(step)
        self.box.setValue(value)
        self.box.setSuffix(suffix)
        self.box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Note: no setButtonSymbols(NoButtons) - with a style sheet Qt then collapses the text field;
        # the arrows are hidden with a zero width in the style sheet instead.
        self.box.setMinimumWidth(70 + len(suffix) * 6)
        self.box.setMaximumWidth(120 + len(suffix) * 6)
        if tooltip:
            self.setToolTip(tooltip)
        lay.addWidget(self.minus)
        lay.addWidget(self.box)
        lay.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self.box.stepBy(-1))
        self.plus.clicked.connect(lambda: self.box.stepBy(1))
        self.minus.setAutoRepeat(True)
        self.plus.setAutoRepeat(True)
        self.box.valueChanged.connect(self.valueChanged.emit)

    def value(self) -> float:
        return self.box.value()

    def int_value(self) -> int:
        return int(round(self.box.value()))

    def setValue(self, v: float) -> None:  # noqa: N802
        self.box.setValue(v)

    def setRange(self, lo: float, hi: float) -> None:  # noqa: N802
        self.box.setRange(lo, hi)


# --------------------------------------------------------------------------- status pill

class StatusPill(QWidget):
    """Rounded status label whose colour fades when the status changes."""

    LABELS = {"queued": "Queued", "starting": "Starting", "running": "Running", "waiting": "Waiting",
              "done": "Done", "failed": "Failed", "stopped": "Stopped", "skipped": "Skipped", "paused": "Paused"}

    def __init__(self, status: str = "queued", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = status
        self._color = QColor(pal().c(STATUS_COLORS.get(status, "muted")))
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(350)
        self._anim.valueChanged.connect(self._on_color)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        _repaint_on_theme(self)
        ThemeManager.instance().changed.connect(lambda *_: self.set_status(self._status, animate=False))

    def _on_color(self, c: QColor) -> None:
        self._color = QColor(c)
        self.update()

    def status(self) -> str:
        return self._status

    def set_status(self, status: str, animate: bool = True, text: str | None = None) -> None:
        self._status = status
        self._text = text
        target = pal().c(STATUS_COLORS.get(status, "muted"))
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._color)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._color = target
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return getattr(self, "_text", None) or self.LABELS.get(self._status, self._status.title())

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self.text()) + 30, 26)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._color)
        bg.setAlpha(45 if pal().name == "light" else 70)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(10, r.center().y() - 3.5, 7, 7))
        p.setPen(self._color.darker(130) if pal().name == "light" else self._color.lighter(130))
        f = self.font()
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(22, 0, r.width() - 24, r.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())


# --------------------------------------------------------------------------- help icon

class HelpIcon(QLabel):
    """A small “?” that shows a plain-English explanation on hover or click."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.help_text = text
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFixedSize(18, 18)
        self._refresh()
        ThemeManager.instance().changed.connect(lambda *_: self._refresh())

    def _refresh(self) -> None:
        self.setPixmap(pixmap("help", pal().muted, 16))

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        QToolTip.showText(ev.globalPosition().toPoint(), self.help_text, self)


def labeled(label: str, widget: QWidget, help_text: str = "", stretch: bool = False) -> QWidget:
    """``Label [?]  widget`` in one row."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lab = QLabel(label)
    lay.addWidget(lab)
    if help_text:
        lay.addWidget(HelpIcon(help_text))
    lay.addSpacing(6)
    lay.addWidget(widget, 1 if stretch else 0)
    if not stretch:
        lay.addStretch(1)
    return w


def title_label(text: str, role: str = "title") -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", role)
    return lab


def muted_label(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "muted")
    lab.setWordWrap(wrap)
    return lab


class EmptyState(QWidget):
    """Friendly placeholder: big icon, a message and an optional action button."""

    def __init__(self, icon: str, title: str, message: str, action_text: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon
        lay.addWidget(self._icon)
        t = title_label(title, "subtitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        m = muted_label(message)
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m.setMinimumWidth(260)
        m.setMaximumWidth(460)
        m.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(m, 3)
        row.addStretch(1)
        lay.addLayout(row)
        self.button: ClayButton | None = None
        if action_text:
            self.button = ClayButton(action_text, "plus", "primary")
            lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignCenter)
        self._refresh()
        ThemeManager.instance().changed.connect(lambda *_: self._refresh())

    def _refresh(self) -> None:
        self._icon.setPixmap(pixmap(self._icon_name, pal().primary, 48, 1.6))


class FlowLayout(QLayout):
    """Wraps child widgets into rows (used for the run cards)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: ANN001, N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: ANN201, N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: ANN201, N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: ANN201, N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, dry: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        line_h = 0
        right = rect.right() - m.right()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > right + 1 and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self.spacing()
                line_h = 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
