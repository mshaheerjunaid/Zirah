"""Custom painted widgets of the Zirah desktop app.

Every graphic here draws only what it is given. The signal window draws the
device's real samples (or the simulator's, with a watermark); with no
samples it says No Signal. The stage rail lights a stage only when the
device reports it. Nothing animates data into existence.
"""
from __future__ import annotations

import math
import time
from collections import deque
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (Property, QEasingCurve, QLineF, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPixmap, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from PySide6.QtSvg import QSvgRenderer

import theme as th
from theme import Motion, T, font, qc


# ---------------------------------------------------------------------------
# A shared animation clock: widgets that need continuous motion subscribe,
# and the timer only runs while someone is subscribed.
# ---------------------------------------------------------------------------

class Clock:
    _subs: set = set()
    _timer: Optional[QTimer] = None

    @classmethod
    def subscribe(cls, w: QWidget) -> None:
        cls._subs.add(w)
        if cls._timer is None:
            cls._timer = QTimer()
            cls._timer.setInterval(16)           # about 60 frames a second, like Qt's own animations
            cls._timer.timeout.connect(cls._tick)
        if not cls._timer.isActive():
            cls._timer.start()

    @classmethod
    def unsubscribe(cls, w: QWidget) -> None:
        cls._subs.discard(w)
        if not cls._subs and cls._timer is not None:
            cls._timer.stop()

    @classmethod
    def _tick(cls) -> None:
        for w in list(cls._subs):
            try:
                w.update()
            except RuntimeError:  # widget deleted
                cls._subs.discard(w)


def painter(w: QWidget) -> QPainter:
    p = QPainter(w)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    return p


def fade_in(w: QWidget, ms: int = 220, offset: int = 0) -> None:
    """Fade (and optionally lift) a widget into view after a person's action."""
    ms = Motion.ms(ms)
    if ms == 0:
        w.setGraphicsEffect(None)
        return
    eff = QGraphicsOpacityEffect(w)
    w.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", w)
    a.setDuration(ms)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.Type.OutCubic)
    a.finished.connect(lambda: w.setGraphicsEffect(None))
    a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class _Crossfade(QWidget):
    """An opaque overlay that blends a snapshot of the old view into the new one.

    Both views are captured once, so each animation frame is two image
    copies instead of a full repaint of every widget on the page; a
    QGraphicsOpacityEffect would re-render the whole page every frame.
    """

    def __init__(self, host: QWidget, old: QPixmap, new: QPixmap, ms: int, lift: float):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.old, self.new, self.lift = old, new, lift
        self._k = 0.0
        self.setGeometry(host.rect())
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._done)
        self.show()
        self.raise_()
        self._anim.start()

    def _step(self, v):
        self._k = float(v)
        self.update()

    def _done(self):
        self.hide()
        self.deleteLater()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(th.GRAPHITE))
        k = self._k
        p.drawPixmap(QPointF(0, self.lift * (1.0 - k)), self.new)
        if k < 1.0:
            p.setOpacity(1.0 - k)
            p.drawPixmap(QPointF(0, 0), self.old)
        p.end()


def crossfade(host: QWidget, switch, ms: int = 220, lift: float = 8.0) -> None:
    """Run switch() (which changes what host shows) behind a short cross-fade."""
    ms = Motion.ms(ms)
    if ms == 0 or not host.isVisible() or host.width() < 2 or host.height() < 2:
        switch()
        return
    for child in host.findChildren(_Crossfade):     # a fade still running ends at once
        child.hide()
        child.deleteLater()
    old = host.grab()
    switch()
    if host.layout() is not None:
        host.layout().activate()
    new = host.grab()
    _Crossfade(host, old, new, ms, lift)


class KeyboardFocus:
    """Focus rings only after keyboard navigation (the :focus-visible rule),
    so a click or the window opening never leaves a ring behind."""

    _kbd = False

    def focusInEvent(self, e):
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                   Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kbd = False
        super().focusOutEvent(e)

    def focus_ring(self) -> bool:
        return self.hasFocus() and self._kbd


# ---------------------------------------------------------------------------
# Brand lockup: the vector symbol beside the wordmark, the tagline set to the
# wordmark's exact width beneath it. Painted from SVG, so it is sharp at any
# display scale.
# ---------------------------------------------------------------------------

ASSETS = Path(__file__).resolve().parent / "assets"


class BrandLockup(QWidget):
    def __init__(self, tagline: str, height: int = 64, parent=None):
        super().__init__(parent)
        self.symbol = QSvgRenderer(str(ASSETS / "symbol.svg"))
        self.word = QSvgRenderer(str(ASSETS / "wordmark.svg"))
        self.tagline = tagline
        self.setFixedHeight(height)
        self.setMinimumWidth(220)
        self.setAccessibleName("Zirah, " + tagline)

    def _tag_font(self, width: float) -> QFont:
        f = QFont(th.SANS)
        f.setPixelSize(12)
        try:
            f.setVariableAxis(QFont.Tag("wght"), 500.0)
        except (AttributeError, TypeError):
            pass
        natural = QFontMetricsF(f).horizontalAdvance(self.tagline)
        spacing = (width - natural) / max(len(self.tagline) - 1, 1)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, max(-0.4, min(spacing, 1.6)))
        return f

    def paintEvent(self, e):
        p = painter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        H = float(self.height())
        sv = self.symbol.viewBoxF()
        k = H / max(sv.width(), sv.height())
        self.symbol.render(p, QRectF((H - sv.width() * k) / 2, (H - sv.height() * k) / 2,
                                     sv.width() * k, sv.height() * k))
        x = H + round(H * 0.25)
        wv = self.word.viewBoxF()
        avail = self.width() - x - 2
        wh = min(H * 0.50, avail * wv.height() / wv.width())
        ww = wh * wv.width() / wv.height()
        f = self._tag_font(ww)
        fm = QFontMetricsF(f)
        gap = 6.0
        block = wh + gap + fm.capHeight()
        y = (H - block) / 2
        self.word.render(p, QRectF(x, y, ww, wh))
        p.setFont(f)
        p.setPen(QColor(th.STEEL))
        p.drawText(QPointF(x + 1, y + wh + gap + fm.capHeight()), self.tagline)
        p.end()


# ---------------------------------------------------------------------------
# Connection lamp
# ---------------------------------------------------------------------------

