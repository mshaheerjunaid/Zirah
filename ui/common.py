"""Helpers shared by the pages of the Zirah window."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from widgets import hbox, label

# Connection states in which the Sign button may be used.
SIGNABLE = ("READY", "DEGRADED", "SIMULATED")


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n:,} B"
    if n < 1024 ** 2:
        return f"{n / 1024:,.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:,.1f} MB"
    return f"{n / 1024 ** 3:,.2f} GB"


def group_hex(h: str, per_line: int = 4) -> str:
    """64 hex digits as two lines of four 8-digit groups."""
    groups = [h[i:i + 8] for i in range(0, len(h), 8)]
    return "\n".join(" ".join(groups[i:i + per_line]) for i in range(0, len(groups), per_line))


class Cell(QFrame):
    """One cell of the instrument rack: title, explanation, then content."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("Cell")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 18, 22, 20)
        self.body.setSpacing(10)
        # Content stays at the top when a neighbour in the same rack row is taller.
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(label(title, "", "title"))
        if subtitle:
            self.body.addWidget(label(subtitle, "muted", "small", wrap=True))
        self.body.addSpacing(4)


def local_time(iso: str | None) -> str:
    """'2026-10-25T17:14:00Z' as this PC's local time, with its UTC offset;
    '' for nothing."""
    import datetime as _dt
    if not iso:
        return ""
    try:
        t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).astimezone()
    except ValueError:
        return iso
    off = t.strftime("%z")
    return t.strftime("%d %b %Y, %H:%M:%S") + f"  UTC{off[:3]}:{off[3:]}"


def kv_row(key: str, value: QWidget, key_width: int = 140) -> QHBoxLayout:
    k = label(key, "muted", "small")
    k.setFixedWidth(key_width)
    return hbox(k, value, "stretch", spacing=12)
