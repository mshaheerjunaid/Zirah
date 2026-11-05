#!/usr/bin/env python3
"""Trace the official Zirah logo sheet into clean vector artwork.

    python brand/vectorize_logo.py

Input:  brand/source/zirah-logo-sheet.jpg (the official sheet)
Output: brand/zirah-symbol.svg        symbol, metallic finish, for dark backgrounds
        brand/zirah-symbol-light.svg  symbol in dark ink, for light backgrounds
        brand/zirah-wordmark.svg      the ZIRAH wordmark
        brand/zirah-wordmark-light.svg

Each element is traced at four times the sheet's resolution with potrace,
so edges become smooth Bezier curves instead of JPEG pixels. Strokes and the
cyan particles are traced separately and keep their own colours. Every
raster asset (app icons, .ico, banners, the OLED bitmap) is rendered from
these files by brand/make_brand_assets.py.

Needs numpy, scipy, Pillow and potracer (pip install potracer).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import potrace
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "source" / "zirah-logo-sheet.jpg"
OUT = ROOT / "brand"

SCALE = 4                       # trace resolution relative to the sheet
SYMBOL_BOX = (0, 613, 0, 579)   # y0, y1, x0, x1 of the large symbol panel
LOCKUP_BOX = (619, 1024, 0, 1024)

# Colours sampled from the sheet: the strokes run from a bright silver at
# the top left to a cooler grey at the bottom right; the particles are the
# interface cyan.
SILVER = ("#F3F6F9", "#D3DBE2", "#AEB9C3")
WORD = ("#FFFFFF", "#DCE3EA")
CYAN = "#59E1E8"
INK_LIGHT = "#18212C"
CYAN_LIGHT = "#1597A1"


def coverage(panel: np.ndarray) -> np.ndarray:
    """0..1 ink coverage against the sheet's flat background."""
    frame = np.concatenate([panel[:10].reshape(-1, 3), panel[:, :10].reshape(-1, 3)])
    bg = np.median(frame, axis=0)
    return np.clip(np.max((panel - bg) / (255.0 - bg), axis=2), 0, 1)


def cyan_mask(panel: np.ndarray) -> np.ndarray:
    r, g, b = panel[..., 0], panel[..., 1], panel[..., 2]
    return ((g + b) / 2 - r) > 40


def to_bitmap(cov: np.ndarray, threshold: float = 0.42, blur: float = 1.1) -> np.ndarray:
    """Upsample the coverage map, smooth away JPEG noise and threshold."""
    img = Image.fromarray((cov * 255).astype(np.uint8), "L")
    big = img.resize((img.width * SCALE, img.height * SCALE), Image.LANCZOS)
    a = np.asarray(big).astype(float) / 255.0
    a = ndimage.gaussian_filter(a, blur * SCALE / 2)
    bm = a > threshold
    lab, n = ndimage.label(bm)
    if n:
        sizes = ndimage.sum(bm, lab, index=np.arange(1, n + 1))
        for i, s in enumerate(sizes, start=1):
            if s < 30 * SCALE * SCALE:
                bm[lab == i] = False
    return bm


def trace(bm: np.ndarray, ox: float, oy: float, alphamax: float = 1.0) -> str:
    """potrace -> SVG path data in sheet pixel units, offset by (ox, oy)."""
    # potracer treats dark pixels as ink (it inverts its input), so pass the
    # complement of the ink mask.
    paths = potrace.Bitmap(~bm).trace(turdsize=8, turnpolicy=potrace.POTRACE_TURNPOLICY_MINORITY,
                                     alphamax=alphamax, opticurve=True, opttolerance=0.2)
    k = 1.0 / SCALE

    def pt(p):
        return f"{p.x * k - ox:.2f} {p.y * k - oy:.2f}"

    out = []
    for curve in paths:
        out.append("M" + pt(curve.start_point))
        for seg in curve.segments:
            if seg.is_corner:
                out.append("L" + pt(seg.c) + "L" + pt(seg.end_point))
            else:
                out.append("C" + pt(seg.c1) + " " + pt(seg.c2) + " " + pt(seg.end_point))
        out.append("Z")
    return "".join(out)


