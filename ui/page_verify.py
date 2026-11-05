"""Verify page: a signed copy, or a file and its signature file, and
whose keys to trust.

This page needs no board, so anyone who installs Zirah can check signatures
with it. Several files dropped at once, onto any slot or anywhere on the
page, are sorted by what they contain. A signed copy needs no second file."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QStackedLayout, QVBoxLayout, QWidget

import zirah_core as core
from common import group_hex, local_time, size_text
from theme import T
from widgets import Button, DropZone, ElidedLabel, Seal, StatusIcon, fade_in, hbox, label, seam, vbox


class FileSlot(QFrame):
    """A small drop target that turns into the chosen file's name."""

    picked = Signal(Path)
    picked_many = Signal(list)

    def __init__(self, caption: str, drop_title: str, button: str, choose: Callable[[], Optional[Path]]):
        super().__init__()
        self.choose = choose
        self.stack = QStackedLayout()
        zone = DropZone(drop_title, button, compact=True, multi=True)
        zone.dropped.connect(self.picked.emit)
        zone.dropped_many.connect(self.picked_many.emit)
        zone.clicked.connect(self._ask)
        chosen = QWidget()
        self.name = label("", "", "body_strong", wrap=True)
        self.detail = ElidedLabel("", "muted", "small")
        change = Button(T("sign.change_dest"), "ghost")
        change.clicked.connect(self._ask)
        slot = hbox(vbox(self.name, self.detail, spacing=4), change, spacing=10, margins=(16, 14, 12, 14))
        slot.setStretch(0, 1)
        chosen.setLayout(slot)
        chosen.setObjectName("Chosen")
        self.stack.addWidget(zone)
        self.stack.addWidget(chosen)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label(caption, "muted", "label"))
        lay.addLayout(self.stack)
        self.setMinimumHeight(96)

    def _ask(self) -> None:
        p = self.choose()
        if p is not None:
            self.picked.emit(p)

    def set_file(self, name: str, detail: str) -> None:
        self.name.setText(name)
        self.detail.set_full(detail)
        self.stack.setCurrentIndex(1)
        fade_in(self.stack.currentWidget(), 180)

    def clear(self) -> None:
        self.stack.setCurrentIndex(0)


class CheckRow(QWidget):
    def __init__(self):
        super().__init__()
        self.icon = StatusIcon()
        self.text = label("", "", "body")
        self.setLayout(hbox(self.icon, self.text, "stretch", spacing=10))


class VerifyPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.doc: Optional[dict] = None
        self.doc_path: Optional[Path] = None
        self.sig: Optional[Path] = None
        self.keys: Optional[Path] = None
        self.inside = False              # the file is a signed copy and carries its signature
        self.sig_auto = False            # the signature file was found beside the signed file
        self.job_id = ""
        self.setAcceptDrops(True)

        self.slot_doc = FileSlot(T("verify.file"), T("verify.drop_doc"), T("verify.choose_doc"),
                                 lambda: self.ctx.ask_open("verify-doc"))
        self.slot_doc.picked.connect(self.set_signed_file)
        self.slot_sig = FileSlot(T("verify.signature"), T("verify.drop_sig"), T("verify.choose_sig"),
                                 lambda: self.ctx.ask_open("verify-sig"))
        self.slot_sig.picked.connect(self.set_signature)
        self.slot_keys = FileSlot(T("verify.keys"), T("verify.drop_keys"), T("verify.keys_optional"),
                                  lambda: self.ctx.ask_open("keys"))
        self.slot_keys.picked.connect(self.set_keys)
        for slot in (self.slot_doc, self.slot_sig, self.slot_keys):
            slot.picked_many.connect(self.add_files)

        self.verify_btn = Button(T("verify.button"), "blue")
        self.verify_btn.setMinimumWidth(180)
        self.verify_btn.clicked.connect(self.run)
        reset = Button(T("verify.reset"), "ghost")
        reset.clicked.connect(self.reset)

        # Report.
        self.report = QWidget()
        self.seal = Seal(size=64)
        self.v_title = label("", "", "display")
        self.v_detail = label("", "muted", "body", wrap=True)
        self.rows: List[CheckRow] = [CheckRow() for _ in range(8)]
        rows = vbox(*self.rows, spacing=4)
        verdict = hbox(self.seal, vbox("stretch", self.v_title, self.v_detail, "stretch", spacing=2), spacing=16)
        verdict.setStretch(1, 1)
        # Whose key made the signature: what a reader without a key file compares.
        self.v_pq = label("", "", "readout")
        self.v_ec = label("", "", "readout")
        self.v_fp_hint = label("", "muted", "small", wrap=True)
        fp = hbox(vbox(label(T("verify.fp_pq"), "muted", "label"), self.v_pq, spacing=2), 34,
                  vbox(label(T("verify.fp_ec"), "muted", "label"), self.v_ec, spacing=2), "stretch")
        # The two digests side by side, so an edited file is visible at a glance.
        self.v_signed = label("", "", "mono_small", wrap=True)
        self.v_file = label("", "", "mono_small", wrap=True)
        self.v_meta = label("", "muted", "small", wrap=True)
        facts = QGridLayout()
        facts.setHorizontalSpacing(18)
        facts.setVerticalSpacing(4)
        facts.addWidget(label(T("verify.signed_digest"), "muted", "small"), 0, 0)
        facts.addWidget(self.v_signed, 0, 1)
        facts.addWidget(label(T("verify.file_digest"), "muted", "small"), 1, 0)
        facts.addWidget(self.v_file, 1, 1)
        facts.setColumnStretch(1, 1)
        # When: what the signature vouches for, then what this PC says now.
        times = QGridLayout()
        times.setHorizontalSpacing(18)
        times.setVerticalSpacing(3)
        self.t_rows = {}
        for i, key in enumerate(("signed", "created_then", "modified_then", "created_now", "modified_now",
                                 "verified")):
            value, note = label("", "", "mono_small"), label("", "muted", "small")
            times.addWidget(label(T(f"verify.t_{key}"), "muted", "small"), i, 0)
            times.addWidget(value, i, 1)
            times.addWidget(note, i, 2)
            self.t_rows[key] = (value, note)
        times.setColumnStretch(2, 1)
        self.t_changed = label(T("verify.changed_after"), "err", "small", wrap=True)
        self.report.setLayout(vbox(seam(), 12, verdict, 8, rows, 10, seam(), 8,
                                   label(T("verify.fp_title"), "", "title"), fp, self.v_fp_hint, 8,
                                   facts, 10, seam(), 8, label(T("verify.times_title"), "", "title"), times,
                                   self.t_changed, 4, self.v_meta,
                                   label(T("verify.covers"), "muted", "small", wrap=True), spacing=6))
        self.report.hide()

        top = hbox(self.slot_doc, self.slot_sig, self.slot_keys, spacing=18)
        self.setLayout(vbox(top, label(T("verify.drop_all"), "muted", "small", wrap=True),
                            hbox(self.verify_btn, reset, "stretch", spacing=10), self.report,
                            "stretch", spacing=14, margins=(28, 22, 28, 22)))
        self.refresh()

    # -- inputs ----------------------------------------------------------------

    def add_files(self, paths: List[Path]) -> None:
        """Several files at once: each goes where its content says it belongs."""
        kinds = {p: core.classify_file(p) for p in paths if p.is_file()}
        for p, kind in kinds.items():
            if kind == "signature":
                self.set_signature(p)
            elif kind == "keys":
                self.set_keys(p)
        docs = [p for p, kind in kinds.items() if kind == "file"]
        if docs:
            self.set_signed_file(docs[-1])

    def dragEnterEvent(self, e):
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_files(paths)

    def set_signed_file(self, path: Path) -> None:
        if core.classify_file(path) != "file":
            self.add_files([path])        # a signature or key file dropped on the wrong slot
            return
        try:
            job = self.ctx.manager.prepare_file(path)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "NO_FILE"})())
            return
        self.doc, self.doc_path = None, path
        self.slot_doc.set_file(path.name, T("sign.hashing", name=""))
        self.report.hide()
        self._doc_job = job
        self.ctx.track(job, self._on_doc)
        if self.inside or self.sig_auto:
            self.sig, self.inside, self.sig_auto = None, False, False
            self.slot_sig.clear()
        sib = core.find_signature_for(path)
        if sib is not None and self.sig is None:
            self.set_signature(sib, found=True)
        self.refresh()

    def _on_doc(self, job: dict) -> None:
        if job["status"] == "running" or job["id"] != getattr(self, "_doc_job", None):
            return                      # still hashing, or a file that was replaced since
        if job["status"] != "done":
            self.ctx.toast_error(job.get("error"))
            self.slot_doc.clear()
            self.doc_path = None
            self.refresh()
            return
        self.doc = job["result"]
        self.slot_doc.set_file(self.doc["name"], f"{size_text(self.doc['size'])}    {self.doc['short']}")
        kind = self.doc.get("embedded")
        if kind and (self.sig is None or self.sig_auto):
            # A signed copy checks itself; a file found beside it is not needed.
            self.sig, self.sig_auto, self.inside = None, False, True
            self.slot_sig.set_file(T("verify.inside"), T("verify.inside_detail", kind=T(f"verify.kinds.{kind}")))
        self.refresh()

    def set_signature(self, path: Path, found: bool = False) -> None:
        if core.classify_file(path) == "keys":
            self.set_keys(path)
            return
        self.sig, self.sig_auto, self.inside = path, found, False
        self.slot_sig.set_file(path.name, T("verify.found_sig") if found else str(path.parent))
        self.report.hide()
        self.refresh()

    def set_keys(self, path: Path) -> None:
        if core.classify_file(path) == "signature":
            self.set_signature(path)
            return
        self.keys = path
        self.slot_keys.set_file(path.name, T("verify.keys_detail"))
        self.report.hide()

    def reset(self) -> None:
        self._doc_job = None
        self.doc = self.doc_path = self.sig = self.keys = None
        self.inside = self.sig_auto = False
        for slot in (self.slot_doc, self.slot_sig, self.slot_keys):
            slot.clear()
        self.report.hide()
        self.refresh()

    def load(self, doc: Path, sig: Optional[Path]) -> None:
        """Open with a file and signature already chosen (Verify Now);
        sig None for a signed copy."""
        self.reset()
        if sig is not None:
            self.set_signature(sig)
        self.set_signed_file(doc)

    # -- run ---------------------------------------------------------------------

    def run(self) -> None:
        if not (self.doc and (self.sig or self.inside)):
            return
        try:
            self.job_id = self.ctx.manager.verify(self.doc["id"], self.sig, self.keys)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "INTERNAL"})())
            return
        self.verify_btn.setEnabled(False)
        self.verify_btn.setText(T("verify.verifying"))
        self.ctx.track(self.job_id, self._on_verify)

    def _on_verify(self, job: dict) -> None:
        if job["status"] == "running":
            return
        self.verify_btn.setText(T("verify.button"))
        self.refresh()
        if job["status"] != "done":
            self.ctx.toast_error(job.get("error"))
            return
        self.show_report(job["result"])
        self.ctx.history_changed()

    def show_report(self, r: dict) -> None:
        v = r["verdict"]
        kind = {"VALID": "ok", "VALID_PQ_ONLY": "ok", "UNTRUSTED": "warn", "INVALID": "fail",
                "SIMULATED": "sim"}[v]
        self.v_title.setText(T(f"verify.verdicts.{v}.title"))
        self.v_detail.setText(T(f"verify.verdicts.{v}.detail"))
        checks = r["checks"]
        for row in self.rows:
            row.hide()
        self.report.show()
        fade_in(self.report, 220)
        self.seal.play(kind)
        self.ctx.scroll_to(self, self.report)
        for i, c in enumerate(checks[:len(self.rows)]):
            row = self.rows[i]
            text = T(f"verify.checks.{c['key']}.{'ok' if c['ok'] else 'fail'}")
            st = "pass" if c["ok"] else ("fail" if c["required"] else "warn")
            row.text.setText(text)
            row.icon.set_state(st)
            # Rows appear one after another: the result of the person's click.
            QTimer.singleShot(0 if self.ctx.reduced() else 70 * i, row.show)
        s = r.get("signature") or {}
        self.v_pq.setText(s.get("mldsa_fp", "") or "—")
        self.v_ec.setText(s.get("ecc_fp", "") or T("verify.fp_none"))
        self.v_fp_hint.setText(T("verify.fp_match", who=r["signer"]) if r.get("signer") else T("verify.fp_hint"))
        signed, mine = s.get("sha256", ""), r.get("file_sha256", "")
        self.v_signed.setText(group_hex(signed))
        self.v_file.setText(group_hex(mine) + ("" if signed == mine else "   " + T("verify.digest_differs")))
        self.v_file.setProperty("role", "" if signed == mine else "err")
        self.v_file.style().unpolish(self.v_file)
        self.v_file.style().polish(self.v_file)
        t = r.get("times") or {}
        state = t.get("times_state") or ("signed" if t.get("signed_at_signed") == "yes" else "not_signed")
        signed_note = {"signed": T("verify.note_signed"), "unverified": T("verify.note_unverified")}.get(
            state, T("verify.note_claimed"))
        then_note = {"signed": T("verify.note_signed"), "unverified": T("verify.note_unverified")}.get(
            state, T("verify.note_v1"))
        for key, value, note in (("signed", t.get("signed_at"), signed_note),
                                 ("created_then", t.get("created_at_signing"), then_note),
                                 ("modified_then", t.get("modified_at_signing"), then_note),
                                 ("created_now", t.get("created_now"), T("verify.note_fs")),
                                 ("modified_now", t.get("modified_now"), T("verify.note_fs")),
                                 ("verified", t.get("verified_at"), T("verify.note_now"))):
            v, n = self.t_rows[key]
            v.setText(local_time(value) or T("verify.t_none"))
            n.setText(note)
        self.t_changed.setVisible(t.get("changed_after_signing") == "yes")
        meta = []
        if s.get("device_uid"):
            meta.append(f"{T('verify.device')}: {s['device_uid']}  {s.get('firmware', '')}")
        self.v_meta.setText("\n".join(meta))

    def refresh(self) -> None:
        self.verify_btn.setEnabled(bool(self.doc and (self.sig or self.inside)))
