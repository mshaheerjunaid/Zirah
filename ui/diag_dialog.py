"""Diagnostics dialog: a non-sensitive report the person can read and export.

It contains no keys, signatures, file contents or file hashes. The full
device ID is shortened unless the person asks to include it.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout

import theme as th
import zirah_core as core
from theme import T, font
from widgets import Button, Toggle, hbox, label, seam


class DiagnosticsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Zirah")
        self.setModal(True)
        self.resize(640, 560)
        self.setStyleSheet(th.QSS)

        self.include_uid = Toggle(T("diag.include_uid"))
        self.include_uid.toggled.connect(self._render)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(font("mono_small"))

        export = Button(T("diag.export"), "secondary")
        export.clicked.connect(self._export)
        close = Button(T("diag.close"), "ghost")
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label(T("diag.title"), "", "title"))
        lay.addWidget(label(T("diag.sub"), "muted", "small", wrap=True))
        lay.addWidget(seam())
        lay.addLayout(hbox(self.include_uid, "stretch"))
        lay.addWidget(self.text, 1)
        lay.addLayout(hbox("stretch", export, close, spacing=10))
        self._render()

    def _data(self) -> dict:
        return self.win.manager.diagnostics(self.include_uid.isChecked())

    def _render(self) -> None:
        self.text.setPlainText(json.dumps(self._data(), indent=2))

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        import time
        suggested = Path.home() / f"zirah-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(self, T("diag.export"), str(suggested), "JSON (*.json)",
                                              options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        try:
            p = Path(path)
            if p.exists():
                raise core.ZirahError("EXISTS", "A file with that name already exists")
            core.write_new_file(p, json.dumps(self._data(), indent=2).encode("utf-8"))
            self.win.toast(T("diag.exported"), p.name, "ok")
        except core.ZirahError as e:
            self.win.toast_error(e.as_dict())
