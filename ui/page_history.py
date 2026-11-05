"""History page: the last operations, with no contents, paths or keys."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QScrollArea, QWidget

from theme import T
from widgets import Button, Chip, ElidedLabel, hbox, label, seam, vbox

VERDICTS = ("VALID", "VALID_PQ_ONLY", "UNTRUSTED", "INVALID", "SIMULATED")
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def local_time(stamp: str) -> str:
    """The stored UTC time shown in the PC's own time zone; anything unparsable shows as stored."""
    try:
        t = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        return t.astimezone().strftime("%Y-%m-%d  %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(stamp)[:20]


def result_chip(result: str) -> Chip:
    """Only results Zirah itself records get a meaning; anything else in the file says so."""
    if result == "Signed":
        return Chip(T("sign.signed"), "ok")
    if result in VERDICTS:
        kind = "ok" if result.startswith("VALID") else "warn" if result in ("UNTRUSTED", "SIMULATED") else "err"
        return Chip(T(f"verify.verdicts.{result}.title"), kind)
    return Chip(T("history.unknown"), "err")


class HistoryPage(QWidget):
    COLS = 6

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        clear = Button(T("history.clear"), "danger")
        clear.clicked.connect(self.clear)
        head = hbox(vbox(label(T("history.title"), "", "title"),
                         label(T("history.sub"), "muted", "small", wrap=True), spacing=2),
                    clear, spacing=12)
        head.setStretch(0, 1)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(0)
        self.grid.setContentsMargins(4, 0, 4, 0)
        self.grid.setColumnStretch(2, 1)
        holder = QWidget()
        holder.setLayout(vbox(self.grid, "stretch", spacing=0))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(holder)
        self.empty = label(T("history.empty"), "muted", "body")
        self.setLayout(vbox(head, seam(), self.empty, area, spacing=12, margins=(28, 22, 28, 18)))
        self.refresh()

    def clear(self) -> None:
        self.ctx.history.clear()
        self.refresh()
        self.ctx.toast(T("history.cleared"), "", "info")

    def refresh(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for r in range(self.grid.rowCount()):
            self.grid.setRowMinimumHeight(r, 0)
        items = self.ctx.history.items()
        self.empty.setVisible(not items)
        for i, it in enumerate(items[:200]):
            row = 2 * i
            sign = it.get("kind") == "sign"
            digest = str(it.get("digest8", ""))
            cells = [
                (label(local_time(it.get("t", "")), "muted", "mono_small"), LEFT),
                (Chip(T("history.sign") if sign else T("history.verify"), "cyan" if sign else "blue"), LEFT),
                (ElidedLabel(str(it.get("name", "")), "", "body"), LEFT),
                (label(f"{digest[:4]} {digest[4:8]}", "", "mono_small"), RIGHT),
                (result_chip(str(it.get("result", ""))), LEFT),
            ]
            if it.get("simulated"):
                cells.append((Chip(T("history.simulated"), "warn"), LEFT))
            for col, (w, align) in enumerate(cells):
                self.grid.addWidget(w, row, col, align)
            self.grid.setRowMinimumHeight(row, 44)
            self.grid.addWidget(seam(), row + 1, 0, 1, self.COLS)
