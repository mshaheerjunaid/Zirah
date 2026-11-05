#!/usr/bin/env python3
"""Start the real desktop app the way zirah-app.bat does, use it briefly and quit.

    python tests/ui/lifecycle.py

Runs ui/zirah_app.main() offscreen in Simulation Mode, signs nothing, switches
through every tab, closes the window and checks that the program exits with
code 0, that no error box was raised, and that the device thread has stopped.
This is the path a person takes every time they quit Zirah.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZIRAH_HOME"] = tempfile.mkdtemp(prefix="zirah-life-")
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "host"))

import zirah_app  # noqa: E402

boxes = []
zirah_app.message_box = lambda text, title="Zirah": boxes.append(text)


def main() -> int:
    import window
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    original_show = window.MainWindow.show
    seen = {}

    def show(self):
        original_show(self)
        seen["win"] = self
        steps = [lambda: self.manager.req_simulation(True)]
        steps += [lambda i=i: self.tabs.set_index(i) for i in (1, 2, 3, 0)]
        steps += [self.close]
        for n, fn in enumerate(steps):
            QTimer.singleShot(1500 + 700 * n, fn)
        QTimer.singleShot(30000, QApplication.quit)     # never hang the test run

    window.MainWindow.show = show
    sys.argv = [sys.argv[0]]
    try:
        rc = zirah_app.main()
    except Exception as e:  # what the person would see as an error box
        boxes.append(repr(e))
        rc = -1
    alive = [t.name for t in threading.enumerate() if t.name == "zirah-device" and t.is_alive()]
    failures = []
    if rc != 0:
        failures.append(f"exit code {rc}")
    if boxes:
        failures.append("error box: " + " | ".join(boxes))
    if alive:
        failures.append("device thread still running after quit")
    if "win" not in seen:
        failures.append("window never opened")
    for f in failures:
        print("FAIL", f)
    print(f"lifecycle: started, used and quit, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
