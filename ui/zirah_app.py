"""Zirah desktop app: entry point.

    zirah-app.bat                    (Windows, no console window)
    python ui/zirah_app.py [--sim] [--port COM5]

A local program: one native window built with PySide6 (Qt), nothing hosted
and no browser. It starts the device manager on its own thread, opens the
window and releases the serial port when the window closes. Only one copy
runs at a time.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "host"))

APP_TITLE = "Zirah"
MUTEX_NAME = "Local\\Zirah.Desktop.SingleInstance"


def message_box(text: str, title: str = APP_TITLE) -> None:
    """A visible error even with no console (pythonw / the .bat launcher)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000)
            return
        except Exception:
            pass
    print(f"{title}: {text}", file=sys.stderr)


def single_instance():
    """Return a handle to keep alive, or None if Zirah is already running."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle or err == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    import fcntl
    from zirah_core import data_dir
    f = open(data_dir() / "app.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def install_error_log(window_ref: list) -> None:
    """pythonw has no console, so an error inside the window would vanish.

    The traceback goes to last-error.txt in the per-user Zirah folder (code
    lines and the error text only, never keys or file contents), and the
    window says where to find it. The app keeps running.
    """
    import datetime
    import traceback
    from zirah_core import data_dir

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))[-8000:]
        path = data_dir() / "last-error.txt"
        try:
            stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            path.write_text(f"Zirah {stamp}\n{text}", encoding="utf-8")
        except OSError:
            pass
        if sys.stderr is not None:
            sys.stderr.write(text)
        win = window_ref[0] if window_ref else None
        if win is not None:
            try:
                from theme import T
                win.toast(T("app.error_title"), T("app.error_detail"), "err")
            except Exception:
                pass

    sys.excepthook = hook


def check_packages() -> bool:
    missing = []
    for mod in ("PySide6", "serial", "dilithium_py", "cryptography"):
        try:
            __import__(mod)
        except ImportError:
            missing.append({"PySide6": "PySide6", "serial": "pyserial"}.get(mod, mod))
    if missing:
        message_box("A required package is missing: " + ", ".join(missing) +
                    ".\n\nRun setup.bat in the Zirah folder, then start Zirah again.")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Zirah desktop app")
    ap.add_argument("--sim", action="store_true", help="start in Simulation Mode (no hardware)")
    ap.add_argument("--port", default="", help="serial port to use, e.g. COM5")
    args = ap.parse_args()

    if not check_packages():
        return 2
    lock = single_instance()
    if lock is None:
        message_box("Zirah is already running. Use the open window.")
        return 3

    if os.name == "nt":
        # Our own taskbar identity, so Windows shows the Zirah icon rather
        # than the Python one, and groups the window under Zirah.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Zirah.Desktop.1")
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication

    import theme
    from manager import Bus, DeviceManager
    from store import History, Settings
    from window import MainWindow, app_icon

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    theme.load_fonts()
    app.setStyleSheet(theme.QSS)
    app.setFont(theme.font("body"))

    settings = Settings()
    if args.port:
        settings.values["port"] = args.port
    history = History(settings)
    bus = Bus()
    manager = DeviceManager(bus, settings, history)
    manager.start()
    if args.sim:
        manager.req_simulation(True)

    window_ref: list = []
    install_error_log(window_ref)
    win = MainWindow(manager, settings, history)
    window_ref.append(win)
    win.show()
    rc = app.exec()
    manager.stop()
    manager.join(timeout=3)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        message_box(f"Zirah stopped because of an unexpected error:\n{e!r}")
        raise
