#!/usr/bin/env python3
"""Check the signal window's drawing shortcut against the plain path.

    python tests/ui/scope_check.py

A block that arrives while the beam is still sweeping is drawn as soon as it
arrives, so that the frame which starts its sweep only swaps images. This
checks that the shortcut gives exactly the same pixels as drawing at the
sweep start, that the sweep start uses it instead of drawing again, and that
it is thrown away when the window changed size in between.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
os.environ["ZIRAH_HOME"] = tempfile.mkdtemp(prefix="zirah-scope-")
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "host"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
import widgets  # noqa: E402


def block(seed: int) -> dict:
    rnd = random.Random(seed)
    return {"samples": [max(0, min(4095, int(rnd.gauss(2048, 210)))) for _ in range(512)],
            "valid": True, "clipped": 0, "seq": seed}


def main() -> int:
    app = QApplication(sys.argv)
    theme.load_fonts()
    widgets.Motion.reduce = False
    failures = []

    def settle():
        for _ in range(5):
            app.processEvents()

    def scope():
        s = widgets.Scope()
        s.resize(1000, 300)
        s.show()
        settle()
        return s

    def finish_sweep(s):
        s._sweep_anim.stop()
        s._on_sweep_done()

    def counted(s):
        calls = [0]
        original = s._render_trace

        def render(*a, _o=original):
            calls[0] += 1
            return _o(*a)
        s._render_trace = render
        return calls

    shortcut, plain = scope(), scope()
    plain._prepare = lambda b: None          # the plain path draws at the sweep start
    for s in (shortcut, plain):
        s.push(block(1))
        s.push(block(2))                     # arrives mid-sweep and waits
    if shortcut._pending_pre is None or shortcut._pending_pre["trace"] is None:
        failures.append("a waiting block was not drawn ahead")
    calls = counted(shortcut)
    for s in (shortcut, plain):
        finish_sweep(s)
    if calls[0]:
        failures.append(f"the sweep start drew the trace again ({calls[0]} times)")
    if shortcut._trace_img != plain._trace_img:
        failures.append("the trace drawn ahead differs from the one drawn at the sweep start")
    if shortcut._persist_img != plain._persist_img:
        failures.append("the persistence differs between the two paths")
    for s in (shortcut, plain):
        s._on_sweep(0.5)
    if shortcut.grab().toImage() != plain.grab().toImage():
        failures.append("the two paths give different frames half way through a sweep")

    # A resize between a block's arrival and its sweep start makes the drawing stale.
    shortcut.push(block(3))
    stale = shortcut._pending_pre
    shortcut.resize(1100, 320)
    settle()
    calls[0] = 0
    finish_sweep(shortcut)
    if stale is None or calls[0] != 1:
        failures.append("a block drawn before a resize was not drawn again at the new size")
    if shortcut._trace_img is None or shortcut._trace_img.size() != shortcut._new_size():
        failures.append("the trace does not match the new window size")

    for f in failures:
        print("FAIL", f)
    print(f"scope: drawing ahead checked against the plain path, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
