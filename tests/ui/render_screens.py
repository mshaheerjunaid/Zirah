#!/usr/bin/env python3
"""Drive the real desktop app through a full flow and save screenshots.

    python tests/ui/render_screens.py [output folder]

Runs the actual window offscreen against the simulated device (no hardware),
so every screenshot carries the Simulation ribbon and simulated markings. It
is also a smoke test: it exits with status 1 if any step of the flow fails
(hashing, signing, the PC-side checks, saving, verifying).

Screens: launch, file prepared, waiting for the USER button, signing in
progress, signed, verify report, device page after a self-check, history.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "host"))

WORK = Path(tempfile.mkdtemp(prefix="zirah-ui-"))
os.environ["ZIRAH_HOME"] = str(WORK / "home")

from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
import zirah_core as core  # noqa: E402
from manager import Bus, DeviceManager  # noqa: E402
from store import History, Settings  # noqa: E402
from window import MainWindow, app_icon  # noqa: E402


def sample_pdf(size: int) -> bytes:
    """A small, valid one-page PDF padded to about `size` bytes, so signing
    produces a signed copy as it would for a real file."""
    body = b"BT /F1 12 Tf 20 180 Td (Service Agreement) Tj ET\n" + b"% " + b"x" * size + b"\n"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(body) + body + b"endstream"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 5\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screens"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    theme.load_fonts()
    app.setStyleSheet(theme.QSS)
    app.setFont(theme.font("body"))
    settings = Settings()
    history = History(settings)
    mgr = DeviceManager(Bus(), settings, history)
    mgr.start()
    mgr.req_simulation(True)
    win = MainWindow(mgr, settings, history)
    win.resize(1360, 900)
    win.show()
    failures = []

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.015)

    def wait(cond, seconds: float, what: str) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        failures.append(what)
        return False

    def settle_toasts(seconds: float = 9.0) -> None:
        # Toasts belong to the moment they report; later screens start without them.
        end = time.monotonic() + seconds
        while win.toasts.toasts and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(name: str) -> None:
        app.processEvents()
        win.grab().save(str(out / f"{name}.png"))

    wait(lambda: win.conn_state() == "SIMULATED", 15, "simulation did not come up")
    pump(2.6)                                   # a few noise blocks for the phosphor trace
    shot("01-launch")

    doc = WORK / "Service Agreement.pdf"
    doc.write_bytes(sample_pdf(240_000))
    sign = win.page_sign
    sign.prepare(doc)
    wait(lambda: sign.prep is not None, 20, "file was not hashed")
    pump(0.8)
    shot("02-file-ready")

    sign.sign()
    wait(lambda: (mgr.job is not None and mgr.job.stage == "CONFIRM"), 15, "no USER button prompt")
    pump(2.2)
    shot("03-press-user")
    mgr.press_sim_button()
    # The simulated stages can finish between two polls, so a saved result counts as started too.
    wait(lambda: (mgr.job is not None and mgr.job.stage in ("SIGN", "VERIFY", "ECDSA", "PC_VERIFY", "SAVE"))
         or sign.out is not None, 15, "signing did not start")
    shot("04-signing")
    wait(lambda: sign.out is not None, 40, "signature was not saved")
    pump(1.2)
    shot("05-signed")

    if sign.out:
        settle_toasts()
        if not sign.out.get("embedded"):
            failures.append("the PDF was not signed into a signed copy")
        sign.verify_now()                       # the button on the result page
        vp = win.page_verify
        wait(lambda: vp.doc is not None, 20, "verify file not hashed")
        vp.run()
        wait(lambda: vp.report.isVisible(), 30, "no verify report")
        pump(1.2)
        shot("06-verify")
        if not vp.inside or vp.report.isHidden():
            failures.append("the signed copy did not verify by itself")
        # A key file and the signed copy dropped together land in their own slots.
        vp.reset()
        keys = WORK / "their-keys.json"
        core.export_public_keys(keys, [core.TrustedDevice("A" * 24, os.urandom(1952), None, "Someone")])
        copy = Path(sign.out["path"])
        vp.add_files([keys, copy])
        wait(lambda: vp.doc is not None, 20, "dropped file not hashed")
        if vp.keys != keys or vp.doc_path != copy or not vp.inside:
            failures.append("files dropped together were not sorted into their slots")

    win.tabs.set_index(2)
    win.page_device.run_selfcheck()
    wait(lambda: mgr.job is None and win.page_device.sc_summary.isVisible(), 40, "self-check did not finish")
    pump(0.6)
    shot("07-device")

    # A real board's INFO, as the first bench board reported it, must reach
    # every row; Simulation Mode shows these rows as simulated and so cannot
    # catch a field that never reaches the page.
    from manager import device_view  # noqa: E402
    real = core.parse_info(core.Reply(True, [
        "FW 1.0.0", "PROTO 1", "UID 3c0027001050534147303820", "HARDENING mpu=1 canary=1 stack=52480 rdp=0",
        "FW_HASH 8ca85f68eacff41b19bc01fc35959400", "VBIAS_MV 12089", "TEMP_C10 401", "TRNG OK",
        "H_ASSUMED 1.00 bits/symbol", "HEALTH symbols=3072 rct_cut=21 apt_cut=311 fails=0 fails_total=0",
        "SOURCE_B absent", "STM32_RNG ok",
        "JITTER live mean_ticks=2408 distinct=12 spread=11 mode=53 of=256",
        "ACCEL absent chip=none", "BOARD devebox noise=PA4 serial=PA9/PA10 hse_mhz=8", "KEY present",
        "ATECC ready rev=00006002 config_locked=1 data_locked=1", "OLED present"]), simulated=False)
    saved = win.snapshot
    win.snapshot = dict(saved, conn=dict(saved.get("conn", {}), simulated=False, state="IDLE"),
                        device=device_view(real), job=None)
    pd = win.page_device
    pd.refresh()
    want = {"e_temp": "40.1 °C", "e_ecc": theme.T("device.mcu_ok"), "e_mcu": theme.T("device.mcu_ok"),
            "e_jit": theme.T("device.mcu_ok"),
            "e_acc": theme.T("device.accel_absent"), "e_source_b": theme.T("device.source_b_absent"),
            "fw_board": theme.T("device.board_devebox"),
            "fw_rdp": theme.T("device.rdp_0")}
    for name, text in want.items():
        got = getattr(pd, name).text()
        if got != text:
            failures.append(f"a real board's {name} showed {got!r}, not {text!r}")
    # The noise header names the pin this board samples (PA4 on the DevEBox, not the Discovery's PA1).
    win._apply_snapshot()
    sub = win.scope_sub.text()
    if sub != theme.T("scope.subtitle", pin="PA4"):
        failures.append(f"a DevEBox's noise header showed {sub!r}")
    win.snapshot = saved
    win._apply_snapshot()
    pd.refresh()

    settle_toasts()
    win.tabs.set_index(3)
    pump(0.6)
    shot("08-history")

    mgr.stop()
    mgr.join(timeout=3)
    shutil.rmtree(WORK, ignore_errors=True)
    for f in failures:
        print("FAIL", f)
    print(f"screens written to {out}, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
