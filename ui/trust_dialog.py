"""Trust dialog: the person reads the PQ fingerprint on the device's OLED
and types it here. Signing never trusts a key with one click."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout

import theme as th
from theme import T, font
from widgets import Button, hbox, label, seam


class TrustDialog(QDialog):
    def __init__(self, parent, uid: str, keys: dict):
        super().__init__(parent)
        self.keys = keys
        self.changed = keys.get("trust") == "changed"
        self.setWindowTitle("Zirah")
        self.setModal(True)
        self.setFixedWidth(460)
        self.setStyleSheet(th.QSS)

        title = label(T("trust.title_replace") if self.changed else T("trust.title"), "", "title")
        intro = label(T("trust.intro"), "muted", "body", wrap=True)
        warn = label(T("trust.changed"), "err", "small", wrap=True)
        warn.setVisible(self.changed)

        # The expected fingerprint is deliberately not shown: the characters must
        # be read off the OLED, or the check would only compare the PC with itself.
        step = label(T("trust.step"), "", "body", wrap=True)
        self.result = label("", "err", "small", wrap=True)
        self.entry = QLineEdit()
        self.entry.setFont(font("mono_big"))
        self.entry.setMaxLength(9)
        self.entry.setPlaceholderText(T("trust.placeholder"))
        self.entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry.textChanged.connect(self._check)
        self.entry.returnPressed.connect(self._try_accept)

        self.ok = Button(T("trust.button_replace") if self.changed else T("trust.button"), "blue")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._try_accept)
        cancel = Button(T("trust.cancel"), "ghost")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        lay.addWidget(title)
        lay.addWidget(intro)
        if self.changed:
            lay.addWidget(warn)
        lay.addWidget(seam())
        lay.addWidget(step)
        lay.addWidget(self.entry)
        lay.addWidget(self.result)
        lay.addLayout(hbox("stretch", cancel, self.ok, spacing=10))

    def _normalized(self) -> str:
        return self.entry.text().replace(" ", "").replace("-", "").upper()

    def _check(self) -> None:
        typed = self._normalized()
        self.ok.setEnabled(len(typed) == 8 and all(c in "0123456789ABCDEF" for c in typed))
        self.result.setText("")

    def _try_accept(self) -> None:
        if not self.ok.isEnabled():
            return
        if self._normalized() != self.keys.get("pq_fp", ""):
            self.result.setText(T("trust.mismatch"))
            self.entry.selectAll()
            return
        self.accept()

    def typed(self) -> str:
        return self._normalized()
