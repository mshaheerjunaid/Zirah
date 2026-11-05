#!/usr/bin/env python3
"""Measure how long the window takes to draw while the noise trace animates.

    python tests/ui/perf.py [seconds]

Opens the real window in Simulation Mode at 150 % display scaling (a common
Windows laptop setting), lets the signal window sweep through several noise
blocks and times every paint of the custom widgets. It fails if the signal
window needs more than 30 ms per frame on average, which is the point where
an animation visibly stutters and the program starts to load the whole PC.
It also times every pass of the event loop, which includes the work done
when a new block arrives or its sweep starts, and fails if more than one
pass holds the window for more than 60 ms, about four frames, which is a
visible jerk. One such pass alone is allowed, because the operating system
can pause any program once in a while; a slow drawing step repeats with
every block. The budgets are loose on purpose so that a slow PC still
passes, while a drawing regression (the kind that costs hundreds of
milliseconds) cannot.
"""
from __future__ import annotations

import collections
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
os.environ["ZIRAH_HOME"] = tempfile.mkdtemp(prefix="zirah-perf-")
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "host"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import theme  # noqa: E402
import widgets  # noqa: E402
from manager import Bus, DeviceManager  # noqa: E402
from store import History, Settings  # noqa: E402
from window import MainWindow  # noqa: E402

BUDGET_MS = 30.0
STALL_BUDGET_MS = 60.0
stats = collections.defaultdict(lambda: [0, 0.0, 0.0])   # paints, total s, worst s


def instrument(cls) -> None:
    original = cls.paintEvent

    def timed(self, e, _orig=original, _name=cls.__name__):
        t = time.perf_counter()
        _orig(self, e)
        dt = time.perf_counter() - t
        s = stats[_name]
        s[0] += 1
        s[1] += dt
        s[2] = max(s[2], dt)
    cls.paintEvent = timed


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    for name in dir(widgets):
        c = getattr(widgets, name)
        if isinstance(c, type) and issubclass(c, QWidget) and "paintEvent" in c.__dict__:
            instrument(c)
    app = QApplication(sys.argv)
    theme.load_fonts()
    app.setStyleSheet(theme.QSS)
    app.setFont(theme.font("body"))
    settings = Settings()
    history = History(settings)
    mgr = DeviceManager(Bus(), settings, history)
    mgr.start()
    mgr.req_simulation(True)
    win = MainWindow(mgr, settings, history)
    win.resize(1600, 950)
    win.show()

    stalls = []

    def pump(sec: float) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            t = time.perf_counter()
            app.processEvents()
            stalls.append(time.perf_counter() - t)
            time.sleep(0.004)

    pump(3.0)                    # connect, intro animation, first blocks
    stats.clear()
    stalls.clear()
    wall0, cpu0 = time.monotonic(), time.thread_time()
    pump(seconds)
    wall = time.monotonic() - wall0
    cpu = time.thread_time() - cpu0
    mgr.stop()
    mgr.join(timeout=3)

    n, total, worst = stats.get("Scope", [0, 0.0, 0.0])
    failures = []
    if n < 10:
        failures.append(f"the signal window only drew {n} frames in {seconds:.0f} s")
    avg = total / n * 1000 if n else 0.0
    if avg > BUDGET_MS:
        failures.append(f"signal window {avg:.1f} ms per frame, budget {BUDGET_MS:.0f} ms")
    stall = max(stalls) * 1000 if stalls else 0.0
    over = sum(1 for t in stalls if t * 1000 > STALL_BUDGET_MS)
    if over > 1:
        failures.append(f"{over} passes of the event loop held the window for more than "
                        f"{STALL_BUDGET_MS:.0f} ms, the longest {stall:.1f} ms")
    for f in failures:
        print("FAIL", f)
    print(f"perf: signal window {avg:.1f} ms per frame (worst {worst * 1000:.1f} ms) over {n} frames, "
          f"longest event-loop pass {stall:.1f} ms ({over} over {STALL_BUDGET_MS:.0f} ms), "
          f"UI thread {cpu / wall * 100:.0f} % of one core, "
          f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
