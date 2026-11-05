"""The Zirah main window: an instrument faceplate.

Layout, left to right:
  * Spine: the logo, the connection lamp and state, the device identity,
    key fingerprints and trust, the standing toggles and a Diagnostics
    button. It is the one place that is always visible.
  * Deck: the signal window at the top (the one bold element), the stage
    rail beneath it, then the four work pages (Sign, Verify, Device,
    History) under their tabs.

The window owns the device manager and routes every job event to whichever
page is tracking that job. Pages never touch the device or the file system
directly; they go through the AppContext methods here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QMainWindow, QMessageBox, QScrollArea,
                               QStackedLayout, QVBoxLayout, QWidget)

import theme as th
import zirah_core as core
from theme import Motion, T, error_text
from widgets import (BrandLockup, Button, CheckList, Lamp, Scope, StageRail, TabStrip, ToastHost, Toggle,
                     crossfade, fade_in, hbox, label, seam, vbox)

from page_device import DevicePage
from page_history import HistoryPage
from page_sign import SignPage
from page_verify import VerifyPage
from trust_dialog import TrustDialog

HERE = Path(__file__).resolve().parent
LAMP_FOR = {"DISCONNECTED": "off", "SEARCHING": "search", "CONNECTING": "search", "HANDSHAKE": "search",
            "SELF_CHECK": "search", "READY": "ready", "DEGRADED": "warn", "NEEDS_TRUST": "warn",
            "SIMULATED": "sim", "BUSY": "busy", "ERROR": "error"}


def app_icon() -> QIcon:
    """Every rendered size, so Windows never scales a single bitmap."""
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256, 512):
        f = HERE / "assets" / f"icon-{s}.png"
        if f.exists():
            icon.addFile(str(f), QSize(s, s))
    return icon


def dark_title_bar(win) -> None:
    """Windows 10/11: a dark title bar in Obsidian instead of the light default."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.c_void_p(int(win.winId()))
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        colour = ctypes.c_uint(0x00100B08)   # COLORREF of #080B10 (0x00BBGGRR)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), ctypes.sizeof(colour))
    except Exception:
        pass


class Bridge(QObject):
    """Carries manager events from the device thread onto the Qt thread."""
    event = Signal(str, object)