class Lamp(QWidget):
    COLORS = {"off": th.STEEL, "search": th.CYAN, "ready": th.SUCCESS, "warn": th.WARNING,
              "busy": th.CYAN, "error": th.ERROR, "sim": th.WARNING}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self._mode = "off"
        self._t0 = time.monotonic()

    def set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._t0 = time.monotonic()
        if mode in ("search", "busy", "warn") and not Motion.reduce:
            Clock.subscribe(self)
        else:
            Clock.unsubscribe(self)
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        c = self.COLORS.get(self._mode, th.STEEL)
        t = time.monotonic() - self._t0
        level = 1.0
        if not Motion.reduce:
            if self._mode == "search":
                level = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.6))
            elif self._mode == "warn":
                level = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 2.6))
        if self._mode == "off":
            level = 0.45
        cx, cy = self.width() / 2, self.height() / 2
        glow = QRadialGradient(QPointF(cx, cy), 16)
        glow.setColorAt(0.0, qc(c, 0.55 * level))
        glow.setColorAt(1.0, qc(c, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        if self._mode != "off":
            p.setBrush(QBrush(glow))
            p.drawEllipse(QPointF(cx, cy), 16, 16)
        p.setBrush(qc(c, level))
        p.drawEllipse(QPointF(cx, cy), 6, 6)
        if self._mode == "busy":
            pen = QPen(qc(th.CYAN, 0.9), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = 0 if Motion.reduce else int(-t * 360 * 16) % (360 * 16)
            p.drawArc(QRectF(cx - 11, cy - 11, 22, 22), start, 100 * 16)
        p.end()


# ---------------------------------------------------------------------------
# Buttons with animated hover and press
# ---------------------------------------------------------------------------

class Button(KeyboardFocus, QPushButton):
    KINDS = {
        #           fill        text        border      hover fill
        "primary": (th.CYAN, th.OBSIDIAN, th.CYAN, "#86ECF0"),
        "secondary": (th.SLATE, th.ICE, th.BORDER, "#1B2735"),
        "ghost": (None, th.STEEL, None, th.SLATE),
        "danger": (th.SLATE, th.ERROR, "#5A2A33", "#24161C"),
        "blue": (th.SLATE, th.BLUE, "#2E3F73", "#18213A"),
    }

    def __init__(self, text: str = "", kind: str = "secondary", parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self.setFont(font("button"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumHeight(38)
        self._hover = 0.0
        self._press = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._set_hover)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        if Motion.reduce:
            self._set_hover(target)
            return
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._press = 1.0
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = 0.0
        self.update()
        super().mouseReleaseEvent(e)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(int(fm.horizontalAdvance(self.text()) + 36), 38)

    def paintEvent(self, e):
        p = painter(self)
        fill, text, border, hover = self.KINDS[self.kind]
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._press:
            r = r.adjusted(1, 1, -1, -1)
        enabled = self.isEnabled()
        bg = None
        edge = None
        if not enabled:
            if fill:
                bg, edge = qc(th.SLATE, 0.6), qc(th.BORDER, 0.8)
        else:
            if fill:
                bg = th.mix(fill, hover, self._hover)
            elif self._hover > 0:
                bg = qc(hover, self._hover)
            if border:
                edge = QColor(border)
        p.setBrush(bg if bg is not None else Qt.BrushStyle.NoBrush)
        p.setPen(QPen(edge, 1) if edge is not None else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 6, 6)
        if self.focus_ring():
            p.setPen(QPen(qc(th.CYAN, 0.9), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        tc = QColor(text) if enabled else qc(th.STEEL, 0.55)
        if self.kind == "ghost" and enabled:
            tc = th.mix(th.STEEL, th.ICE, self._hover)
        p.setPen(tc)
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


# ---------------------------------------------------------------------------
# Toggle switch
# ---------------------------------------------------------------------------

class Toggle(KeyboardFocus, QAbstractButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFont(font("body"))
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.toggled.connect(self._on_toggled)
        self.setAccessibleName(text)

    def _get_knob(self):
        return self._pos

    def _set_knob(self, v):
        self._pos = float(v)
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _on_toggled(self, on: bool) -> None:
        self._anim.stop()
        if Motion.reduce:
            self._set_knob(1.0 if on else 0.0)
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set_quiet(self, on: bool) -> None:
        """Reflect state from the device without firing toggled()."""
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._set_knob(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(int(46 + fm.horizontalAdvance(self.text()) + 4), 30)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, e):
        p = painter(self)
        h = self.height()
        tw, th_ = 38.0, 22.0
        track = QRectF(1, (h - th_) / 2, tw, th_)
        t = max(0.0, min(1.0, self._pos))
        on = self.isEnabled()
        col = th.mix(th.SLATE, "#1E4A52", t)
        p.setPen(QPen(th.mix(th.BORDER, th.CYAN, t) if on else qc(th.BORDER, 0.6), 1))
        p.setBrush(col if on else qc(th.SLATE, 0.6))
        p.drawRoundedRect(track, th_ / 2, th_ / 2)
        kx = track.left() + 3 + (tw - 6 - 16) * self._pos
        knob = QRectF(kx, track.top() + 3, 16, 16)
        if t > 0.02 and on:
            glow = QRadialGradient(knob.center(), 14)
            glow.setColorAt(0, qc(th.CYAN, 0.35 * t))
            glow.setColorAt(1, qc(th.CYAN, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(knob.center(), 14, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.mix(th.STEEL, th.ICE, t) if on else qc(th.STEEL, 0.5))
        p.drawEllipse(knob)
        if self.focus_ring():
            p.setPen(QPen(qc(th.CYAN, 0.9), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-3, -3, 3, 3), th_ / 2 + 3, th_ / 2 + 3)
        if self.text():
            p.setPen(QColor(th.ICE) if on else qc(th.STEEL, 0.7))
            p.setFont(self.font())
            p.drawText(QRectF(tw + 12, 0, self.width() - tw - 12, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


# ---------------------------------------------------------------------------
# Tabs with a sliding indicator
# ---------------------------------------------------------------------------

class TabStrip(KeyboardFocus, QWidget):
    changed = Signal(int)

    def __init__(self, labels: List[str], parent=None):
        super().__init__(parent)
        self.labels = labels
        self.index = 0
        self._hover = -1
        self._x = 0.0
        self._w = 0.0
        self.setFont(font("tab"))
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setAccessibleName("Tabs")

    def _rects(self) -> List[QRectF]:
        fm = QFontMetricsF(self.font())
        x = 0.0
        out = []
        for lab in self.labels:
            w = fm.horizontalAdvance(lab) + 34
            out.append(QRectF(x, 0, w, self.height()))
            x += w + 4
        return out

    def _on_anim(self, v):
        self._x, self._w = v.x(), v.y()
        self.update()

    def set_index(self, i: int, animate: bool = True) -> None:
        if i < 0 or i >= len(self.labels):
            return
        r = self._rects()[i]
        target = QPointF(r.x() + 12, r.width() - 24)
        self._anim.stop()
        if animate and not Motion.reduce and self._w > 0:
            self._anim.setStartValue(QPointF(self._x, self._w))
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._x, self._w = target.x(), target.y()
        changed = i != self.index
        self.index = i
        self.update()
        if changed:
            self.changed.emit(i)

    def showEvent(self, e):
        self.set_index(self.index, animate=False)
        super().showEvent(e)

    def mouseMoveEvent(self, e):
        hov = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if hov != self._hover:
            self._hover = hov
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        for i, r in enumerate(self._rects()):
            if r.contains(e.position()):
                self.set_index(i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.set_index((self.index + 1) % len(self.labels))
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.set_index((self.index - 1) % len(self.labels))
        else:
            super().keyPressEvent(e)

    def sizeHint(self) -> QSize:
        rs = self._rects()
        return QSize(int(rs[-1].right()) if rs else 100, 46)

    def paintEvent(self, e):
        p = painter(self)
        p.setFont(self.font())
        for i, (lab, r) in enumerate(zip(self.labels, self._rects())):
            if i == self.index:
                col = QColor(th.ICE)
            elif i == self._hover:
                col = th.mix(th.STEEL, th.ICE, 0.6)
            else:
                col = QColor(th.STEEL)
            p.setPen(col)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, lab)
        # Sliding indicator with a short glow.
        bar = QRectF(self._x, self.height() - 3, self._w, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(th.CYAN))
        p.drawRoundedRect(bar, 1.5, 1.5)
        g = QLinearGradient(0, self.height() - 14, 0, self.height())
        g.setColorAt(0, qc(th.CYAN, 0))
        g.setColorAt(1, qc(th.CYAN, 0.18))
        p.setBrush(QBrush(g))
        p.drawRect(QRectF(self._x, self.height() - 14, self._w, 11))
        if self.focus_ring():
            r = self._rects()[self.index]
            p.setPen(QPen(qc(th.CYAN, 0.7), 1.2, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(4, 6, -4, -6), 6, 6)
        p.end()


# ---------------------------------------------------------------------------
# Signal window: the device's raw noise, with phosphor persistence
# ---------------------------------------------------------------------------

class Scope(QWidget):
    """The latest block of raw samples, drawn like a phosphor oscilloscope.

    A beam sweeps each new block in from the left at a slow, constant speed,
    the way a real scope's trace is written, while the previous block stays
    in place to the right of the beam until the beam reaches it. Blocks that
    arrive mid-sweep wait for the beam to finish, so the motion never jumps,
    and the next sweep starts as soon as one does. Older blocks fade out
    gradually through a persistence layer, a few small steps per sweep, so no
    frame dims all at once.

    Drawing 512 translucent anti-aliased segments costs Qt's raster engine
    over 100 ms, so nothing expensive happens per frame. Each block is drawn
    once, with opaque pens, into its own image, as soon as it arrives when it
    has to wait for the beam, so a sweep start only swaps images; the grid,
    the persistence and the background share one opaque image; and a frame
    of the sweep repaints only the narrow strip around the beam and the
    histogram, which takes well under a millisecond.
    """

    HIST_W = 150
    PAD_L = 64
    BINS = 40
    SWEEP_MS = 1450       # one block per sweep; the device sends one about every 0.9 s
    FADE = 0.55           # how much of the persistence image survives one sweep
    FADE_STEPS = 6        # the fade is applied in this many even steps per sweep
    GHOST = 0.42          # opacity a block keeps when it becomes the previous trace
    GLOW_W = 46           # width of the glow trailing the beam

    shown = Signal(dict)  # a block's sweep has started; the readouts follow it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.history: deque = deque(maxlen=6)    # (samples, mean, sd), newest last
        self.block: Optional[dict] = None
        self._pending: Optional[dict] = None
        self._pending_pre: Optional[dict] = None   # the waiting block, already drawn
        self.reason = T("scope.disconnected")
        self.simulated = False
        self._sweep = 1.0
        self._x_last = 0.0
        self._fades_done = 0
        self._intro = 1.0
        self._hist = [0.0] * self.BINS
        self._hist_from = [0.0] * self.BINS
        self._hist_target = [0.0] * self.BINS
        self._grid_img: Optional[QImage] = None
        self._grid_key = None
        self._trace_img: Optional[QImage] = None
        self._prev_img: Optional[QImage] = None     # the block the beam is writing over
        self._persist_img: Optional[QImage] = None
        self._base_img: Optional[QImage] = None    # opaque: background, grid and ghosts in one copy
        self._layers_key = None
        self._sweep_anim = QVariantAnimation(self)
        self._sweep_anim.setDuration(self.SWEEP_MS)
        self._sweep_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._sweep_anim.valueChanged.connect(self._on_sweep)
        self._sweep_anim.finished.connect(self._on_sweep_done)
        self._intro_anim = QVariantAnimation(self)
        self._intro_anim.setDuration(900)
        self._intro_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro_anim.valueChanged.connect(self._on_intro)
        self.setAccessibleName(T("scope.title"))

    # -- geometry -----------------------------------------------------------

    def _plot(self) -> QRectF:
        W, H = self.width(), self.height()
        return QRectF(self.PAD_L, 14, max(10.0, W - self.PAD_L - self.HIST_W - 26), max(10.0, H - 40))

    def _hist_rect(self, plot: QRectF) -> QRectF:
        return QRectF(plot.right() + 14, plot.top(), self.HIST_W, plot.height())

    def _beam_x(self, plot: QRectF) -> float:
        return plot.left() + (plot.width() + 4) * self._sweep

    def _dpr(self) -> float:
        return max(1.0, float(self.devicePixelRatioF()))

    def _new_image(self) -> QImage:
        d = self._dpr()
        img = QImage(max(1, int(self.width() * d)), max(1, int(self.height() * d)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(d)
        img.fill(Qt.GlobalColor.transparent)
        return img

    # -- animation ----------------------------------------------------------

    def intro(self) -> None:
        if Motion.reduce:
            return
        self._intro_anim.setStartValue(0.0)
        self._intro_anim.setEndValue(1.0)
        self._intro_anim.start()

    def _on_intro(self, v):
        self._intro = float(v)
        self.update()

    def _on_sweep(self, v):
        self._sweep = float(v)
        # The histogram settles over the same sweep, gently at both ends.
        k = 0.5 - 0.5 * math.cos(math.pi * self._sweep)
        self._hist = [a + (b - a) * k for a, b in zip(self._hist_from, self._hist_target)]
        plot = self._plot()
        # The persistence fades in a few even steps, so no single frame dims much.
        step = min(self.FADE_STEPS, int(self._sweep * self.FADE_STEPS + 1e-9))
        faded = False
        while self._fades_done < step:
            self._fade_persist(None, self.FADE ** (1.0 / self.FADE_STEPS))
            self._fades_done += 1
            faded = True
        if faded:
            self._base_img = None
            self.update(plot.adjusted(-2, -2, 2, 2).toAlignedRect())
        else:
            # Only the strip the beam and its glow moved through needs drawing.
            x = self._beam_x(plot)
            lo = min(x, self._x_last) - self.GLOW_W - 3
            hi = max(x, self._x_last) + 4
            self.update(QRectF(lo, plot.top() - 2, hi - lo, plot.height() + 4).toAlignedRect())
        self._x_last = self._beam_x(plot)
        self.update(self._hist_rect(plot).adjusted(0, -2, 0, 2).toAlignedRect())

    def _on_sweep_done(self) -> None:
        self._prev_img = None
        self.update()
        if self._pending is not None:
            block, pre = self._pending, self._pending_pre
            self._pending = self._pending_pre = None
            self._start(block, pre)

    # -- data ---------------------------------------------------------------

    def clear(self, reason: str) -> None:
        changed = self.block is not None or reason != self.reason
        self.block = None
        self._pending = None
        self._pending_pre = None
        self.history.clear()
        self.reason = reason
        self._hist = [0.0] * self.BINS
        self._hist_from = list(self._hist)
        self._hist_target = list(self._hist)
        self._trace_img = None
        self._prev_img = None
        self._persist_img = None
        self._base_img = None
        self._layers_key = None
        self._sweep_anim.stop()
        self._sweep = 1.0
        if changed:
            self.update()

    def push(self, block: dict) -> None:
        if len(block.get("samples") or []) < 2:
            return
        if self._sweep_anim.state() == QVariantAnimation.State.Running and not Motion.reduce \
                and self.isVisible():
            # The beam finishes first and only the newest block waits. It is
            # drawn now, while the beam has frames to spare, so the frame that
            # starts its sweep has nothing to draw.
            self._pending = block
            self._pending_pre = self._prepare(block)
            return
        self._start(block)

    @staticmethod
    def _stats(samples) -> Tuple[float, float]:
        n = len(samples)
        mean = sum(samples) / n
        return mean, math.sqrt(max(sum((s - mean) ** 2 for s in samples) / n, 1.0))

    def _prepare(self, block: dict) -> dict:
        """The costly part of a sweep start, done ahead: the block's trace image
        and the ghost of the trace it will replace."""
        samples = block["samples"]
        mean, sd = self._stats(samples)
        after = self.history[-1] if self.history else None
        ready = self._layers_key == self._key()
        return {"key": self._key(), "after": after, "stats": (samples, mean, sd),
                "trace": self._render_trace(samples, mean, sd) if ready else None,
                "ghost": self._ghost_of(after) if ready and after is not None else None}

    def _start(self, block: dict, pre: Optional[dict] = None) -> None:
        current = self.history[-1] if self.history else None
        if pre is not None and (pre["key"] != self._key() or pre["after"] is not current
                                or pre["stats"][0] is not block["samples"]):
            pre = None                      # drawn for another size or another predecessor
        if pre is not None:
            samples, mean, sd = pre["stats"]
        else:
            samples = block["samples"]
            mean, sd = self._stats(samples)
        sim = bool(block.get("simulated"))
        if sim != self.simulated:
            self._grid_key = None           # the watermark lives in the grid layer
        self._base_img = None
        self.block = block
        self.simulated = sim
        animate = not Motion.reduce and self.isVisible()
        # The trace that was current becomes the newest ghost, and stays fully
        # drawn to the right of the beam until the beam has written over it.
        if self._trace_img is not None and self._layers_key == self._key():
            if pre is not None and pre["ghost"] is not None:
                ghost = pre["ghost"]
            else:
                ghost = self._ghost_of(current) if current is not None else None
            self._fade_persist(ghost, 1.0 if animate else self.FADE)
            self._prev_img = self._trace_img if animate else None
        else:
            self._prev_img = None
        self.history.append((samples, mean, sd))
        if self._layers_key != self._key():
            self._rebuild_layers()
        elif pre is not None and pre["trace"] is not None:
            self._trace_img = pre["trace"]
        else:
            self._trace_img = self._render_trace(samples, mean, sd)
        # Histogram over mean +/- 4 sd, sideways next to the trace.
        lo, hi = mean - 4 * sd, mean + 4 * sd
        counts = [0] * self.BINS
        for s in samples:
            i = int((s - lo) / (hi - lo) * self.BINS)
            if 0 <= i < self.BINS:
                counts[i] += 1
        m = max(counts) or 1
        self._hist_from = list(self._hist)
        self._hist_target = [c / m for c in counts]
        self._sweep_anim.stop()
        self._fades_done = 0
        if not animate:
            self._sweep = 1.0
            self._hist = list(self._hist_target)
        else:
            self._sweep = 0.0
            self._x_last = self._plot().left()
            self._sweep_anim.setStartValue(0.0)
            self._sweep_anim.setEndValue(1.0)
            self._sweep_anim.start()
        self.update()
        self.shown.emit(block)

    # -- cached layers ------------------------------------------------------

    def _key(self):
        return (self.width(), self.height(), self._dpr())

    def _points(self, samples, mean, sd) -> List[QPointF]:
        plot = self._plot()
        cy = plot.center().y()
        yk = (plot.height() / 2 - 4) / (3.6 * sd)
        top, bot = plot.top() + 1, plot.bottom() - 1
        x0, dx = plot.left(), plot.width() / (len(samples) - 1)
        return [QPointF(x0 + dx * i, max(top, min(bot, cy - (v - mean) * yk))) for i, v in enumerate(samples)]

    @staticmethod
    def _segments(pts: List[QPointF]) -> List[QLineF]:
        return [QLineF(a, b) for a, b in zip(pts, pts[1:])]

    def _render_trace(self, samples, mean, sd) -> QImage:
        """The current block: a soft glow under a bright core line, opaque pens only.

        Square caps, not round ones: the raster engine strokes 511 round-capped
        segments about ten times slower (about 20 ms against 2 ms each pass at
        150 % scaling), which showed as a dropped frame at every block start,
        and at this line width the two look the same."""
        lines = self._segments(self._points(samples, mean, sd))
        glow = self._new_image()
        p = QPainter(glow)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(th.CYAN), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.drawLines(lines)
        p.end()
        img = self._new_image()
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(0.2)
        p.drawImage(0, 0, glow)
        p.setOpacity(1.0)
        p.setPen(QPen(th.mix(th.CYAN, th.ICE, 0.18), 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.drawLines(lines)
        p.end()
        return img

    def _ghost_of(self, entry) -> QImage:
        samples, mean, sd = entry
        img = self._new_image()
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(th.CYAN), 1.0))
        p.drawLines(self._segments(self._points(samples, mean, sd)))
        p.end()
        return img

    def _fade_persist(self, ghost: Optional[QImage], keep: float) -> None:
        """Keep `keep` of the persistence image, then lay `ghost` on top."""
        if self._persist_img is None:
            self._persist_img = self._new_image()
        p = QPainter(self._persist_img)
        if keep < 0.999:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.fillRect(self._persist_img.rect(), QColor(0, 0, 0, max(0, min(255, round(255 * keep)))))
        if ghost is not None:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setOpacity(self.GHOST)
            p.drawImage(0, 0, ghost)
        p.end()

    def _base(self) -> QImage:
        if self._base_img is not None and self._base_img.size() == self._new_size():
            return self._base_img
        d = self._dpr()
        img = QImage(self._new_size(), QImage.Format.Format_RGB32)
        img.setDevicePixelRatio(d)
        img.fill(QColor(th.OBSIDIAN))
        p = QPainter(img)
        p.drawImage(0, 0, self._grid())
        if self._persist_img is not None and self.block:
            p.drawImage(0, 0, self._persist_img)
        p.end()
        self._base_img = img
        return img

    def _new_size(self) -> QSize:
        d = self._dpr()
        return QSize(max(1, int(self.width() * d)), max(1, int(self.height() * d)))

    def _rebuild_layers(self) -> None:
        """After a resize or a screen change: redraw the ghosts and the current trace."""
        self._base_img = None
        self._layers_key = self._key()
        self._persist_img = self._new_image()
        self._prev_img = None
        older = list(self.history)[:-1]
        for entry in older:
            self._fade_persist(self._ghost_of(entry), self.FADE)
        self._trace_img = self._render_trace(*self.history[-1]) if self.history else None

    def _grid(self) -> QImage:
        key = (self._key(), self.simulated)
        if self._grid_img is not None and self._grid_key == key:
            return self._grid_img
        img = self._new_image()
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        plot = self._plot()
        # Graticule: 10 x 8 divisions. Short dashes drawn as plain lines, which
        # the raster engine handles far faster than a dotted pen.
        p.setPen(QPen(qc(th.BORDER, 0.55), 1))
        dots = []
        for i in range(1, 10):
            x = plot.left() + plot.width() * i / 10
            y = plot.top()
            while y < plot.bottom():
                dots.append(QLineF(x, y, x, min(y + 1.2, plot.bottom())))
                y += 4
        for j in range(1, 8):
            y = plot.top() + plot.height() * j / 8
            x = plot.left()
            while x < plot.right():
                dots.append(QLineF(x, y, min(x + 1.2, plot.right()), y))
                x += 4
        p.drawLines(dots)
        p.setPen(QPen(qc(th.BORDER, 0.95), 1))
        p.drawLine(QPointF(plot.left(), plot.center().y()), QPointF(plot.right(), plot.center().y()))
        r = plot.adjusted(-6, -6, 6, 6)
        p.setPen(QPen(qc(th.CYAN, 0.75), 1.5))
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * 14, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * 14))
        hist = self._hist_rect(plot)
        p.setPen(QPen(qc(th.BORDER, 0.9), 1))
        p.drawLine(QPointF(hist.left(), hist.top()), QPointF(hist.left(), hist.bottom()))
        if self.simulated:
            p.save()
            p.setPen(qc(th.WARNING, 0.07))
            f = th.font("display")
            f.setPixelSize(64)
            p.setFont(f)
            p.translate(plot.center())
            p.rotate(-12)
            p.drawText(QRectF(-plot.width() / 2, -50, plot.width(), 100), Qt.AlignmentFlag.AlignCenter,
                       T("scope.watermark"))
            p.restore()
        p.end()
        self._grid_img, self._grid_key = img, key
        return img

    # -- painting -----------------------------------------------------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.history:
            self._rebuild_layers()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        plot = self._plot()
        hist = self._hist_rect(plot)
        if self.block and self.history and self._layers_key != self._key():
            self._rebuild_layers()
        if self._intro >= 0.999:
            p.drawImage(0, 0, self._base())          # one opaque copy, no blending
        else:
            p.fillRect(self.rect(), QColor(th.OBSIDIAN))
            p.setOpacity(self._intro)
            p.drawImage(0, 0, self._grid())
            p.setOpacity(1.0)
            if self._persist_img is not None and self.block:
                p.drawImage(0, 0, self._persist_img)

        if not self.block or not self.history:
            self._no_signal(p, plot)
            p.end()
            return

        samples, mean, sd = self.history[-1]
        span = 3.6 * sd
        cy = plot.center().y()
        yk = (plot.height() / 2 - 4) / span

        def ymap(v):
            return max(plot.top() + 1, min(plot.bottom() - 1, cy - (v - mean) * yk))

        # Axis labels: real ADC codes at the mean and +/- 1, 2, 3 sd.
        p.setFont(th.font("mono_small"))
        for kk in (-3, -2, -1, 0, 1, 2, 3):
            v = mean + kk * sd
            y = ymap(v)
            p.setPen(QColor(th.STEEL) if kk else QColor(th.ICE))
            p.drawText(QRectF(4, y - 8, self.PAD_L - 12, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.0f}")

        # Left of the beam the newest block, right of it the one it replaces.
        if self._trace_img is not None:
            x = self._beam_x(plot)
            if self._prev_img is not None and self._sweep < 1.0:
                p.save()
                p.setClipRect(QRectF(x + 3, 0, self.width() - x - 3, self.height()), Qt.ClipOperation.IntersectClip)
                p.drawImage(0, 0, self._prev_img)
                p.restore()
            p.save()
            p.setClipRect(QRectF(0, 0, x + 3, self.height()), Qt.ClipOperation.IntersectClip)
            p.drawImage(0, 0, self._trace_img)
            p.restore()
            if self._sweep < 1.0:
                g = QLinearGradient(x - self.GLOW_W, 0, x, 0)
                g.setColorAt(0, qc(th.CYAN, 0))
                g.setColorAt(1, qc(th.CYAN, 0.13))
                p.fillRect(QRectF(x - self.GLOW_W, plot.top(), self.GLOW_W, plot.height()), QBrush(g))
                p.setPen(QPen(qc(th.CYAN, 0.55), 1))
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # Sideways histogram sharing the trace's vertical scale; one gradient for all bars.
        lo, hi = mean - 4 * sd, mean + 4 * sd
        g = QLinearGradient(hist.left(), 0, hist.right() - 6, 0)
        g.setColorAt(0, qc(th.BLUE, 0.35))
        g.setColorAt(1, qc(th.BLUE, 0.9))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        for i, v in enumerate(self._hist):
            y0 = ymap(lo + (hi - lo) * (i + 1) / self.BINS)
            y1 = ymap(lo + (hi - lo) * i / self.BINS)
            if y1 - y0 < 0.5 or v <= 0.002:
                continue
            p.drawRoundedRect(QRectF(hist.left() + 2, y0 + 0.5, (hist.width() - 8) * v, max(0.5, y1 - y0 - 1)),
                              1.2, 1.2)
        p.setPen(QColor(th.STEEL))
        p.setFont(th.font("small"))
        p.drawText(QRectF(hist.left(), plot.bottom() + 6, hist.width(), 18), Qt.AlignmentFlag.AlignLeft,
                   T("scope.histogram"))

        # Status badge.
        clipped = int(self.block.get("clipped", 0))
        if self.simulated:
            self._badge(p, plot, T("scope.simulated"), th.WARNING)
        elif not self.block.get("valid"):
            self._badge(p, plot, T("scope.fault", state=self.block.get("trng", "")), th.ERROR)
        else:
            self._badge(p, plot, T("scope.live"), th.SUCCESS)
        row = 1
        if clipped:
            self._badge(p, plot, T("scope.clipped") + f" {clipped}", th.WARNING, row=row)
            row += 1
        if self.block.get("b") and self.block.get("corr_x1000") is not None:
            r = int(self.block["corr_x1000"])
            if self.simulated:
                color = th.WARNING        # like every simulated value, never the colour of a real result
            else:
                color = th.SUCCESS if abs(r) < 100 else (th.WARNING if abs(r) < 300 else th.ERROR)
            self._badge(p, plot, T("scope.source_b", r=f"{r / 1000:+.2f}"), color, row=row)
        p.end()

    def _badge(self, p: QPainter, plot: QRectF, text: str, color: str, row: int = 0) -> None:
        p.setFont(th.font("small_strong"))
        fm = QFontMetricsF(p.font())
        w = fm.horizontalAdvance(text) + 26
        r = QRectF(plot.right() - w - 8, plot.top() + 8 + row * 28, w, 22)
        p.setPen(QPen(qc(color, 0.55), 1))
        p.setBrush(qc(th.OBSIDIAN, 0.85))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(r.left() + 11, r.center().y()), 3.2, 3.2)
        p.setPen(QColor(color))
        p.drawText(r.adjusted(18, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def _no_signal(self, p: QPainter, plot: QRectF) -> None:
        cy = plot.center().y()
        p.setPen(QPen(qc(th.STEEL, 0.5), 1.4, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), cy), QPointF(plot.right(), cy))
        p.setPen(QColor(th.ICE))
        p.setFont(th.font("title"))
        p.drawText(QRectF(plot.left(), cy - 52, plot.width(), 28), Qt.AlignmentFlag.AlignCenter,
                   T("scope.no_signal"))
        p.setPen(QColor(th.STEEL))
        p.setFont(th.font("body"))
        p.drawText(QRectF(plot.left(), cy + 14, plot.width(), 24), Qt.AlignmentFlag.AlignCenter, self.reason)



# ---------------------------------------------------------------------------
# Stage rail: mirrors the stages the device reports, then the PC's checks
# ---------------------------------------------------------------------------

DEVICE_STAGES = ["CONFIRM", "ENTROPY", "REFRESH", "SIGN", "VERIFY", "ECDSA"]
PC_STAGES = ["PC_VERIFY", "SAVE"]


class StageRail(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(92)
        self.stages: List[str] = DEVICE_STAGES + PC_STAGES
        self.done: set = set()
        self.active = ""
        self.failed = ""
        self.idle = True
        self._fill = {}           # stage -> 0..1 connector fill toward it
        self._t0 = time.monotonic()
        self._intro = 1.0
        self._intro_anim = QVariantAnimation(self)
        self._intro_anim.setDuration(900)
        self._intro_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro_anim.valueChanged.connect(lambda v: self._set_intro(float(v)))
        self._fill_anim = QVariantAnimation(self)
        self._fill_anim.setDuration(380)
        self._fill_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fill_anim.valueChanged.connect(self._on_fill)
        self._fill_target = ""
        self.setAccessibleName("Stages")

    def _set_intro(self, v):
        self._intro = v
        self.update()

    def intro(self) -> None:
        if Motion.reduce:
            return
        self._intro_anim.setStartValue(0.0)
        self._intro_anim.setEndValue(1.0)
        self._intro_anim.start()

    def _on_fill(self, v):
        if self._fill_target:
            self._fill[self._fill_target] = float(v)
        self.update()

    def set_job(self, stages: Optional[List[str]], active: str, done: List[str], failed: str,
                running: bool) -> None:
        if stages is None:
            self.stages = DEVICE_STAGES + PC_STAGES
            self.done, self.active, self.failed, self.idle = set(), "", "", True
            self._fill = {}
            Clock.unsubscribe(self)
            self.update()
            return
        new_active = active if running else ""
        if new_active and new_active != self.active:
            self._fill_target = new_active
            self._fill_anim.stop()
            if Motion.reduce:
                self._fill[new_active] = 1.0
            else:
                self._fill_anim.setStartValue(0.0)
                self._fill_anim.setEndValue(1.0)
                self._fill_anim.start()
        for s in done:
            self._fill.setdefault(s, 1.0)
        self.stages = list(stages)
        self.done = set(done)
        self.active = new_active
        self.failed = failed
        self.idle = False
        if running and not Motion.reduce:
            Clock.subscribe(self)
        else:
            Clock.unsubscribe(self)
        self.update()

    def _positions(self):
        dev = [s for s in self.stages if s in DEVICE_STAGES]
        pcs = [s for s in self.stages if s in PC_STAGES]
        left, right = 28.0, self.width() - 28.0
        gap = 54.0 if (dev and pcs) else 0.0
        step = (right - left - gap) / max(len(dev) + len(pcs) - 1, 1)
        pos = {}
        x = left
        for s in dev:
            pos[s] = x
            x += step
        if pcs:
            x = pos[dev[-1]] + step + gap if dev else left
            for s in pcs:
                pos[s] = x
                x += step
        return dev, pcs, pos

    def paintEvent(self, e):
        p = painter(self)
        dev, pcs, pos = self._positions()
        cy = 34.0
        R = 11.0
        t = time.monotonic() - self._t0
        order = dev + pcs
        p.setFont(th.font("label"))
        # Group captions.
        p.setPen(QColor(th.STEEL))
        if dev:
            p.drawText(QRectF(pos[dev[0]] - R, 0, 300, 14), Qt.AlignmentFlag.AlignLeft, T("rail.device"))
        if pcs:
            p.drawText(QRectF(pos[pcs[0]] - R, 0, 300, 14), Qt.AlignmentFlag.AlignLeft, T("rail.pc"))
        # Connectors.
        for a, b in zip(order, order[1:]):
            if a in DEVICE_STAGES and b in PC_STAGES:
                continue
            x0, x1 = pos[a] + R + 4, pos[b] - R - 4
            p.setPen(QPen(qc(th.BORDER, self._intro), 2))
            p.drawLine(QPointF(x0, cy), QPointF(x1, cy))
            f = 1.0 if b in self.done or b == self.failed else self._fill.get(b, 0.0) if b == self.active else 0.0
            if f > 0:
                p.setPen(QPen(qc(th.CYAN, 0.9), 2))
                p.drawLine(QPointF(x0, cy), QPointF(x0 + (x1 - x0) * f, cy))
        if dev and pcs:
            x0, x1 = pos[dev[-1]] + R + 6, pos[pcs[0]] - R - 6
            p.setPen(QPen(qc(th.BORDER, 0.8 * self._intro), 1.4, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x0, cy), QPointF(x1, cy))
        # Nodes.
        for i, s in enumerate(order):
            appear = max(0.0, min(1.0, self._intro * len(order) - i * 0.6))
            x = pos[s]
            center = QPointF(x, cy)
            if s == self.failed:
                p.setPen(QPen(QColor(th.ERROR), 2))
                p.setBrush(qc(th.ERROR, 0.15))
                p.drawEllipse(center, R, R)
                p.drawLine(QPointF(x - 4.5, cy - 4.5), QPointF(x + 4.5, cy + 4.5))
                p.drawLine(QPointF(x + 4.5, cy - 4.5), QPointF(x - 4.5, cy + 4.5))
                lab = QColor(th.ERROR)
            elif s in self.done:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(th.CYAN))
                p.drawEllipse(center, R, R)
                pen = QPen(QColor(th.OBSIDIAN), 2.2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(x - 4.5, cy + 0.5), QPointF(x - 1.2, cy + 3.8))
                p.drawLine(QPointF(x - 1.2, cy + 3.8), QPointF(x + 5, cy - 3.5))
                lab = QColor(th.ICE)
            elif s == self.active:
                pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.2) if not Motion.reduce else 1.0
                glow = QRadialGradient(center, R * 2.4)
                glow.setColorAt(0, qc(th.CYAN, 0.35 * pulse))
                glow.setColorAt(1, qc(th.CYAN, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(center, R * 2.4, R * 2.4)
                p.setPen(QPen(QColor(th.CYAN), 2))
                p.setBrush(qc(th.CYAN, 0.12))
                p.drawEllipse(center, R, R)
                ring = QPen(qc(th.CYAN, 0.8), 1.4, Qt.PenStyle.DashLine)
                ring.setDashPattern([2.5, 3.5])
                ring.setDashOffset(0 if Motion.reduce else -t * 6)
                p.setPen(ring)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(center, R + 5, R + 5)
                lab = QColor(th.CYAN)
            else:
                dim = 0.45 if self.idle else 0.8
                pen = QPen(qc(th.STEEL, dim * appear), 1.4, Qt.PenStyle.DotLine)
                p.setPen(pen)
                p.setBrush(qc(th.SLATE, appear))
                p.drawEllipse(center, R, R)
                lab = qc(th.STEEL, (0.6 if self.idle else 1.0) * appear)
            p.setPen(lab)
            p.setFont(th.font("stage"))
            p.drawText(QRectF(x - 50, cy + R + 8, 100, 18), Qt.AlignmentFlag.AlignCenter, T(f"stages.{s}.short"))
        p.end()


# ---------------------------------------------------------------------------
# Seal: the result mark after an operation
# ---------------------------------------------------------------------------

class Seal(QWidget):
    def __init__(self, parent=None, size: int = 84):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.kind = "ok"
        self._k = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on)

    def _on(self, v):
        self._k = float(v)
        self.update()

    def play(self, kind: str) -> None:
        self.kind = kind
        self._anim.stop()
        if Motion.reduce:
            self._k = 1.0
            self.update()
            return
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, e):
        p = painter(self)
        col = {"ok": th.SUCCESS, "warn": th.WARNING, "fail": th.ERROR, "sim": th.WARNING}[self.kind]
        s = self.width()
        r = QRectF(6, 6, s - 12, s - 12)
        ring_k = min(1.0, self._k / 0.6)
        mark_k = max(0.0, (self._k - 0.45) / 0.55)
        glow = QRadialGradient(r.center(), s / 2)
        glow.setColorAt(0, qc(col, 0.16 * self._k))
        glow.setColorAt(1, qc(col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(r.center(), s / 2, s / 2)
        p.setPen(QPen(qc(col, 0.25), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        pen = QPen(QColor(col), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(-360 * 16 * ring_k))
        c = r.center()
        u = r.width() / 2
        if self.kind in ("ok", "sim") and mark_k > 0:
            pts = [QPointF(c.x() - u * 0.38, c.y() + u * 0.02), QPointF(c.x() - u * 0.1, c.y() + u * 0.3),
                   QPointF(c.x() + u * 0.42, c.y() - u * 0.28)]
            self._polyline(p, pts, mark_k)
        elif mark_k > 0:
            a = u * 0.3
            self._polyline(p, [QPointF(c.x() - a, c.y() - a), QPointF(c.x() + a, c.y() + a)], min(1, mark_k * 2))
            if mark_k > 0.5:
                self._polyline(p, [QPointF(c.x() + a, c.y() - a), QPointF(c.x() - a, c.y() + a)],
                               (mark_k - 0.5) * 2)
        p.end()

    @staticmethod
    def _polyline(p, pts, k):
        lens = [math.dist((a.x(), a.y()), (b.x(), b.y())) for a, b in zip(pts, pts[1:])]
        total = sum(lens) * k
        path = QPainterPath(pts[0])
        for (a, b), L in zip(zip(pts, pts[1:]), lens):
            if total <= 0:
                break
            f = min(1.0, total / L)
            path.lineTo(a + (b - a) * f)
            total -= L
        p.drawPath(path)


# ---------------------------------------------------------------------------
# Digest readout, revealed left to right when a new file is hashed
# ---------------------------------------------------------------------------

class DigestReadout(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.setFixedHeight(64)
        self._k = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_k", float(v)), self.update()))

    def set_text(self, text: str) -> None:
        self.text = text
        self._anim.stop()
        if Motion.reduce:
            self._k = 1.0
            self.update()
            return
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(300, 64)

    def paintEvent(self, e):
        p = painter(self)
        p.setFont(th.font("digest"))
        fm = QFontMetricsF(p.font())
        w = fm.horizontalAdvance(self.text)
        r = QRectF(0, 0, w + 4, self.height())
        p.setClipRect(QRectF(0, 0, (w + 6) * self._k, self.height()))
        p.setPen(QColor(th.ICE))
        p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.setClipping(False)
        if self._k < 1.0:
            x = (w + 6) * self._k
            p.setPen(QPen(qc(th.CYAN, 0.9), 2))
            p.drawLine(QPointF(x, 8), QPointF(x, self.height() - 8))
        p.end()


# ---------------------------------------------------------------------------
# Drop zone
# ---------------------------------------------------------------------------

class DropZone(KeyboardFocus, QFrame):
    dropped = Signal(Path)
    dropped_many = Signal(list)   # several files at once, only when `multi` is set
    clicked = Signal()

    def __init__(self, title: str, subtitle: str, compact: bool = False, parent=None, multi: bool = False):
        super().__init__(parent)
        self.multi = multi
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.title = title
        self.subtitle = subtitle
        self.compact = compact
        self._hot = False
        self._hover = False
        self._t0 = time.monotonic()
        self.setMinimumHeight(110 if compact else 220)
        self.setAccessibleName(title)

    def _local(self, e) -> List[Path]:
        urls = [u for u in e.mimeData().urls() if u.isLocalFile()]
        if not urls or (len(urls) > 1 and not self.multi):
            return []
        return [Path(u.toLocalFile()) for u in urls]

    def dragEnterEvent(self, e):
        if self._local(e):
            e.acceptProposedAction()
            self._hot = True
            if not Motion.reduce:
                Clock.subscribe(self)
            self.update()

    def dragLeaveEvent(self, e):
        self._hot = False
        Clock.unsubscribe(self)
        self.update()

    def dropEvent(self, e):
        self._hot = False
        Clock.unsubscribe(self)
        self.update()
        paths = self._local(e)
        if paths:
            e.acceptProposedAction()
            if len(paths) == 1:
                self.dropped.emit(paths[0])
            else:
                self.dropped_many.emit(paths)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hot = self._hot
        p.setBrush(qc(th.CYAN, 0.06) if hot else (qc(th.SLATE, 0.7) if self._hover else qc(th.SLATE, 0.4)))
        pen = QPen(QColor(th.CYAN) if (hot or self.focus_ring()) else th.mix(th.BORDER, th.STEEL, 0.35 if self._hover else 0.0),
                   1.6 if hot else 1.2, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        if hot and not Motion.reduce:
            pen.setDashOffset(-(time.monotonic() - self._t0) * 14)
        p.setPen(pen)
        p.drawRoundedRect(r, 10, 10)
        # Glyph: a sheet with a folded corner and an arrow into it.
        c = QPointF(r.center().x(), r.center().y() - (14 if self.compact else 30))
        gw, gh = (22, 28) if self.compact else (34, 44)
        sheet = QPainterPath()
        sheet.moveTo(c.x() - gw / 2, c.y() - gh / 2)
        sheet.lineTo(c.x() + gw / 2 - 9, c.y() - gh / 2)
        sheet.lineTo(c.x() + gw / 2, c.y() - gh / 2 + 9)
        sheet.lineTo(c.x() + gw / 2, c.y() + gh / 2)
        sheet.lineTo(c.x() - gw / 2, c.y() + gh / 2)
        sheet.closeSubpath()
        col = QColor(th.CYAN) if hot else QColor(th.STEEL)
        p.setPen(QPen(col, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(sheet)
        p.drawLine(QPointF(c.x(), c.y() - gh / 4), QPointF(c.x(), c.y() + gh / 4))
        p.drawLine(QPointF(c.x() - 5, c.y() + gh / 4 - 5), QPointF(c.x(), c.y() + gh / 4))
        p.drawLine(QPointF(c.x() + 5, c.y() + gh / 4 - 5), QPointF(c.x(), c.y() + gh / 4))
        p.setPen(QColor(th.ICE))
        p.setFont(th.font("title" if not self.compact else "body_strong"))
        ty = c.y() + gh / 2 + (10 if self.compact else 18)
        p.drawText(QRectF(r.left(), ty, r.width(), 26), Qt.AlignmentFlag.AlignHCenter, self.title)
        p.setPen(QColor(th.STEEL))
        p.setFont(th.font("small" if self.compact else "body"))
        p.drawText(QRectF(r.left(), ty + 24, r.width(), 22), Qt.AlignmentFlag.AlignHCenter, self.subtitle)
        p.end()


# ---------------------------------------------------------------------------
# Countdown ring for the USER button window
# ---------------------------------------------------------------------------

class CountdownRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(58, 58)
        self.deadline = 0.0
        self.total = 30.0

    def start(self, seconds: float) -> None:
        self.total = max(seconds, 1.0)
        self.deadline = time.monotonic() + seconds
        Clock.subscribe(self)
        self.show()

    def stop(self) -> None:
        Clock.unsubscribe(self)
        self.hide()

    def paintEvent(self, e):
        p = painter(self)
        left = max(0.0, self.deadline - time.monotonic())
        k = left / self.total
        r = QRectF(5, 5, self.width() - 10, self.height() - 10)
        p.setPen(QPen(qc(th.BORDER, 1), 3))
        p.drawEllipse(r)
        pen = QPen(QColor(th.WARNING if k < 0.25 else th.CYAN), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(360 * 16 * k))
        p.setPen(QColor(th.ICE))
        p.setFont(th.font("title"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{math.ceil(left)}")
        p.end()


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------

class Chip(QLabel):
    COLORS = {"ok": th.SUCCESS, "warn": th.WARNING, "err": th.ERROR, "cyan": th.CYAN, "blue": th.BLUE,
              "muted": th.STEEL}

    def __init__(self, text: str = "", kind: str = "muted", parent=None):
        super().__init__(text, parent)
        self.setFont(th.font("small_strong"))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        # A style sheet change re-polishes the widget, so skip it when nothing changed.
        if getattr(self, "_kind", None) == kind:
            return
        self._kind = kind
        c = self.COLORS.get(kind, th.STEEL)
        q = QColor(c)
        self.setStyleSheet(
            f"QLabel {{ color: {c}; border: 1px solid rgba({q.red()},{q.green()},{q.blue()},0.45);"
            f" background: rgba({q.red()},{q.green()},{q.blue()},0.08); border-radius: 10px;"
            f" padding: 2px 10px; }}")


class StatusIcon(QWidget):
    """Pending, running, pass, warn or fail mark for a list row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.state = "pending"
        self._t0 = time.monotonic()

    def set_state(self, s: str) -> None:
        self.state = s
        if s == "running" and not Motion.reduce:
            Clock.subscribe(self)
        else:
            Clock.unsubscribe(self)
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        c = QPointF(11, 11)
        if self.state == "pending":
            p.setPen(QPen(qc(th.STEEL, 0.6), 1.3, Qt.PenStyle.DotLine))
            p.drawEllipse(c, 7, 7)
        elif self.state == "running":
            p.setPen(QPen(qc(th.BORDER, 1), 2))
            p.drawEllipse(c, 7, 7)
            pen = QPen(QColor(th.CYAN), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            a = 0 if Motion.reduce else int(-(time.monotonic() - self._t0) * 400 * 16) % (360 * 16)
            p.drawArc(QRectF(4, 4, 14, 14), a, 110 * 16)
        else:
            col = {"pass": th.SUCCESS, "warn": th.WARNING, "fail": th.ERROR}[self.state]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(col, 0.16))
            p.drawEllipse(c, 9, 9)
            pen = QPen(QColor(col), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if self.state == "pass":
                p.drawLine(QPointF(7, 11.5), QPointF(10, 14.5))
                p.drawLine(QPointF(10, 14.5), QPointF(15.5, 8))
            elif self.state == "warn":
                p.drawLine(QPointF(11, 6.5), QPointF(11, 12))
                p.drawPoint(QPointF(11, 15.5))
            else:
                p.drawLine(QPointF(7.5, 7.5), QPointF(14.5, 14.5))
                p.drawLine(QPointF(14.5, 7.5), QPointF(7.5, 14.5))
        p.end()


class ElidedLabel(QLabel):
    """One line that shortens in the middle (paths), full text as a tooltip."""

    def __init__(self, text: str = "", role: str = "", f: str = "small"):
        super().__init__()
        self.setFont(th.font(f))
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)
        self._full = ""
        self.set_full(text)

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 40)))


class CheckList(QWidget):
    """Compact readiness rows: one status mark and one line per check."""

    def __init__(self, keys: List[str], parent=None):
        super().__init__(parent)
        self.keys = keys
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.rows = {}
        for k in keys:
            icon = StatusIcon()
            text = QLabel("")
            text.setFont(th.font("small"))
            text.setMinimumHeight(22)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(icon)
            row.addWidget(text, 1)
            lay.addLayout(row)
            self.rows[k] = (icon, text)

    def set_checks(self, checks: List[dict]) -> None:
        by_key = {c["key"]: c for c in checks}
        for k, (icon, text) in self.rows.items():
            c = by_key.get(k)
            if c is None:
                state, words, role = "pending", T(f"checks.{k}.ok"), "muted"
            else:
                state = "pass" if c["ok"] else ("fail" if c["required"] else "warn")
                words = T(f"checks.{k}.{'ok' if c['ok'] else 'fail'}")
                role = "" if c["ok"] else ("err" if c["required"] else "warn")
            icon.set_state(state)
            text.setText(words)
            if text.property("role") != role:
                text.setProperty("role", role)
                text.style().unpolish(text)
                text.style().polish(text)


def label(text: str = "", role: str = "", f: str = "body", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(th.font(f))
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse if f.startswith("mono")
                                else Qt.TextInteractionFlag.NoTextInteraction)
    return lab


def seam(vertical: bool = False) -> QFrame:
    s = QFrame()
    s.setObjectName("Seam")
    if vertical:
        s.setFixedWidth(1)
    else:
        s.setFixedHeight(1)
    return s


def _pack(lay, widgets) -> None:
    for w in widgets:
        if w == "stretch":
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif hasattr(w, "addWidget") and not isinstance(w, QWidget):
            lay.addLayout(w)
        else:
            lay.addWidget(w)


def hbox(*widgets, spacing: int = 10, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    _pack(lay, widgets)
    return lay


def vbox(*widgets, spacing: int = 10, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    _pack(lay, widgets)
    return lay


# ---------------------------------------------------------------------------
# Toasts
# ---------------------------------------------------------------------------

class Toast(QFrame):
    def __init__(self, title: str, detail: str, kind: str, host: "ToastHost"):
        super().__init__(host)
        self.host = host
        self.kind = kind
        self.setFixedWidth(360)
        self._life = 4.5 if kind != "err" else 7.0
        self._born = time.monotonic()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 14, 14)
        lay.setSpacing(3)
        t = label(title, "", "body_strong", wrap=True)
        lay.addWidget(t)
        if detail:
            lay.addWidget(label(detail, "muted", "small", wrap=True))
        self.adjustSize()
        Clock.subscribe(self)

    def mousePressEvent(self, e):
        self._life = 0

    def paintEvent(self, e):
        p = painter(self)
        col = {"ok": th.SUCCESS, "warn": th.WARNING, "err": th.ERROR, "info": th.CYAN, "sim": th.WARNING}.get(
            self.kind, th.CYAN)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(th.BORDER), 1))
        p.setBrush(QColor(th.SLATE))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(0, 0, 4, self.height()), 2, 2)
        age = time.monotonic() - self._born
        k = max(0.0, 1.0 - age / self._life)
        p.setBrush(qc(col, 0.6))
        p.drawRect(QRectF(4, self.height() - 2, (self.width() - 8) * k, 2))
        p.end()
        if k <= 0 and not getattr(self, "_retiring", False):
            self._retiring = True
            Clock.unsubscribe(self)
            QTimer.singleShot(0, lambda: self.host.retire(self))


class ToastHost(QWidget):
    """Transparent overlay in the main window's bottom-right corner."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.toasts: List[Toast] = []
        self.raise_()

    def show_toast(self, title: str, detail: str = "", kind: str = "info") -> None:
        t = Toast(title, detail, kind, self)
        self.toasts.append(t)
        if len(self.toasts) > 4:
            self.retire(self.toasts[0])
        t.show()
        self.relayout(new=t)

    def retire(self, t: Toast) -> None:
        if t in self.toasts:
            self.toasts.remove(t)
            t.deleteLater()
            self.relayout()

    def relayout(self, new: Optional[Toast] = None) -> None:
        par = self.parentWidget()
        if par is None:
            return
        W, H = 380, sum(t.height() + 10 for t in self.toasts) + 10
        self.setGeometry(par.width() - W - 14, par.height() - H - 14, W, H)
        y = 10
        for t in self.toasts:
            target = QPointF(10, y)
            if t is new and not Motion.reduce:
                a = QPropertyAnimation(t, b"pos", t)
                a.setDuration(280)
                a.setEasingCurve(QEasingCurve.Type.OutCubic)
                a.setStartValue(QPointF(W, y).toPoint())
                a.setEndValue(target.toPoint())
                a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            else:
                t.move(target.toPoint())
            y += t.height() + 10
        self.raise_()
