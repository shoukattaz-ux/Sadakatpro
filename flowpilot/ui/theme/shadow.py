"""Clay rendering: double soft shadows (dark bottom-right + light top-left) and insets.

Qt style sheets cannot draw two shadows, so clay widgets paint themselves with
:func:`paint_clay`. Blurred shadow images are expensive, so they are cached by size,
radius, colour and blur (an LRU of a few hundred entries is plenty for a whole UI).
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from flowpilot.ui.theme.palette import Palette, to_qcolor

_CACHE: OrderedDict[tuple, QPixmap] = OrderedDict()
_CACHE_SIZE = 300


def _blurred_rounded_rect(w: int, h: int, radius: float, color: QColor, blur: int) -> QPixmap:
    key = (w, h, round(radius), color.rgba(), blur)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    pad = blur * 2
    img = QImage(w + pad * 2, h + pad * 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(pad, pad, w, h), radius, radius)
    p.end()
    # Blur through a graphics scene: fast, high quality and available everywhere.
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(blur)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(0, 0, out.width(), out.height()), QRectF(0, 0, out.width(), out.height()))
    p.end()
    pix = QPixmap.fromImage(out)
    _CACHE[key] = pix
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return pix


def _draw_shadow(p: QPainter, rect: QRectF, radius: float, color: QColor, blur: int, dx: float, dy: float) -> None:
    w, h = max(1, int(rect.width())), max(1, int(rect.height()))
    pix = _blurred_rounded_rect(w, h, radius, color, blur)
    pad = blur * 2
    p.drawPixmap(QPointF(rect.x() - pad + dx, rect.y() - pad + dy), pix)


def paint_clay(p: QPainter, rect: QRectF, radius: float, pal: Palette, fill: str | None = None,
               depth: float = 1.0, pressed: bool = False, hovered: bool = False, outline: str | None = None) -> None:
    """Paint a puffy clay surface in ``rect``.

    ``depth`` scales the shadow distance (0.5 = subtle, 1 = normal). ``pressed`` draws the
    surface pushed into the background (inner shadows) instead of raised.
    """
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QColor(fill) if fill else pal.c("card")
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if not pressed:
        off = 6 * depth + (1.5 if hovered else 0)
        blur = int(12 * depth + 6)
        _draw_shadow(p, rect, radius, to_qcolor(pal.shadow_dark), blur, off, off * 1.15)
        _draw_shadow(p, rect, radius, to_qcolor(pal.shadow_light), blur, -off * 0.9, -off * 0.9)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, base.lighter(104 if pal.name == "light" else 112))
        grad.setColorAt(1, base.darker(102 if pal.name == "light" else 100))
        p.fillPath(path, QBrush(grad))
        # soft top highlight for the "puffy" look
        hl = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.5))
        top = QColor(255, 255, 255, 70 if pal.name == "light" else 14)
        hl.setColorAt(0, top)
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(hl))
    else:
        p.fillPath(path, base.darker(104 if pal.name == "light" else 110))
        overlay = _inset_overlay(int(rect.width()), int(rect.height()), radius, to_qcolor(pal.shadow_dark),
                                 to_qcolor(pal.shadow_light), depth)
        p.setClipPath(path)
        p.drawImage(rect.topLeft(), overlay)
        p.setClipping(False)
    if outline:
        p.setPen(QPen(QColor(outline), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
    p.restore()


_INSET_CACHE: OrderedDict[tuple, QImage] = OrderedDict()


def _inset_overlay(w: int, h: int, radius: float, dark: QColor, light: QColor, depth: float) -> QImage:
    """Inner shadows for a pressed surface of size w x h (cached)."""
    key = (w, h, round(radius), dark.rgba(), light.rgba(), round(depth, 2))
    hit = _INSET_CACHE.get(key)
    if hit is not None:
        _INSET_CACHE.move_to_end(key)
        return hit
    inner = int(8 * depth + 3)
    out = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    rect = QRectF(0, 0, w, h)
    for color, dx, dy in ((dark, 3.5 * depth, 3.5 * depth), (light, -3.5 * depth, -3.5 * depth)):
        # A ring of colour around the shape, shifted inwards, then blurred: an inner shadow.
        pad = inner * 3
        img = QImage(w + pad * 2, h + pad * 2, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        ring = QPainterPath()
        ring.addRect(QRectF(0, 0, img.width(), img.height()))
        ring.addRoundedRect(rect.translated(pad + dx, pad + dy), radius, radius)
        ring.setFillRule(Qt.FillRule.OddEvenFill)
        ip = QPainter(img)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        ip.fillPath(ring, color)
        ip.end()
        blurred = _blur_image(img, inner)
        op = QPainter(out)
        op.drawImage(QPointF(-pad, -pad), blurred)
        op.end()
    _INSET_CACHE[key] = out
    if len(_INSET_CACHE) > 200:
        _INSET_CACHE.popitem(last=False)
    return out


def _blur_image(img: QImage, radius: int) -> QImage:
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(0, 0, out.width(), out.height()), QRectF(0, 0, out.width(), out.height()))
    p.end()
    return out


def paint_inset(p: QPainter, rect: QRectF, radius: float, pal: Palette, fill: str | None = None) -> None:
    """A field pressed into the surface (used for inputs, progress tracks, toggles)."""
    paint_clay(p, rect, radius, pal, fill=fill or pal.input, depth=0.5, pressed=True)
