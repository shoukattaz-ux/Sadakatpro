"""Render the SVG logo to PNG, a multi-size ICO and installer bitmaps (run after editing the SVG)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
ICONS = "flowpilot/resources/icons"
renderer = QSvgRenderer(f"{ICONS}/flowpilot.svg")


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


render(512).save(f"{ICONS}/flowpilot.png")
render(256).save(f"{ICONS}/flowpilot-256.png")
big = Image.open(f"{ICONS}/flowpilot.png")
big.save(f"{ICONS}/flowpilot.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])

# Inno Setup wizard images (BMP): large side panel 410x797 (scales down cleanly) and small 110x110.
os.makedirs("installer", exist_ok=True)
side = QImage(410, 797, QImage.Format.Format_RGB32)
p = QPainter(side)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
g = QLinearGradient(0, 0, 410, 797)
g.setColorAt(0, QColor("#8F7CF7"))
g.setColorAt(0.6, QColor("#7C6CF2"))
g.setColorAt(1, QColor("#4FB8D6"))
p.fillRect(side.rect(), g)
renderer.render(p, QRectF(85, 170, 240, 240))
p.setPen(QColor("#FFFFFF"))
f = QFont("Inter", 30)
f.setBold(True)
p.setFont(f)
p.drawText(QRectF(0, 440, 410, 60), int(Qt.AlignmentFlag.AlignCenter), "FlowPilot")
f2 = QFont("Inter", 13)
p.setFont(f2)
p.drawText(QRectF(20, 500, 370, 60), int(Qt.AlignmentFlag.AlignCenter), "No-code browser automation")
p.end()
side.save("installer/wizard-large.bmp")
small = QImage(110, 110, QImage.Format.Format_RGB32)
small.fill(QColor("#FFFFFF"))
p = QPainter(small)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
renderer.render(p, QRectF(5, 5, 100, 100))
p.end()
small.save("installer/wizard-small.bmp")
print("rendered")
