"""Settings and the minimal operation history of the desktop app.

Both live in the per-user Zirah folder (%LOCALAPPDATA%\\Zirah on Windows).
The history keeps only what a person needs to recognise an operation: time,
kind, the file's base name, the first 8 hex digits of its SHA-256 and the
result. It never stores file contents, full paths, keys or signatures, and
the Clear History control deletes the file.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "host"))

from zirah_core import clean_text, data_dir, now_utc  # noqa: E402

DEFAULTS = {
    "auto_connect": True,
    "reduce_motion": False,
    "keep_history": True,
    "port": "",
}
HISTORY_MAX = 200


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._lock = threading.Lock()
        self.values = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                        self.values[k] = v
        except (OSError, ValueError):
            pass

    def update(self, changes: dict) -> dict:
        with self._lock:
            for k, v in changes.items():
                if k not in DEFAULTS or type(v) is not type(DEFAULTS[k]):
                    raise ValueError(f"bad setting {k!r}")
                if k == "port":
                    v = clean_text(v, 64)
                self.values[k] = v
            try:
                _atomic_write(self.path, json.dumps(self.values, indent=2).encode("utf-8"))
            except OSError:
                pass    # a locked or read-only data folder: the setting still applies this session
            return dict(self.values)

    def get(self, key):
        return self.values.get(key, DEFAULTS.get(key))


class History:
    def __init__(self, settings: Settings, path: Path | None = None):
        self.settings = settings
        self.path = path or (data_dir() / "history.jsonl")
        self._lock = threading.Lock()

    def add(self, kind: str, name: str, digest8: str, result: str, simulated: bool) -> None:
        if not self.settings.get("keep_history"):
            return
        entry = {"t": now_utc(), "kind": kind, "name": clean_text(Path(name).name, 120),
                 "digest8": clean_text(digest8, 8).upper(), "result": clean_text(result, 40),
                 "simulated": bool(simulated)}
        with self._lock:
            items = self._read()
            items.append(entry)
            items = items[-HISTORY_MAX:]
            data = "".join(json.dumps(i) + "\n" for i in items).encode("utf-8")
            try:
                _atomic_write(self.path, data)
            except OSError:
                pass    # history is a convenience; never let it break signing

    def _read(self) -> list:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-HISTORY_MAX:]:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("kind") in ("sign", "verify"):
                out.append({"t": clean_text(e.get("t", ""), 30), "kind": e["kind"],
                            "name": clean_text(e.get("name", ""), 120),
                            "digest8": clean_text(e.get("digest8", ""), 8),
                            "result": clean_text(e.get("result", ""), 40),
                            "simulated": bool(e.get("simulated"))})
        return out

    def items(self) -> list:
        with self._lock:
            return list(reversed(self._read()))

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
