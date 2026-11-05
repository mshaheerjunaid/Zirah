"""Device page: self-check, keys and trust, entropy source, firmware, button."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QScrollArea, QVBoxLayout, QWidget

import theme as th
from PySide6.QtCore import Qt
from common import Cell, kv_row
from theme import T
from widgets import Button, Chip, StatusIcon, Toggle, hbox, label

BENCH_ROWS = (("trng32_us", "device.bench_trng", 1000), ("refresh_us", "device.bench_refresh", 1000),
              ("sign_us", "device.bench_sign", 1000), ("verify_us", "device.bench_verify", 1000),
              ("attempts", "device.bench_attempts", 1))


class StepRow(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.icon = StatusIcon()
        self.title = label(title, "", "body")
        self.detail = label("", "muted", "small")
        self.setLayout(hbox(self.icon, self.title, "stretch", self.detail, spacing=10))


class DevicePage(QWidget):
    STEPS = ["LINK", "FIRMWARE", "KAT", "HEALTH", "SIGNAL", "KEYS", "IDENTITY", "HARDENING", "RDP",
             "ATECC"]

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.job_kind = ""
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        area.setWidget(inner)
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(24, 22, 24, 24)
        # The cells sit in a rack: one-pixel seams between them, no floating cards.
        rack = QWidget()
        rack.setObjectName("Rack")
        outer.addWidget(rack)
        outer.addStretch(1)
        self.grid = QGridLayout(rack)
        self.grid.setContentsMargins(1, 1, 1, 1)
        self.grid.setHorizontalSpacing(1)
        self.grid.setVerticalSpacing(1)
        self.grid.addWidget(self._selfcheck_cell(), 0, 0, 1, 2)
        self.grid.addWidget(self._keys_cell(), 1, 0)
        self.grid.addWidget(self._entropy_cell(), 1, 1)
        self.grid.addWidget(self._sources_cell(), 2, 0)
        self.grid.addWidget(self._firmware_cell(), 2, 1)
        self.grid.addWidget(self._confirm_cell(), 3, 0)
        self.grid.addWidget(self._bench_cell(), 3, 1)
        self.sim_cell = self._sim_cell()
        self.grid.addWidget(self.sim_cell, 4, 0, 1, 2)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.empty = label(T("device.no_device"), "muted", "body")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.empty)
        root.addWidget(area)
        self.area = area
        self.refresh()

    # -- cells -----------------------------------------------------------------

    def _selfcheck_cell(self) -> Cell:
        c = Cell(T("device.selfcheck"), T("device.selfcheck_sub"))
        self.steps = [StepRow(T(f"device.steps.{k}")) for k in self.STEPS]
        self.sc_btn = Button(T("device.run_selfcheck"), "primary")
        self.sc_btn.clicked.connect(self.run_selfcheck)
        self.sc_summary = Chip("", "muted")
        self.sc_summary.hide()
        grid = QGridLayout()
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(4)
        for i, row in enumerate(self.steps):
            grid.addWidget(row, i % 5, i // 5)
        c.body.addLayout(grid)
        c.body.addLayout(hbox(self.sc_btn, self.sc_summary, "stretch", spacing=12))
        return c

    def _keys_cell(self) -> Cell:
        c = Cell(T("device.keys"), T("device.keys_sub"))
        self.pq_fp = label("", "", "mono")
        self.ec_fp = label("", "", "mono")
        self.trust_chip = Chip("", "muted")
        c.body.addLayout(kv_row(T("device.pq"), self.pq_fp))
        c.body.addLayout(kv_row(T("device.ec"), self.ec_fp))
        c.body.addLayout(kv_row(T("spine.trust"), self.trust_chip))
        self.trust_btn = Button(T("device.trust"), "blue")
        self.trust_btn.clicked.connect(self.ctx.open_trust)
        self.export_btn = Button(T("device.export"), "secondary")
        self.export_btn.clicked.connect(self.ctx.export_keys)
        self.keygen_btn = Button(T("device.generate"), "ghost")
        self.keygen_btn.clicked.connect(self.keygen)
        c.body.addLayout(hbox(self.trust_btn, self.export_btn, self.keygen_btn, "stretch", spacing=10))
        return c

    def _entropy_cell(self) -> Cell:
        c = Cell(T("device.entropy"), T("device.entropy_sub"))
        self.e_state = Chip("", "muted")
        self.e_symbols = label("", "", "mono")
        self.e_fails = label("", "", "mono")
        self.e_boost = label("", "", "mono")
        self.e_assumed = label("", "", "body")
        self.e_temp = label("", "", "mono")
        c.body.addLayout(kv_row(T("device.state"), self.e_state))
        c.body.addLayout(kv_row(T("device.boost"), self.e_boost))
        c.body.addLayout(kv_row(T("device.symbols"), self.e_symbols))
        c.body.addLayout(kv_row(T("device.failures"), self.e_fails))
        c.body.addLayout(kv_row(T("device.assumed"), self.e_assumed))
        c.body.addLayout(kv_row(T("device.temp"), self.e_temp))
        self.health_btn = Button(T("device.run_health"), "secondary")
        self.health_btn.clicked.connect(self.health)
        c.body.addLayout(hbox(self.health_btn, "stretch"))
        return c

    def _sources_cell(self) -> Cell:
        c = Cell(T("device.sources"), T("device.sources_sub"))
        self.e_source_b = Chip("", "muted")
        self.e_corr = label("", "", "mono")
        self.e_ecc = Chip("", "muted")
        self.e_mcu = Chip("", "muted")
        self.e_jit = Chip("", "muted")
        self.e_acc = Chip("", "muted")
        c.body.addLayout(kv_row(T("device.source_b"), self.e_source_b))
        c.body.addLayout(kv_row(T("device.corr"), self.e_corr))
        c.body.addLayout(kv_row(T("device.ecc_rng"), self.e_ecc))
        c.body.addLayout(kv_row(T("device.mcu_rng"), self.e_mcu))
        c.body.addLayout(kv_row(T("device.jitter"), self.e_jit))
        c.body.addLayout(kv_row(T("device.accel"), self.e_acc))
        return c

    def _confirm_cell(self) -> Cell:
        c = Cell(T("device.confirm"), T("device.confirm_sub"))
        self.confirm_toggle = Toggle(T("device.confirm"))
        self.confirm_toggle.toggled.connect(self.toggle_confirm)
        c.body.addWidget(self.confirm_toggle)
        return c

    def _firmware_cell(self) -> Cell:
        c = Cell(T("device.firmware"))
        self.fw_version = label("", "", "mono")
        self.fw_board = label("", "", "body")
        self.fw_proto = label("", "", "mono")
        self.fw_kat = Chip("", "muted")
        self.fw_mpu = Chip("", "muted")
        self.fw_canary = Chip("", "muted")
        self.fw_stack = label("", "", "mono")
        self.fw_rdp = Chip("", "muted")
        self.fw_build = Chip("", "muted")
        self.fw_sigs = label("", "", "mono")
        c.body.addLayout(kv_row(T("device.board"), self.fw_board))
        c.body.addLayout(kv_row(T("device.version"), self.fw_version))
        c.body.addLayout(kv_row(T("device.protocol"), self.fw_proto))
        c.body.addLayout(kv_row(T("device.kat"), self.fw_kat))
        c.body.addLayout(kv_row(T("device.mpu"), hbox(self.fw_mpu, self.fw_canary, "stretch", spacing=8)))
        c.body.addLayout(kv_row(T("device.stack"), self.fw_stack))
        c.body.addLayout(kv_row(T("device.rdp"), self.fw_rdp))
        c.body.addLayout(kv_row(T("device.build"), self.fw_build))
        c.body.addLayout(kv_row(T("device.signatures"), self.fw_sigs))
        return c

    def _bench_cell(self) -> Cell:
        c = Cell(T("device.bench"), T("device.bench_sub"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(4)
        for col, key in enumerate(("device.bench_min", "device.bench_median", "device.bench_max"), start=1):
            grid.addWidget(label(T(key), "muted", "small"), 0, col, Qt.AlignmentFlag.AlignRight)
        self.bench_cells = {}
        for row, (key, title, _div) in enumerate(BENCH_ROWS, start=1):
            grid.addWidget(label(T(title), "", "body"), row, 0)
            cells = [label("—", "", "mono") for _ in range(3)]
            for col, cell in enumerate(cells, start=1):
                grid.addWidget(cell, row, col, Qt.AlignmentFlag.AlignRight)
            self.bench_cells[key] = cells
        grid.setColumnStretch(4, 1)
        c.body.addLayout(grid)
        self.bench_extra = label("", "muted", "small", wrap=True)
        self.bench_extra.hide()
        c.body.addWidget(self.bench_extra)
        self.bench_btn = Button(T("device.run_bench"), "secondary")
        self.bench_btn.clicked.connect(self.run_bench)
        self.bench_status = Chip("", "muted")
        self.bench_status.hide()
        c.body.addLayout(hbox(self.bench_btn, self.bench_status, "stretch", spacing=12))
        return c

    def _sim_cell(self) -> Cell:
        c = Cell(T("device.sim"), T("device.sim_sub"))
        press = Button(T("device.sim_press"), "secondary")
        press.clicked.connect(self.ctx.manager.press_sim_button)
        c.body.addLayout(hbox(press, "stretch", spacing=10))
        return c

    # -- actions ---------------------------------------------------------------

    def run_selfcheck(self) -> None:
        try:
            job = self.ctx.manager.self_check()
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "BUSY"})())
            return
        for row in self.steps:
            row.icon.set_state("pending")
            row.detail.setText("")
        self.sc_summary.hide()
        self.sc_btn.setEnabled(False)
        self.ctx.track(job, self._on_selfcheck)

    def _on_selfcheck(self, job: dict) -> None:
        result = job.get("result") or {}
        steps = {s["key"]: s for s in result.get("steps", [])}
        active = job.get("stage", "")
        for key, row in zip(self.STEPS, self.steps):
            if key in steps:
                s = steps[key]
                row.icon.set_state("pass" if s["ok"] else ("fail" if s["required"] else "warn"))
                row.detail.setText(self._step_detail(key, s["data"]))
            elif key == active and job["status"] == "running":
                row.icon.set_state("running")
            else:
                row.icon.set_state("pending")
        if job["status"] != "running":
            self.sc_btn.setEnabled(True)
            passed = result.get("passed")
            if job["status"] == "done":
                self.sc_summary.setText(T("device.selfcheck_pass") if passed else T("device.selfcheck_fail"))
                self.sc_summary.set_kind("ok" if passed else "err")
                self.sc_summary.show()
                self.ctx.toast(T("toast.selfcheck_done"), "", "ok" if passed else "warn")
            else:
                self.ctx.toast_error(job.get("error"))

    @staticmethod
    def _step_detail(key: str, data: dict) -> str:
        if key == "FIRMWARE":
            return f"{data.get('firmware', '')}  proto {data.get('protocol', '')}"
        if key == "SIGNAL":
            return f"std {data.get('std_x10', 0) / 10:.1f}  clip {data.get('clipped', 0)}"
        if key == "HARDENING":
            return f"stack {data.get('stack', 0) // 1024} KB" if data.get("stack") else ""
        if key == "KEYS":
            return data.get("pq_fp", "")
        if key == "RDP":
            rdp = data.get("rdp", -1)
            return T(f"device.rdp_{rdp}") if rdp in (0, 1, 2) else ""
        if key == "ATECC":
            return data.get("atecc", "")
        return ""

    def run_bench(self) -> None:
        try:
            job = self.ctx.manager.bench(20)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "BUSY"})())
            return
        self.bench_btn.setEnabled(False)
        self.bench_status.setText(T("device.bench_running", done="0", of="20"))
        self.bench_status.set_kind("muted")
        self.bench_status.show()
        self.ctx.track(job, self._on_bench)

    def _on_bench(self, job: dict) -> None:
        if job["status"] == "running":
            f = job.get("fields") or {}
            self.bench_status.setText(T("device.bench_running", done=f.get("done", "0"), of=f.get("of", "20")))
            return
        self.bench_btn.setEnabled(not self.ctx.busy() and not self.ctx.simulated())
        if job["status"] != "done":
            self.bench_status.hide()
            self.ctx.toast_error(job.get("error"))
            return
        res = job["result"]["bench"]
        for key, _title, div in BENCH_ROWS:
            st = res["stages"].get(key, {})
            for cell, k in zip(self.bench_cells[key], ("min", "median", "max")):
                v = st.get(k)
                cell.setText("—" if v is None else (f"{v}" if div == 1 else T("sign.ms", value=f"{v / div:.1f}")))
        vals = res.get("values", {})
        parts = []
        kg = vals.get("keygen_us", {}).get("value")
        if kg:
            parts.append(T("device.bench_keygen", value=f"{kg / 1000:.1f}"))
        for key, name in (("ntt_cycles", "device.bench_ntt"), ("invntt_cycles", "device.bench_invntt")):
            c = vals.get(key, {})
            if c.get("signer") and c.get("c"):
                parts.append(T(name, fast=str(c["signer"]), slow=str(c["c"]),
                               ratio=f"{c['c'] / c['signer']:.1f}"))
        self.bench_extra.setText(",  ".join(parts))
        self.bench_extra.setVisible(bool(parts))
        self.bench_status.setText(T("device.bench_done"))
        self.bench_status.set_kind("ok")
        path = job["result"].get("path")
        self.ctx.toast(T("toast.bench_done"), T("toast.bench_saved") if path else "", "ok")

    def keygen(self) -> None:
        dev = self.ctx.device()
        force = bool(dev and dev.get("key") in ("present", "stored"))
        if force and not self.ctx.confirm_box(T("device.regenerate"),
                                              T("device.regenerate_text")):
            return
        try:
            job = self.ctx.manager.keygen(force)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "BUSY"})())
            return
        self.ctx.track(job, self._on_keygen)

    def _on_keygen(self, job: dict) -> None:
        if job["status"] == "done":
            self.ctx.toast(T("toast.keygen_done"), "", "warn")
        elif job["status"] == "failed":
            self.ctx.toast_error(job.get("error"))

    def health(self) -> None:
        try:
            job = self.ctx.manager.health()
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "BUSY"})())
            return
        self.ctx.track(job, self._on_health)

    def _on_health(self, job: dict) -> None:
        if job["status"] == "done":
            ok = (job["result"].get("trng") == "OK")
            self.ctx.toast(T("toast.health_ok") if ok else T("errors.ENTROPY_FAULT.title"), "",
                           "ok" if ok else "err")
        elif job["status"] == "failed":
            self.ctx.toast_error(job.get("error"))

    def toggle_confirm(self, on: bool) -> None:
        try:
            job = self.ctx.manager.set_confirm(on)
        except Exception as e:
            self.ctx.toast_error(getattr(e, "as_dict", lambda: {"code": "BUSY"})())
            return
        self.ctx.track(job, self._on_confirm)

    def _on_confirm(self, job: dict) -> None:
        if job["status"] == "done":
            on = job["result"]["confirm"]
            self.ctx.toast(T("toast.confirm_on") if on else T("toast.confirm_off"), "", "ok" if on else "warn")
        elif job["status"] in ("failed", "cancelled"):
            self.ctx.toast_error(job.get("error"))
            self.refresh()   # the switch goes back to what the device reports

    # -- state -----------------------------------------------------------------

    def refresh(self) -> None:
        dev = self.ctx.device()
        keys = self.ctx.keys()
        sim = self.ctx.simulated()
        self.empty.setVisible(dev is None)
        self.area.setVisible(dev is not None)
        self.sim_cell.setVisible(sim)
        if dev is None:
            return
        busy = self.ctx.busy()
        self.pq_fp.setText(keys.get("pq_fp") if keys else "—")
        self.ec_fp.setText(keys.get("ec_fp") or T("spine.none") if keys else "—")
        trust = (keys or {}).get("trust", "unknown")
        self.trust_chip.setText(T(f"spine.{trust}") if th.has(f"spine.{trust}") else trust)
        self.trust_chip.set_kind({"trusted": "ok", "unpinned": "warn", "changed": "err"}.get(trust, "muted"))
        self.trust_btn.setText(T("device.replace") if trust == "changed" else T("device.trust"))
        self.trust_btn.setVisible(not sim and trust != "trusted")
        self.export_btn.setVisible(not sim)
        self.keygen_btn.setText(T("device.regenerate") if dev.get("key") in ("present", "stored")
                                else T("device.generate"))
        self.keygen_btn.setEnabled(not busy and not sim)
        self.trust_btn.setEnabled(not busy)

        trng = dev.get("trng", "")
        self.e_state.setText(trng or "—")
        self.e_state.set_kind("ok" if trng == "OK" else "err")
        health = dev.get("health", {})
        self.e_symbols.setText(health.get("symbols", "—"))
        self.e_fails.setText(health.get("fails_total", health.get("fails", "0")))
        self.e_boost.setText(f"{dev.get('vbias_mv', 0) / 1000:.2f} V" if not sim else "—")
        self.e_assumed.setText(dev.get("h_assumed", "—").replace("bits/symbol", "Bits/Symbol")
                               if not sim else "—")
        self.health_btn.setEnabled(not busy and not sim)
        sb = dev.get("source_b", "")
        if sim:
            self.e_source_b.setText(T("device.simulated"))
            self.e_source_b.set_kind("warn")
        else:
            self.e_source_b.setText(T(f"device.source_b_{sb}") if sb in ("absent", "present") else "—")
            self.e_source_b.set_kind("ok" if sb == "present" else "muted")
        corr = dev.get("corr_x1000")
        self.e_corr.setText(f"{corr / 1000:+.3f}" if corr is not None and sb == "present" and not sim else "—")
        temp = dev.get("temp_c10")
        self.e_temp.setText(T("device.celsius", value=f"{temp / 10:.1f}") if temp is not None and not sim else "—")
        ecc = dev.get("atecc", "")
        mcu = dev.get("stm32_rng", "")
        if sim:
            for chip in (self.e_ecc, self.e_mcu, self.e_jit, self.e_acc):
                chip.setText(T("device.simulated"))
                chip.set_kind("warn")
        else:
            self.e_ecc.setText(T("device.mcu_ok") if ecc == "ready" else
                               T("device.ecc_absent") if ecc == "absent" else T("device.ecc_not_ready"))
            self.e_ecc.set_kind("ok" if ecc == "ready" else "muted")
            self.e_mcu.setText(T(f"device.mcu_{mcu}") if mcu in ("ok", "fault", "off") else "—")
            self.e_mcu.set_kind({"ok": "ok", "fault": "warn"}.get(mcu, "muted"))
            for chip, key, absent in ((self.e_jit, "jitter", "device.jitter_absent"),
                                      (self.e_acc, "accel", "device.accel_absent")):
                state = dev.get(key, "")
                chip.setText({"live": T("device.mcu_ok"), "absent": T(absent),
                              "fault": T("device.mcu_fault")}.get(state, "—"))
                chip.set_kind({"live": "ok", "fault": "warn"}.get(state, "muted"))
        self.bench_btn.setEnabled(not busy and not sim)

        self.confirm_toggle.set_quiet(dev.get("confirm", True))
        self.confirm_toggle.setEnabled(not busy)

        self.fw_version.setText(dev.get("firmware", "—"))
        self.fw_board.setText(T("device.simulated") if sim else
                              T("device.board_devebox") if dev.get("board") == "devebox" else T("device.board_discovery"))
        self.fw_proto.setText(str(dev.get("protocol", "—")))
        self.fw_kat.setText(T("device.passed") if dev.get("kat_ok") else T("device.failed"))
        self.fw_kat.set_kind("ok" if dev.get("kat_ok") else "err")
        for chip, on in ((self.fw_mpu, dev.get("mpu")), (self.fw_canary, dev.get("canary"))):
            chip.set_kind("ok" if on else ("muted" if sim else "warn"))
        self.fw_mpu.setText("MPU " + (T("device.on") if dev.get("mpu") else T("device.off")))
        self.fw_canary.setText(T("device.canary") + " " + (T("device.on") if dev.get("canary")
                                                           else T("device.off")))
        self.fw_stack.setText(f"{dev.get('stack_bytes', 0) // 1024} KB" if dev.get("stack_bytes") else "—")
        rdp = dev.get("rdp", -1)
        self.fw_rdp.setText(T(f"device.rdp_{rdp}") if rdp in (0, 1, 2) and not sim else "—")
        self.fw_rdp.set_kind("muted" if sim or rdp not in (0, 1, 2) else ("warn" if rdp == 0 else "ok"))
        match = next((c for c in self.ctx.checks() if c.get("key") == "fwmatch"), None)
        fh = dev.get("fw_hash", "")
        if sim:
            self.fw_build.setText("—")
            self.fw_build.set_kind("muted")
        elif match is None:
            self.fw_build.setText(fh[:8] if fh else T("device.build_unknown"))
            self.fw_build.set_kind("muted")
        else:
            self.fw_build.setText((fh[:8] + "  " if fh else "") +
                                  T("device.build_match" if match["ok"] else "device.build_differs"))
            self.fw_build.set_kind("ok" if match["ok"] else "warn")
        self.fw_sigs.setText(str(dev.get("sigs", 0)))

        self.sc_btn.setEnabled(not busy)
