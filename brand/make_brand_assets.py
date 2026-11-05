#!/usr/bin/env python3
"""Render every Zirah brand asset from the vector logo.

    python brand/vectorize_logo.py      (once: trace the official sheet)
    python brand/make_brand_assets.py   (render everything below)

Outputs
  ui/assets/symbol.svg, wordmark.svg      vector art the app draws at any DPI
  ui/assets/icon-<n>.png                  app icon, 16 to 1024 pixels
  ui/zirah.ico, brand/zirah.ico           Windows icon, 16 to 256 pixels
  brand/png/                              raster masters (symbol, icon, lockups)
  docs/assets/banner-dark.png, -light.png README banners
  firmware/src/logo_bitmap.h              48 x 48 one-bit symbol for the OLED

The app icon puts the symbol on an Obsidian tile so it stays legible on light
and dark taskbars. Sizes of 48 pixels and below are drawn with thickened
strokes, because the chain links of the full mark would blur into grey at
that scale. Needs PySide6, numpy and Pillow.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "brand"
UI_ASSETS = ROOT / "ui" / "assets"
DOCS = ROOT / "docs" / "assets"
PNG = BRAND / "png"

OBSIDIAN, GRAPHITE, BORDER, STEEL = "#080B10", "#0E141C", "#243140", "#8796A8"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
TAGLINE = "Post-Quantum File Signer"


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return Image.fromarray(arr[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy(), "RGBA")


def render_svg(svg: Path, w: int, h: int) -> Image.Image:
    r = QSvgRenderer(str(svg))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, w, h))
    p.end()
    return qimage_to_pil(img)


def svg_size(svg: Path):
    s = QSvgRenderer(str(svg)).viewBoxF()
    return s.width(), s.height()


def fit(svg: Path, box: int) -> Image.Image:
    """Render an SVG to fit a square box, centred, transparent."""
    w, h = svg_size(svg)
    k = box / max(w, h)
    art = render_svg(svg, max(1, round(w * k)), max(1, round(h * k)))
    out = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    out.alpha_composite(art, ((box - art.width) // 2, (box - art.height) // 2))
    return out


def thicken(art: Image.Image, grow: int) -> Image.Image:
    """Grow the strokes (dilate alpha and colour) before a small downscale."""
    if grow < 3:
        return art
    grow |= 1
    r, g, b, a = art.split()
    a2 = a.filter(ImageFilter.MaxFilter(grow))
    rgb = Image.merge("RGB", (r, g, b)).filter(ImageFilter.MaxFilter(grow))
    return Image.merge("RGBA", (*rgb.split(), a2))


def tile(size: int, master_symbol: Image.Image) -> Image.Image:
    """The app icon: the symbol on a rounded Obsidian tile, rendered at 4x."""
    S = size * 4 if size <= 256 else size
    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(S * 0.02, S * 0.02, S * 0.96, S * 0.96)
    radius = S * 0.22
    grad = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
    grad.setColorAt(0, QColor("#111A24"))
    grad.setColorAt(1, QColor(OBSIDIAN))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, grad)
    if size >= 32:
        pen = QPen(QColor(BORDER))
        pen.setWidthF(max(1.0, S / 128))
        p.setPen(pen)
        p.drawPath(path)
    p.end()
    base = qimage_to_pil(img)
    # Smaller icons give the symbol more of the tile and thicker strokes.
    frac = 0.80 if size >= 64 else 0.86
    grow = {16: 31, 20: 27, 24: 23, 32: 15, 40: 11, 48: 9}.get(size, 0)
    sym = thicken(master_symbol, grow)
    inner = round(S * frac)
    sym = sym.resize((inner, inner), Image.LANCZOS)
    base.alpha_composite(sym, ((S - inner) // 2, (S - inner) // 2))
    return base.resize((size, size), Image.LANCZOS) if S != size else base


def load_fonts():
    for f in ("ChakraPetch-SemiBold.ttf", "IBMPlexSans-Variable.ttf"):
        QFontDatabase.addApplicationFont(str(ROOT / "ui" / "fonts" / f))


def lockup(scale: float, light: bool) -> Image.Image:
    """Horizontal lockup: symbol, then the wordmark over the tagline."""
    sym_svg = BRAND / ("zirah-symbol-light.svg" if light else "zirah-symbol.svg")
    word_svg = BRAND / ("zirah-wordmark-light.svg" if light else "zirah-wordmark.svg")
    sym_h = round(220 * scale)
    word_w, word_h = svg_size(word_svg)
    wh = round(78 * scale)
    ww = round(word_w * wh / word_h)
    tag_px = round(25 * scale)
    gap_sym = round(46 * scale)
    gap_tag = round(14 * scale)
    pad = round(36 * scale)
    W = pad * 2 + sym_h + gap_sym + ww
    H = pad * 2 + sym_h
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    sw, sh = svg_size(sym_svg)
    k = sym_h / max(sw, sh)
    QSvgRenderer(str(sym_svg)).render(p, QRectF(pad + (sym_h - sw * k) / 2, pad + (sym_h - sh * k) / 2,
                                                 sw * k, sh * k))
    block_h = wh + gap_tag + tag_px
    x = pad + sym_h + gap_sym
    y = pad + (sym_h - block_h) / 2
    QSvgRenderer(str(word_svg)).render(p, QRectF(x, y, ww, wh))
    # The tagline is set to exactly the wordmark's width, as in the sheet.
    f = QFont("IBM Plex Sans")
    f.setPixelSize(tag_px)
    try:
        f.setVariableAxis(QFont.Tag("wght"), 500.0)
    except (AttributeError, TypeError):
        pass
    from PySide6.QtGui import QFontMetricsF
    natural = QFontMetricsF(f).horizontalAdvance(TAGLINE)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, (ww - 4 * scale - natural) / (len(TAGLINE) - 1))
    p.setFont(f)
    p.setPen(QColor("#5A6676" if light else STEEL))
    p.drawText(QRectF(x + 2 * scale, y + wh + gap_tag, ww * 1.4, tag_px * 1.6),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, TAGLINE)
    p.end()
    return qimage_to_pil(img)


def oled_header(master: Image.Image, n: int = 48, threshold: int = 96) -> str:
    """One-bit symbol for the 128x64 OLED, from the vector master."""
    a = master.split()[3].resize((n, n), Image.BOX)
    bits = (np.asarray(a) >= threshold).astype(np.uint8)
    rows = []
    for r in range(n):
        row = []
        for c0 in range(0, n, 8):
            b = 0
            for k in range(8):
                if c0 + k < n and bits[r, c0 + k]:
                    b |= 0x80 >> k
            row.append(f"0x{b:02X}")
        rows.append("    " + ", ".join(row) + ",")
    body = "\n".join(rows)
    return ("/* Generated by brand/make_brand_assets.py from the vector logo.\n"
            " * 48x48, 1 bit per pixel, rows top to bottom, MSB first. Do not edit. */\n"
            "#ifndef ZIRAH_LOGO_BITMAP_H\n#define ZIRAH_LOGO_BITMAP_H\n\n#include <stdint.h>\n\n"
            f"#define ZIRAH_LOGO_W {n}\n#define ZIRAH_LOGO_H {n}\n\n"
            f"static const uint8_t zirah_logo_bits[{n * ((n + 7) // 8)}] = {{\n{body}\n}};\n\n#endif\n")


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 (needed for fonts and rendering)
    load_fonts()
    for d in (UI_ASSETS, DOCS, PNG):
        d.mkdir(parents=True, exist_ok=True)
    sym_svg = BRAND / "zirah-symbol.svg"
    for name in ("zirah-symbol.svg", "zirah-wordmark.svg"):
        if not (BRAND / name).exists():
            sys.exit(f"brand/{name} is missing: run brand/vectorize_logo.py first")

    # Vectors used directly by the app.
    shutil.copyfile(sym_svg, UI_ASSETS / "symbol.svg")
    shutil.copyfile(BRAND / "zirah-wordmark.svg", UI_ASSETS / "wordmark.svg")

    master = fit(sym_svg, 1024)
    master.save(PNG / "zirah-symbol-1024.png", optimize=True)
    fit(BRAND / "zirah-symbol-light.svg", 1024).save(PNG / "zirah-symbol-light-1024.png", optimize=True)

    icons = {s: tile(s, master) for s in ICON_SIZES + (512, 1024)}
    for s, im in icons.items():
        im.save(UI_ASSETS / f"icon-{s}.png", optimize=True)
    icons[1024].save(PNG / "zirah-icon-1024.png", optimize=True)
    order = list(ICON_SIZES)
    for target in (BRAND / "zirah.ico", ROOT / "ui" / "zirah.ico"):
        icons[256].save(target, sizes=[(s, s) for s in order],
                        append_images=[icons[s] for s in order if s != 256])

    dark, light = lockup(1.0, False), lockup(1.0, True)
    dark.save(DOCS / "banner-dark.png", optimize=True)
    light.save(DOCS / "banner-light.png", optimize=True)
    lockup(2.0, False).save(PNG / "zirah-lockup-dark@2x.png", optimize=True)
    lockup(2.0, True).save(PNG / "zirah-lockup-light@2x.png", optimize=True)

    # 768-pixel master, box-filtered to 48 and cut at 96/255: the most legible
    # rings and particles of the thresholds and stroke weights compared.
    (ROOT / "firmware" / "src" / "logo_bitmap.h").write_text(oled_header(fit(sym_svg, 768)))
    print("brand assets written")


if __name__ == "__main__":
    main()
