#!/usr/bin/env python3
"""Build docs/assets/oled-screens.png from the firmware's own OLED renders.

    python tests/native/oled_sheet.py <folder with .fb frames> [output.png]

The frames come from tests/native/oled_preview.c, which runs the unchanged
firmware drawing code on a PC. Each panel is drawn like the real 1.3 inch
white OLED. The noise trace in the Ready screen uses simulated samples, and
the sheet says so.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCREENS = [("01_boot", "Boot and Self-Test"), ("03_ready", "Ready with Live Noise"),
           ("15_confirm_sign", "Awaiting USER"), ("21_sign_mldsa", "Masked ML-DSA-65"),
           ("28_signed_hybrid", "Signed, Both Halves"), ("08_fault_bias", "Entropy Fault"),
           ("12_locked", "Signing Locked"), ("34_analyze", "Noise Analysis")]
PX = 5           # screen pixel pitch in the sheet
DOT = 4.4        # lit area of one pixel; the SH1106 gaps are narrow
INK = QColor("#ECF4FA")


def font(family: str, px: int, weight: int) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    try:
        f.setVariableAxis(QFont.Tag("wght"), float(weight))
    except (AttributeError, TypeError):
        pass
    return f


def main() -> int:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "assets" / "oled-screens.png"
    app = QGuiApplication(sys.argv)  # noqa: F841
    for f in ("ChakraPetch-SemiBold.ttf", "IBMPlexSans-Variable.ttf"):
        QFontDatabase.addApplicationFont(str(ROOT / "ui" / "fonts" / f))

    pw, ph = 128 * PX, 64 * PX
    bez = 22
    cols, rows = 4, 2
    gap_x, gap_y = 44, 96
    margin, head = 72, 186
    W = margin * 2 + cols * (pw + 2 * bez) + (cols - 1) * gap_x
    H = head + rows * (ph + 2 * bez) + (rows - 1) * gap_y + 92
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#080B10"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    p.setPen(QColor("#EAF2F7"))
    p.setFont(font("Chakra Petch", 54, 600))
    p.drawText(QRectF(margin, 48, W, 66), Qt.AlignmentFlag.AlignLeft, "Zirah OLED Screens")
    p.setPen(QColor("#8796A8"))
    p.setFont(font("IBM Plex Sans", 27, 400))
    p.drawText(QRectF(margin, 122, W, 38), Qt.AlignmentFlag.AlignLeft,
               "Rendered by the Firmware's Own Drawing Code for the 128 by 64 SH1106 Panel, "
               "with Simulated Samples in the Noise Trace")

    for i, (name, title) in enumerate(SCREENS):
        fb = (src / f"{name}.fb").read_bytes()
        c, r = i % cols, i // cols
        x = margin + c * (pw + 2 * bez + gap_x)
        y = head + r * (ph + 2 * bez + gap_y)
        # Module glass with a thin bezel.
        p.setPen(QPen(QColor("#243140"), 1.5))
        p.setBrush(QColor("#030405"))
        p.drawRoundedRect(QRectF(x, y, pw + 2 * bez, ph + 2 * bez), 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(INK)
        ox, oy = x + bez, y + bez
        for page in range(8):
            for col in range(128):
                b = fb[page * 128 + col]
                if not b:
                    continue
                for bit in range(8):
                    if b & (1 << bit):
                        p.drawRect(QRectF(ox + col * PX, oy + (page * 8 + bit) * PX, DOT, DOT))
        p.setPen(QColor("#EAF2F7"))
        p.setFont(font("IBM Plex Sans", 27, 600))
        p.drawText(QRectF(x, y + ph + 2 * bez + 16, pw + 2 * bez, 40), Qt.AlignmentFlag.AlignLeft, title)
    p.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