def bbox(mask: np.ndarray, pad: int):
    ys, xs = np.nonzero(mask)
    return (max(xs.min() - pad, 0), max(ys.min() - pad, 0), xs.max() + pad + 1, ys.max() + pad + 1)


def svg(w: float, h: float, body: str, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {h:.2f}" '
            f'width="{w:.0f}" height="{h:.0f}">\n'
            f'<defs>{defs}</defs>\n{body}\n</svg>\n')


def gradient(gid: str, stops, x2=1, y2=1) -> str:
    s = "".join(f'<stop offset="{i / (len(stops) - 1):.2f}" stop-color="{c}"/>' for i, c in enumerate(stops))
    return f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">{s}</linearGradient>'


def main() -> None:
    sheet = np.asarray(Image.open(SRC).convert("RGB")).astype(float)

    # Symbol: strokes and particles traced separately.
    y0, y1, x0, x1 = SYMBOL_BOX
    p = sheet[y0:y1, x0:x1]
    cov = coverage(p)
    cy = ndimage.binary_dilation(cyan_mask(p) & (cov > 0.15), iterations=1)
    stroke_cov = np.where(cy, 0.0, cov)
    cyan_cov = np.where(cy, cov, 0.0)
    bx0, by0, bx1, by1 = bbox(cov > 0.3, 6)
    sb = to_bitmap(stroke_cov)
    cb = to_bitmap(cyan_cov, threshold=0.38, blur=0.3)
    stroke_d = trace(sb, bx0, by0)
    cyan_d = trace(cb, bx0, by0, alphamax=0.45)   # keep the particles square
    w, h = bx1 - bx0, by1 - by0
    for name, defs, fs, fc in (
            ("zirah-symbol.svg", gradient("silver", SILVER), "url(#silver)", CYAN),
            ("zirah-symbol-light.svg", "", INK_LIGHT, CYAN_LIGHT)):
        body = (f'<path fill="{fs}" fill-rule="evenodd" d="{stroke_d}"/>\n'
                f'<path fill="{fc}" fill-rule="evenodd" d="{cyan_d}"/>')
        (OUT / name).write_text(svg(w, h, body, defs))

    # Wordmark: the letters of the horizontal lockup, above the tagline.
    y0, y1, x0, x1 = LOCKUP_BOX
    p = sheet[y0:y1, x0:x1]
    cov = coverage(p)
    cols = (cov > 0.3).any(axis=0)
    run, split = 0, None
    for i, c in enumerate(cols):
        run = run + 1 if not c else 0
        if run >= 18 and i > 300:
            split = i
            break
    rows_ink = np.nonzero((cov[:, split:] > 0.3).any(axis=1))[0]
    gap = next(a + 1 for a, b in zip(rows_ink, rows_ink[1:]) if b - a > 12)
    word = np.zeros_like(cov)
    word[rows_ink[0]:gap, split:] = cov[rows_ink[0]:gap, split:]
    wx0, wy0, wx1, wy1 = bbox(word > 0.3, 4)
    wd = trace(to_bitmap(word, threshold=0.45, blur=0.9), wx0, wy0)
    ww, wh = wx1 - wx0, wy1 - wy0
    (OUT / "zirah-wordmark.svg").write_text(
        svg(ww, wh, f'<path fill="url(#word)" fill-rule="evenodd" d="{wd}"/>', gradient("word", WORD, 0, 1)))
    (OUT / "zirah-wordmark-light.svg").write_text(
        svg(ww, wh, f'<path fill="{INK_LIGHT}" fill-rule="evenodd" d="{wd}"/>'))
    print(f"symbol {w}x{h}, wordmark {ww}x{wh}, path bytes {len(stroke_d) + len(cyan_d) + len(wd):,}")


if __name__ == "__main__":
    main()
