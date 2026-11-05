"""Sign page: choose a file, sign it, compare the OLED digits, save.

Where the format allows, the result is a signed copy with the signature
inside; otherwise a separate .zirah.json file. The OLED digits come from the
manifest (the file's digest, name, size and times), so they are known only
once signing starts."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QGridLayout, QProgressBar, QStackedLayout, QWidget

import zirah_core as core

from common import SIGNABLE, group_hex, kv_row, size_text
from theme import T, error_text
from widgets import (Button, CountdownRing, DigestReadout, DropZone, ElidedLabel, Seal, crossfade, hbox,
                     label, seam, vbox)

EMPTY, HASHING, PREPARED, RESULT = range(4)


class SignPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.prep: Optional[dict] = None
        self.dest: Optional[Path] = None
        self.job_id = ""
        self.running = False
        self.out: Optional[dict] = None
        self.stack = QStackedLayout(self)
        for build in (self._empty_view, self._hashing_view, self._prepared_view, self._result_view):
            self.stack.addWidget(build())
        self.go(EMPTY)

    # -- views ---------------------------------------------------------------

    def _empty_view(self) -> QWidget:
        w = QWidget()
        zone = DropZone(T("sign.drop_title"), T("sign.drop_sub"))
        zone.dropped.connect(self.prepare)
        zone.clicked.connect(self.choose)
        pick = Button(T("sign.choose"), "secondary")
        pick.clicked.connect(self.choose)
        w.setLayout(vbox(zone, hbox("stretch", pick, "stretch"), "stretch", spacing=18,
                         margins=(28, 24, 28, 24)))
        return w

    def _hashing_view(self) -> QWidget:
        w = QWidget()
        self.h_title = label("", "", "title")
        self.h_bar = QProgressBar()
        self.h_bar.setRange(0, 1000)
        self.h_bar.setTextVisible(False)
        self.h_bar.setFixedHeight(6)
        w.setLayout(vbox("stretch", self.h_title, self.h_bar, "stretch", spacing=16,
                         margins=(64, 30, 64, 30)))
        return w

    def _prepared_view(self) -> QWidget:
        w = QWidget()
        self.f_name = label("", "", "title", wrap=True)
        self.f_size = label("", "", "body")
        self.f_hash = label("", "", "mono")
        left = vbox(label(T("sign.file"), "muted", "label"), self.f_name, 8,
                    kv_row(T("sign.size"), self.f_size, 70), 8,
                    label(T("sign.sha256"), "muted", "label"), self.f_hash, "stretch", spacing=6)

        self.digest = DigestReadout()
        self.d_name = label("", "", "body_strong", wrap=True)
        self.d_folder = ElidedLabel("", "muted", "small")
        self.d_kept = label("", "muted", "small", wrap=True)
        change = Button(T("sign.change_dest"), "ghost")
        change.clicked.connect(self.change_dest)
        self.note = label("", "warn", "small", wrap=True)
        self.sign_btn = Button(T("sign.button"), "primary")
        self.sign_btn.setMinimumWidth(190)
        self.sign_btn.clicked.connect(self.sign)
        self.other_btn = Button(T("sign.another"), "ghost")
        self.other_btn.clicked.connect(self.choose)
        self.actions = QWidget()
        self.actions.setLayout(hbox(self.sign_btn, self.other_btn, "stretch", spacing=10))

        # Shown while the device waits for the USER button.
        self.ring = CountdownRing()
        self.prompt = label("", "", "body_strong", wrap=True)
        self.prompt_sub = label("", "muted", "small", wrap=True)
        self.cancel_btn = Button(T("rail.cancel"), "danger")
        self.cancel_btn.clicked.connect(self.cancel)
        self.press_btn = Button(T("rail.press_sim"), "secondary")
        self.press_btn.clicked.connect(self.ctx.manager.press_sim_button)
        self.live = QWidget()
        self.live.setLayout(vbox(hbox(self.ring, vbox(self.prompt, self.prompt_sub, spacing=3), spacing=16),
                                 hbox(self.press_btn, self.cancel_btn, "stretch", spacing=10), spacing=14))

        self.d_caption = label(T("sign.saves_as"), "muted", "label")
        right = vbox(label(T("sign.oled_digest"), "muted", "label"), self.digest,
                     label(T("sign.oled_hint"), "muted", "small", wrap=True), 10, seam(), 10,
                     self.d_caption, self.d_name,
                     hbox(self.d_folder, change, spacing=8), self.d_kept, 4, self.note, 14,
                     self.actions, self.live, "stretch", spacing=6)

        grid = QGridLayout(w)
        grid.setContentsMargins(28, 22, 28, 22)
        grid.setHorizontalSpacing(28)
        grid.addLayout(left, 0, 0)
        grid.addWidget(seam(vertical=True), 0, 1)
        grid.addLayout(right, 0, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(2, 6)
        return w

    def _result_view(self) -> QWidget:
        w = QWidget()
        self.seal = Seal()
        self.r_title = label("", "", "display")
        self.r_detail = label("", "muted", "body", wrap=True)
        self.r_file = label("", "", "body_strong", wrap=True)
        self.r_folder = ElidedLabel("", "muted", "small")
        self.r_kept = label("", "muted", "small", wrap=True)
        self.r_fp = label("", "", "mono")
        self.r_times = label("", "muted", "small", wrap=True)
        self.r_sim = label(T("sign.sim_warning"), "warn", "small", wrap=True)
        self.show_btn = Button(T("sign.show"), "secondary")
        self.show_btn.clicked.connect(lambda: self.out and self.ctx.reveal(Path(self.out["path"])))
        self.verify_btn = Button(T("sign.verify_now"), "blue")
        self.verify_btn.clicked.connect(self.verify_now)
        self.retry_btn = Button(T("sign.try_again"), "primary")
        self.retry_btn.clicked.connect(lambda: self.go(PREPARED))
        again = Button(T("sign.sign_another"), "ghost")
        again.clicked.connect(self.reset)
        head = hbox(self.seal, vbox("stretch", self.r_title, self.r_detail, "stretch", spacing=4), spacing=20)
        head.setStretch(1, 1)
        w.setLayout(vbox(head, 14, self.r_file, self.r_folder, self.r_kept, 6, self.r_fp, self.r_times, self.r_sim,
                         18, hbox(self.retry_btn, self.show_btn, self.verify_btn, again, "stretch",
                                  spacing=10), "stretch",
                         spacing=6, margins=(28, 24, 28, 24)))
        return w

    # -- flow ----------------------------------------------------------------

    def go(self, view: int) -> None:
        def switch():
            self.stack.setCurrentIndex(view)
            self.refresh()
        if self.stack.currentIndex() != view:
            crossfade(self, switch, 240)
        else:
            switch()

    def reset(self) -> None:
        self.prep, self.dest, self.out, self.job_id = None, None, None, ""
        self.go(EMPTY)

    def choose(self) -> None:
        path = self.ctx.ask_open("sign")
        if path is not None:
            self.prepare(path)

    def prepare(self, path: Path) -> None:
        if self.running:
            return
        try:
            job = self.ctx.manager.prepare_file(path)
        except Exception as e:  # unreadable path, folder, device file
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "NO_FILE"})())
            return
        self.h_title.setText(T("sign.hashing", name=path.name))
        self.h_bar.setValue(0)
        self.go(HASHING)
        self._prep_job = job
        self.ctx.track(job, self._on_prepare)

    def _on_prepare(self, job: dict) -> None:
        if job["id"] != getattr(self, "_prep_job", None):
            return                      # a file that was replaced by a newer choice
        if job["status"] == "running":
            self.h_bar.setValue(int(job.get("progress", 0) * 1000))
            return
        if job["status"] != "done":
            self.ctx.toast_error(job.get("error"))
            self.go(EMPTY)
            return
        self.prep = job["result"]
        self.dest = Path(self.prep["dest"]) if self.prep.get("dest") else None
        self.f_name.setText(self.prep["name"])
        self.f_size.setText(size_text(self.prep["size"]))
        self.f_hash.setText(group_hex(self.prep["sha256"]))
        self.digest.set_text(T("sign.oled_pending"))
        self.go(PREPARED)

    def change_dest(self) -> None:
        if not self.prep:
            return
        doc = Path(self.prep["folder"]) / self.prep["name"]
        start = self.dest or doc
        path = self.ctx.ask_save(T("sign.choose_dest"), start, self.prep.get("container", "detached"), doc)
        if path is not None:
            self.dest = path
            self.refresh()

    def sign(self) -> None:
        if not self.prep:
            return
        try:
            self.job_id = self.ctx.manager.sign(self.prep["id"], self.dest)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "INTERNAL"})())
            return
        self.running = True
        self.ctx.track(self.job_id, self._on_sign)
        self.refresh()

    def cancel(self) -> None:
        if self.job_id:
            self.ctx.manager.cancel(self.job_id)

    def _on_sign(self, job: dict) -> None:
        if job["status"] == "running":
            if job["stage"] == "CONFIRM":
                secs = int(job.get("fields", {}).get("timeout_ms", "30000") or 30000) / 1000.0
                if getattr(self, "_ring_job", "") != job["id"]:
                    self._ring_job = job["id"]      # start the countdown once per request
                    self.ring.start(secs)
            else:
                self.ring.stop()
            self.refresh(job)
            return
        self.running = False
        self.ring.stop()
        if job["status"] == "done":
            self.out = job["result"]
            self.show_result(True, job)
        else:
            self.out = None
            self.show_result(False, job)

    def show_result(self, ok: bool, job: dict) -> None:
        sim = bool(job.get("simulated"))
        self.retry_btn.setVisible(not ok)
        self.show_btn.setVisible(ok)
        self.verify_btn.setVisible(ok)
        self.r_sim.setVisible(ok and sim)
        if ok:
            o = self.out
            self.r_title.setText(T("sign.signed_sim") if sim else T("sign.signed"))
            self.r_detail.setText(T("sign.signed_detail") if o["hybrid"] else T("sign.signed_pq"))
            self.r_file.setText(o["name"])
            self.r_folder.set_full(T("sign.in_folder", folder=o["folder"]))
            self.r_kept.setText(T("sign.original_kept_copy" if o.get("embedded") else "sign.original_kept",
                                  name=self.prep["name"]) if self.prep else "")
            self.r_fp.setText(f"PQ {o['pq_fp']}" + (f"    EC {o['ec_fp']}" if o["ec_fp"] else ""))
            self.r_times.setText(self._timings(o.get("stats", {}), sim))
            self.seal.play("sim" if sim else "ok")
            self.ctx.toast(T("toast.signed"), o["name"], "sim" if sim else "ok")
        else:
            title, hint = error_text(job.get("error"))
            self.r_title.setText(title)
            self.r_detail.setText(hint)
            for w in (self.r_file, self.r_fp, self.r_times, self.r_kept):
                w.setText("")
            self.r_folder.set_full("")
            self.seal.play("warn" if job["status"] == "cancelled" else "fail")
        self.go(RESULT)

    @staticmethod
    def _timings(st: dict, sim: bool) -> str:
        if sim or not st.get("mldsa_us"):
            return f"{T('sign.timings')}: {T('sign.t_none')}"
        def ms(key):
            return T("sign.ms", value=f"{st.get(key, 0) / 1000:.1f}")
        parts = [f"{T('sign.t_health')} {ms('health_us')}", f"{T('sign.t_masks')} {ms('trng_us')}",
                 f"{T('sign.t_mldsa')} {ms('mldsa_us')}", f"{T('sign.t_verify')} {ms('verify_us')}"]
        if st.get("ecdsa_us"):
            parts.append(f"{T('sign.t_ecdsa')} {ms('ecdsa_us')}")
        parts.append(f"{T('sign.t_attempts')} {st.get('attempts', 0)}")
        if st.get("ecc_rng"):
            parts.append(T("sign.t_ecc_rng"))
        return f"{T('sign.timings')}: " + ",  ".join(parts)

    def _into_copy(self) -> bool:
        """Whether the chosen destination is a signed copy."""
        if not self.prep or self.prep.get("container", "detached") == "detached" or self.dest is None:
            return False
        return not self.dest.name.endswith((".zirah.json", ".zirah-sim.json"))

    def verify_now(self) -> None:
        if self.out and self.prep:
            if self.out.get("embedded"):
                self.ctx.verify_now(Path(self.out["path"]), None)
            else:
                self.ctx.verify_now(Path(self.prep["folder"]) / self.prep["name"], Path(self.out["path"]))

    # -- state from the window -------------------------------------------------

    def refresh(self, job: Optional[dict] = None) -> None:
        state = self.ctx.conn_state()
        sim = self.ctx.simulated()
        if self.prep:
            copy = self._into_copy()
            self.d_caption.setText(T("sign.saves_as_copy") if copy else T("sign.saves_as"))
            if self.dest is not None:
                self.d_name.setText(self.dest.name)
                self.d_folder.set_full(T("sign.in_folder", folder=str(self.dest.parent)))
                self.d_kept.setText(T("sign.original_kept_copy" if copy else "sign.original_kept",
                                      name=self.prep["name"]))
            else:
                self.d_kept.setText("")
                self.d_name.setText(T("sign.choose_dest"))
                self.d_folder.set_full("")
        ready = state in SIGNABLE and not self.running
        already = bool(self.prep and self.prep.get("embedded"))
        self.sign_btn.setEnabled(ready and self.prep is not None and not already)
        self.sign_btn.setText(T("sign.button_sim") if sim else T("sign.button"))
        self.other_btn.setEnabled(not self.running)
        reason = self.ctx.block_reason()
        if already and not self.running:
            self.note.setText(T("sign.already_signed"))
        else:
            self.note.setText("" if (state in SIGNABLE or self.running) else T("sign.not_ready", reason=reason))
        self.actions.setVisible(not self.running)
        self.live.setVisible(self.running)
        if self.running:
            j = job or {}
            stage = j.get("stage", "")
            confirm = stage == "CONFIRM"
            if j.get("oled") and j["oled"] != self.digest.text:
                self.digest.set_text(j["oled"])          # the digits the device will show
            if confirm and self.prep:
                btn = core.button_name((self.ctx.device() or {}).get("board", ""))
                self.prompt.setText(T("rail.confirm_prompt", short=j.get("oled") or "?", btn=btn))
                self.prompt_sub.setText(T("rail.confirm_sub_sim") if sim else T("rail.confirm_sub", btn=btn))
            elif stage:
                self.prompt.setText(T(f"stages.{stage}.long"))
                self.prompt_sub.setText(T("rail.cancel_late"))
            self.cancel_btn.setVisible(confirm and bool(j.get("cancellable")))
            self.press_btn.setVisible(confirm and sim)
            self.ring.setVisible(confirm)