class MainWindow(QMainWindow):
    def __init__(self, manager, settings, history):
        super().__init__()
        self.manager = manager
        self.settings = settings
        self.history = history
        self.snapshot: dict = manager.snapshot()
        self._trackers: Dict[str, Callable[[dict], None]] = {}
        self._last_state = ""

        self.setWindowTitle("Zirah")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1080, 760)
        self.resize(1320, 860)

        Motion.reduce = bool(settings.get("reduce_motion"))

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.spine = self._build_spine()
        row.addWidget(self.spine)
        row.addWidget(self._build_deck(), 1)

        self.toasts = ToastHost(root)
        self._rail_reset = QTimer(self)
        self._rail_reset.setSingleShot(True)
        self._rail_reset.timeout.connect(lambda: self.rail.set_job(None, "", [], "", False))

        # Manager events arrive on its thread; the bridge hands them to Qt.
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        manager.bus.subscribe_callback(lambda kind, data: self.bridge.event.emit(kind, data))

        self._apply_snapshot()
        QTimer.singleShot(60, self._intro)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_spine(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Spine")
        w.setFixedWidth(300)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 24, 22, 18)
        lay.setSpacing(12)

        lay.addWidget(BrandLockup(T("app.tagline")))
        lay.addSpacing(4)
        lay.addWidget(seam())

        self.lamp = Lamp()
        self.state_title = label("", "", "lamp")
        self.state_detail = label("", "muted", "small", wrap=True)
        lay.addLayout(hbox(self.lamp, vbox(self.state_title, spacing=0), "stretch", spacing=12))
        lay.addWidget(self.state_detail)
        self.trust_btn = Button(T("spine.trust_device"), "blue")
        self.trust_btn.clicked.connect(self.open_trust)
        self.conn_btn = Button("", "secondary")
        self.conn_btn.clicked.connect(self._toggle_connection)
        lay.addLayout(hbox(self.trust_btn, self.conn_btn, spacing=8))
        lay.addWidget(seam())

        # Identity: one compact line per field.
        self.id_fw = label("", "", "mono_small")
        self.id_uid = label("", "", "mono_small")
        self.pq_fp = label("", "", "mono")
        self.ec_fp = label("", "", "mono")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        for row, (cap, val) in enumerate(((T("spine.firmware"), self.id_fw), (T("spine.device"), self.id_uid),
                                          (T("spine.pq"), self.pq_fp), (T("spine.ec"), self.ec_fp))):
            grid.addWidget(label(cap, "muted", "small"), row, 0)
            grid.addWidget(val, row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)
        self.ident_box = QWidget()
        self.ident_box.setLayout(grid)
        self.checks_box = QWidget()
        self.checklist = CheckList(["kat", "trng", "key", "signing", "trusted", "confirm", "atecc",
                                    "rdp"])
        self.checks_box.setLayout(vbox(6, label(T("spine.readiness"), "muted", "label"), self.checklist,
                                       spacing=6))
        # Identity and readiness scroll only if the window is very short.
        middle = QWidget()
        middle.setLayout(vbox(self.ident_box, self.checks_box, "stretch", spacing=10))
        mid_area = QScrollArea()
        mid_area.setWidgetResizable(True)
        mid_area.setWidget(middle)
        lay.addWidget(mid_area, 1)
        lay.addWidget(seam())

        self.t_auto = Toggle(T("spine.auto_connect"))
        self.t_auto.toggled.connect(lambda v: self._set("auto_connect", v))
        self.t_sim = Toggle(T("spine.simulation"))
        self.t_sim.toggled.connect(self._toggle_sim)
        self.t_motion = Toggle(T("spine.reduce_motion"))
        self.t_motion.toggled.connect(self._toggle_motion)
        self.t_history = Toggle(T("spine.keep_history"))
        self.t_history.toggled.connect(lambda v: self._set("keep_history", v))
        lay.addLayout(vbox(self.t_auto, self.t_sim, self.t_motion, self.t_history, spacing=4))
        diag = Button(T("spine.diagnostics"), "ghost")
        diag.clicked.connect(self.open_diagnostics)
        lay.addWidget(diag)
        return w

    def _build_deck(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Deck")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Signal window.
        head = QWidget()
        head.setObjectName("Panel")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(26, 18, 26, 8)
        hl.setSpacing(2)
        self.sim_ribbon = label(T("spine.sim_ribbon"), "warn", "small_strong")
        self.sim_ribbon.hide()
        # The noise pin depends on the board (PA1 on the Discovery, PA4 on the DevEBox).
        self.scope_sub = label(T("scope.subtitle_nopin"), "muted", "small")
        hl.addLayout(hbox(label(T("scope.title"), "", "title"),
                          self.scope_sub, "stretch", self.sim_ribbon,
                          spacing=12))
        self.scope = Scope()
        self.scope.shown.connect(self._update_readouts)
        hl.addWidget(self.scope, 1)
        # Numbers of the block on screen, straight from the device's PEEK reply.
        self.readouts = {}
        row = QHBoxLayout()
        row.setContentsMargins(64, 2, 0, 4)
        row.setSpacing(28)
        for key in ("std", "mean", "range", "clipped", "block", "rate"):
            cap = label(T(f"scope.{key}"), "muted", "small")
            val = label("—", "", "mono_small")
            row.addLayout(hbox(cap, val, spacing=8))
            self.readouts[key] = val
        row.addStretch(1)
        hl.addLayout(row)
        head.setFixedHeight(312)
        lay.addWidget(head)
        lay.addWidget(seam())

        rail_box = QWidget()
        rail_box.setObjectName("Panel")
        rl = QVBoxLayout(rail_box)
        rl.setContentsMargins(26, 6, 26, 2)
        self.rail = StageRail()
        rl.addWidget(self.rail)
        lay.addWidget(rail_box)
        lay.addWidget(seam())

        # Tabs + pages.
        self.tabs = TabStrip([T("tabs.sign"), T("tabs.verify"), T("tabs.device"), T("tabs.history")])
        self.tabs.changed.connect(self._on_tab)
        tab_box = QWidget()
        tb = QHBoxLayout(tab_box)
        tb.setContentsMargins(26, 4, 26, 0)
        tb.addWidget(self.tabs)
        tb.addStretch(1)
        lay.addWidget(tab_box)
        lay.addWidget(seam())

        self.pages = QStackedLayout()
        self.page_sign = SignPage(self)
        self.page_verify = VerifyPage(self)
        self.page_device = DevicePage(self)
        self.page_history = HistoryPage(self)
        self.scrolls = {}
        for p in (self.page_sign, self.page_verify, self.page_device, self.page_history):
            holder = QWidget()
            holder.setObjectName("Panel")
            hv = QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            if p in (self.page_sign, self.page_verify):
                # These pages grow (results, reports); they scroll rather than squeeze.
                area = QScrollArea()
                area.setWidgetResizable(True)
                area.setWidget(p)
                hv.addWidget(area)
                self.scrolls[p] = area
            else:
                hv.addWidget(p)
            self.pages.addWidget(holder)
        self.page_host = QWidget()
        self.page_host.setObjectName("Panel")
        self.page_host.setLayout(self.pages)
        lay.addWidget(self.page_host, 1)
        return w

    # ------------------------------------------------------------------
    # Intro sequence (one orchestrated moment on launch)
    # ------------------------------------------------------------------

    def _intro(self) -> None:
        self.scope.intro()
        self.rail.intro()
        if not Motion.reduce:
            fade_in(self.spine, 320)

    # ------------------------------------------------------------------
    # Events from the device manager
    # ------------------------------------------------------------------

    def _on_event(self, kind: str, data) -> None:
        if kind == "state":
            self.snapshot = data
            self._apply_snapshot()
        elif kind == "peek":
            if data is None:
                self.scope.clear(self._scope_reason())
                self._update_readouts(None)
            else:
                self.scope.push(data)    # the readouts follow when its sweep starts
        elif kind == "job":
            self._route_job(data)

    RAIL_JOBS = ("sign", "keygen", "confirm")

    def _update_readouts(self, b: Optional[dict]) -> None:
        if not b:
            for v in self.readouts.values():
                v.setText("—")
            return
        self.readouts["std"].setText(f"{b['std_x10'] / 10:.1f} {T('scope.lsb')}")
        self.readouts["mean"].setText(f"{b['mean']}")
        self.readouts["range"].setText(f"{b['min']} to {b['max']}")
        self.readouts["clipped"].setText(f"{b['clipped']}")
        self.readouts["block"].setText(f"{b['seq']}")
        self.readouts["rate"].setText(T("scope.khz", value=f"{b['rate'] / 1000:g}"))

    def _route_job(self, job: dict) -> None:
        if job.get("kind") in self.RAIL_JOBS:
            running = job["status"] == "running"
            self.rail.set_job(job.get("stages"), job.get("stage", ""), job.get("done", []),
                              job.get("failed", ""), running)
            if running:
                self._rail_reset.stop()
            else:
                self._rail_reset.start(7000)
        cb = self._trackers.get(job["id"])
        if cb:
            cb(job)
            if job["status"] != "running":
                self._trackers.pop(job["id"], None)

    def track(self, job_id: str, cb: Callable[[dict], None]) -> None:
        self._trackers[job_id] = cb

    def _apply_snapshot(self) -> None:
        conn = self.snapshot.get("conn", {})
        state = conn.get("state", "DISCONNECTED")
        sim = conn.get("simulated", False)
        self.lamp.set_mode(LAMP_FOR.get(state, "off"))
        self.state_title.setText(T(f"conn.{state}.label") if th.has(f"conn.{state}.label") else state)
        self.state_detail.setText(self._state_detail(state, conn))
        linked = self.snapshot.get("device") is not None
        if state == "DISCONNECTED" or (state == "ERROR" and not linked):
            self.conn_btn.setText(T("spine.connect"))
        elif state in ("SEARCHING", "CONNECTING"):
            self.conn_btn.setText(T("spine.stop_search"))
        else:
            self.conn_btn.setText(T("spine.disconnect"))
        self.conn_btn.setVisible(not sim)
        trust = (self.snapshot.get("keys") or {}).get("trust")
        self.trust_btn.setVisible(not sim and trust in ("unpinned", "changed") and state != "BUSY")
        self.trust_btn.setText(T("device.replace") if trust == "changed" else T("spine.trust_device"))
        self.sim_ribbon.setVisible(sim)

        dev = self.snapshot.get("device")
        keys = self.snapshot.get("keys")
        pin = (dev or {}).get("noise_pin") or ("PA1" if dev and dev.get("board") == "discovery" else "")
        self.scope_sub.setText(T("scope.subtitle", pin=pin) if pin and not sim else T("scope.subtitle_nopin"))
        self.ident_box.setVisible(dev is not None)
        # In Simulation Mode there is no hardware to check, so no checklist.
        self.checks_box.setVisible(dev is not None and not sim)
        if dev is not None and self.snapshot.get("checks"):
            self.checklist.set_checks(self.snapshot["checks"])
        if dev:
            self.id_fw.setText(dev.get("firmware", "—"))
            uid = dev.get("uid", "")
            self.id_uid.setText(f"{uid[:4]}…{uid[-8:]}" if uid else "—")
            self.id_uid.setToolTip(uid)
        if keys:
            self.pq_fp.setText(keys.get("pq_fp", "—"))
            self.ec_fp.setText(keys.get("ec_fp") or T("spine.none"))
        else:
            for w in (self.pq_fp, self.ec_fp):
                w.setText("—")

        s = self.snapshot.get("settings", {})
        self.t_auto.set_quiet(s.get("auto_connect", True))
        self.t_sim.set_quiet(sim)
        self.t_motion.set_quiet(s.get("reduce_motion", False))
        self.t_history.set_quiet(s.get("keep_history", True))

        if not self.snapshot.get("peek") and state in ("DISCONNECTED", "ERROR", "SEARCHING"):
            self.scope.clear(self._scope_reason())
            self._update_readouts(None)

        for p in (self.page_sign, self.page_verify, self.page_device):
            if hasattr(p, "refresh"):
                p.refresh()
        self._toast_on_state_change(state, conn)
        self._last_state = state

    def _state_detail(self, state: str, conn: dict) -> str:
        if state == "ERROR" and conn.get("error"):
            title, _ = error_text(conn["error"])
            return title
        if state == "DEGRADED":
            warn = next((c for c in self.snapshot.get("checks", []) if not c["ok"] and not c["required"]),
                        None)
            # Name the problem ("OLED Missing"), not the check ("OLED Present").
            return T("conn.DEGRADED.detail", warning=T(f"checks.{warn['key']}.fail") if warn else "")
        if state == "CONNECTING":
            return T("conn.CONNECTING.detail", port=conn.get("port", ""))
        if state == "BUSY":
            job = self.snapshot.get("job") or {}
            return T("conn.BUSY.detail", job=T(f"jobs.{job.get('kind', '')}")
                     if th.has(f"jobs.{job.get('kind', '')}") else "")
        key = f"conn.{state}.detail"
        return T(key) if th.has(key) else ""

    def _scope_reason(self) -> str:
        state = self.snapshot.get("conn", {}).get("state", "DISCONNECTED")
        dev = self.snapshot.get("device")
        if state in ("DISCONNECTED", "ERROR"):
            return T("scope.disconnected")
        if dev and dev.get("trng") and dev["trng"] != "OK":
            return T("scope.fault", state=dev["trng"])
        if state in ("SEARCHING", "CONNECTING", "HANDSHAKE", "SELF_CHECK"):
            return T("conn.SEARCHING.detail")
        return T("scope.waiting")

    def _toast_on_state_change(self, state: str, conn: dict) -> None:
        if state == self._last_state or not self._last_state:
            return
        if state in ("READY", "DEGRADED", "NEEDS_TRUST", "SIMULATED", "DISCONNECTED"):
            self._toasted_error = ""
        if state == "ERROR" and conn.get("error"):
            # Auto-connect retries in the background; the same error is announced once.
            code = conn["error"].get("code", "")
            if code != getattr(self, "_toasted_error", ""):
                self._toasted_error = code
                title, hint = error_text(conn["error"])
                self.toast(title, hint, "err")
        elif state in ("READY", "DEGRADED") and self._last_state in ("SEARCHING", "CONNECTING",
                                                                     "HANDSHAKE", "SELF_CHECK", "ERROR"):
            self.toast(T("toast.connected", port=conn.get("port", "")), "", "ok")
        elif state == "DISCONNECTED" and self._last_state not in ("", "CONNECTING", "SEARCHING"):
            self.toast(T("toast.disconnected"), "", "warn")

    # ------------------------------------------------------------------
    # AppContext: what the pages call
    # ------------------------------------------------------------------

    def simulated(self) -> bool:
        return bool(self.snapshot.get("conn", {}).get("simulated"))

    def scroll_to(self, page, widget) -> None:
        area = self.scrolls.get(page)
        if area is not None:
            QTimer.singleShot(60, lambda: area.ensureWidgetVisible(widget, 0, 24))

    def conn_state(self) -> str:
        return self.snapshot.get("conn", {}).get("state", "DISCONNECTED")

    def device(self) -> Optional[dict]:
        return self.snapshot.get("device")

    def keys(self) -> Optional[dict]:
        return self.snapshot.get("keys")

    def checks(self) -> list:
        return self.snapshot.get("checks") or []

    def busy(self) -> bool:
        return self.conn_state() == "BUSY" or self.snapshot.get("job") is not None

    def reduced(self) -> bool:
        return Motion.reduce

    def block_reason(self) -> str:
        checks = self.snapshot.get("checks", [])
        bad = next((c for c in checks if c["required"] and not c["ok"] and c["key"] != "trusted"), None)
        if bad:
            return bad["title"]
        if any(c["key"] == "trusted" and not c["ok"] for c in checks):
            return T("conn.NEEDS_TRUST.detail")
        state = self.conn_state()
        return T(f"conn.{state}.label") if th.has(f"conn.{state}.label") else state

    def ask_open(self, purpose: str) -> Optional[Path]:
        kinds = {"sign": (T("sign.choose"), "any"), "verify-doc": (T("verify.choose_doc"), "any"),
                 "verify-sig": (T("verify.choose_sig"), "signature"), "keys": (T("verify.choose_keys"),
                                                                               "keys")}
        title, kind = kinds[purpose]
        filters = {"any": "All Files (*)", "signature": "Zirah Signatures (*.zirah.json *.zirah-sim.json);;All Files (*)",
                   "keys": "Zirah Public Keys (*.json);;All Files (*)"}[kind]
        path, _ = QFileDialog.getOpenFileName(self, title, str(Path.home()), filters)
        return Path(path) if path else None

    def ask_save(self, title: str, suggested: Path, container: str = "detached",
                 doc: Optional[Path] = None) -> Optional[Path]:
        """Where to save the signature: a signed copy keeps the file's
        ending; a name ending in .zirah.json asks for a separate file instead."""
        sim = self.simulated()
        sig_suffix = core.SIM_SIG_SUFFIX if sim else core.SIG_SUFFIX
        ext = doc.suffix if doc is not None else ""
        filters = "Zirah Signatures (*.json)" if container == "detached" else \
            f"Signed Copy (*{ext});;Separate Zirah Signature (*.json)"
        path, chosen = QFileDialog.getSaveFileName(self, title, str(suggested), filters,
                                                   options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return None
        p = Path(path)
        detached = container == "detached" or p.name.endswith((core.SIG_SUFFIX, core.SIM_SIG_SUFFIX)) \
            or chosen.startswith("Separate")
        if detached:
            if not p.name.endswith(sig_suffix):
                stem = p.name[:-5] if p.name.endswith(".json") else p.name
                p = p.with_name(stem + sig_suffix)
            kind = "detached"
        else:
            if p.suffix.lower() != ext.lower():
                p = p.with_name(p.name + ext)
            if sim and ".signed-sim" not in p.name:
                p = p.with_name(p.name[:len(p.name) - len(ext)] + ".signed-sim" + ext)
            kind = container
        try:
            core.check_destination(p, sim, kind, doc)
        except core.ZirahError as e:
            self.toast_error(e.as_dict())
            return None
        return p

    def reveal(self, path: Path) -> None:
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError:
            pass

    def toast(self, title: str, detail: str = "", kind: str = "info") -> None:
        self.toasts.show_toast(title, detail, kind)

    def toast_error(self, err: Optional[dict]) -> None:
        title, hint = error_text(err or {})
        self.toasts.show_toast(title, hint, "err")

    def confirm_box(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Zirah")
        box.setText(title)
        box.setInformativeText(text)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def open_trust(self) -> None:
        keys = self.keys()
        dev = self.device()
        if not keys or not dev:
            return
        dlg = TrustDialog(self, dev.get("uid", ""), keys)
        if dlg.exec():
            res = self.manager.trust_device(dlg.typed(), replace=keys.get("trust") == "changed")
            if "error" in res:
                self.toast_error(res["error"])
            else:
                self.toast(T("trust.done"), "", "ok")

    def export_keys(self) -> None:
        devices = self.manager.trust_hw.devices
        if not devices:
            self.toast_error({"code": "NO_TRUSTED"})
            return
        path, _ = QFileDialog.getSaveFileName(self, T("device.export"),
                                              str(Path.home() / "zirah-public-keys.json"),
                                              "JSON (*.json)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        try:
            p = Path(path)
            if p.exists():
                raise core.ZirahError("EXISTS", "A file with that name already exists")
            core.export_public_keys(p, devices)
            self.toast(T("toast.keys_exported"), p.name, "ok")
        except core.ZirahError as e:
            self.toast_error(e.as_dict())

    def verify_now(self, doc: Path, sig: Optional[Path]) -> None:
        """sig None: doc is a signed copy that carries its own signature."""
        self.page_verify.load(doc, sig)
        self.tabs.set_index(1)

    def history_changed(self) -> None:
        self.page_history.refresh()

    def open_diagnostics(self) -> None:
        from diag_dialog import DiagnosticsDialog
        DiagnosticsDialog(self).exec()

    # ------------------------------------------------------------------
    # Spine controls
    # ------------------------------------------------------------------

    def _on_tab(self, index: int) -> None:
        def switch():
            if index == 3:
                self.page_history.refresh()
            self.pages.setCurrentIndex(index)
        if self.pages.currentIndex() != index:
            crossfade(self.page_host, switch, 220)
        else:
            switch()

    def _toggle_connection(self) -> None:
        state = self.conn_state()
        # "Not Ready" with a board attached is a connected device that still
        # needs a key or trust; the button then disconnects, it never reconnects.
        if state == "DISCONNECTED" or (state == "ERROR" and self.device() is None):
            self.manager.req_connect()
        else:
            self.manager.req_disconnect()

    def _toggle_sim(self, on: bool) -> None:
        self.manager.req_simulation(on)
        self.toast(T("toast.sim_on") if on else T("toast.sim_off"), "", "sim" if on else "info")

    def _toggle_motion(self, on: bool) -> None:
        Motion.reduce = on
        self._set("reduce_motion", on)

    def _set(self, key: str, value) -> None:
        self.settings.update({key: value})
        self.manager.publish_state()

    def showEvent(self, e):
        super().showEvent(e)
        dark_title_bar(self)
        if not getattr(self, "_warmed", False):
            # Lay out and style every page once, a little after the window
            # appears, so the first visit to a tab does not stall a frame.
            self._warmed = True
            for n, page in enumerate(self._all_pages()):
                QTimer.singleShot(1200 + 150 * n, lambda pg=page: self._warm(pg))
        # Start with focus on the tabs without a keyboard ring; Qt would
        # otherwise focus the first toggle as if it had been tabbed to.
        QTimer.singleShot(0, lambda: self.tabs.setFocus(Qt.FocusReason.OtherFocusReason))

    def closeEvent(self, e):
        if self.busy() and not self.confirm_box(T("app.quit_busy_title"), T("app.quit_busy_text")):
            e.ignore()
            return
        self.manager.stop()
        super().closeEvent(e)

    def nativeEvent(self, event_type, message):
        # Windows tells every top-level window when a device is plugged in or
        # removed (WM_DEVICECHANGE). That is the moment to look for the board,
        # so the background search can stay slow the rest of the time.
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0219 and msg.wParam in (0x0007, 0x8000, 0x8004):
                    self.manager.req_rescan()
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def _all_pages(self):
        return [self.pages.widget(i) for i in range(self.pages.count())]

    @staticmethod
    def _warm(page) -> None:
        try:
            if not page.isVisible():
                page.ensurePolished()
                for w in page.findChildren(QWidget):
                    w.ensurePolished()
                page.resize(page.parentWidget().size() if page.parentWidget() else page.size())
                if page.layout() is not None:
                    page.layout().activate()
        except RuntimeError:
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toasts"):
            self.toasts.relayout()
