#!/usr/bin/env python3
"""Compose docs/assets/zirah-app.png from a real screenshot of the app.

    python tests/ui/app_hero.py [docs/screens/03-press-user.png] [output.png]

The window content is the untouched capture from tests/ui/render_screens.py
(Simulation Mode, so the ribbon and simulated markings are visible). This
script only frames it: a dark Windows 11 style title bar like the one the app
sets, rounded corners, a soft shadow and a caption that says what it shows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage,  # noqa: E402
                           QPainter, QPainterPath, QPen, QRadialGradient)

ROOT = Path(__file__).resolve().parents[2]


def font(family, px, weight):
    f = QFont(family)
    f.setPixelSize(px)
    try:
        f.setVariableAxis(QFont.Tag("wght"), float(weight))
    except (AttributeError, TypeError):
        pass
    f.setWeight(QFont.Weight(weight))
    return f


def main() -> int:
    shot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screens" / "03-press-user.png"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "assets" / "zirah-app.png"
    app = QGuiApplication(sys.argv)  # noqa: F841
    for f in ("ChakraPetch-SemiBold.ttf", "IBMPlexSans-Variable.ttf"):
        QFontDatabase.addApplicationFont(str(ROOT / "ui" / "fonts" / f))
    shot = QImage(str(shot_path))
    icon = QImage(str(ROOT / "ui" / "assets" / "icon-64.png"))
    bar = 38
    pad_x, pad_top, pad_bottom = 110, 96, 120
    ww, wh = shot.width(), shot.height() + bar
    W, H = ww + 2 * pad_x, wh + pad_top + pad_bottom
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Backdrop: Obsidian with a faint cyan bloom behind the window.
    p.fillRect(img.rect(), QColor("#05070A"))
    glow = QRadialGradient(QPointF(W * 0.5, H * 0.42), W * 0.55)
    glow.setColorAt(0, QColor(89, 225, 232, 34))
    glow.setColorAt(1, QColor(89, 225, 232, 0))
    p.fillRect(img.rect(), glow)

    win = QRectF(pad_x, pad_top, ww, wh)
    # Shadow: a few stacked translucent rounded rects.
    for i in range(18, 0, -1):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 9))
        p.drawRoundedRect(win.adjusted(-i, -i + 10, i, i + 14), 12 + i, 12 + i)
    clip = QPainterPath()
    clip.addRoundedRect(win, 10, 10)
    p.save()
    p.setClipPath(clip)
    p.fillRect(QRectF(win.left(), win.top(), ww, bar), QColor("#080B10"))
    p.drawImage(QRectF(win.left() + 12, win.top() + 9, 20, 20), icon)
    p.setPen(QColor("#C9D3DC"))
    p.setFont(font("IBM Plex Sans", 13, 400))
    p.drawText(QRectF(win.left() + 42, win.top(), 300, bar), Qt.AlignmentFlag.AlignVCenter, "Zirah")
    # Minimise, maximise, close glyphs.
    p.setPen(QPen(QColor("#8796A8"), 1.2))
    x = win.right() - 138
    cy = win.top() + bar / 2
    p.drawLine(QPointF(x, cy), QPointF(x + 10, cy))
    p.drawRect(QRectF(x + 46, cy - 5, 10, 10))
    p.drawLine(QPointF(x + 92, cy - 5), QPointF(x + 102, cy + 5))
    p.drawLine(QPointF(x + 102, cy - 5), QPointF(x + 92, cy + 5))
    p.drawImage(QPointF(win.left(), win.top() + bar), shot)
    p.restore()
    p.setPen(QPen(QColor("#243140"), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(win, 10, 10)

    p.setPen(QColor("#EAF2F7"))
    p.setFont(font("Chakra Petch", 30, 600))
    p.drawText(QRectF(pad_x, 30, W, 40), Qt.AlignmentFlag.AlignLeft, "Zirah Desktop App")
    p.setPen(QColor("#8796A8"))
    p.setFont(font("IBM Plex Sans", 16, 400))
    p.drawText(QRectF(pad_x, H - pad_bottom + 44, W - 2 * pad_x, 26), Qt.AlignmentFlag.AlignLeft,
               "Captured From the Running Program in Simulation Mode: No Hardware Was Attached, "
               "and the App Marks Every Simulated Result")
    p.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out))
    print(f"wrote {out} {W}x{H}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
