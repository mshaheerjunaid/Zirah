"""Device manager: the one thread that owns the connection to the Zirah.

Every device command runs here, in order, so the polling of live noise
samples can never interleave with a signature. The manager publishes its
state on the event bus; the window only queues requests and draws what the
bus reports.

Connection state machine (docs/DESIGN.md, "Desktop App"):

  DISCONNECTED -> SEARCHING -> CONNECTING -> HANDSHAKE -> SELF_CHECK ->
      READY | DEGRADED | NEEDS_TRUST | ERROR      (hardware)
      SIMULATED                                   (simulation transport)
  BUSY while an operation runs; any link failure returns to DISCONNECTED
  (and SEARCHING when auto-connect is on).

READY is only ever set from readiness() in zirah_core, which needs every
required check to pass on real hardware.
"""
from __future__ import annotations

import collections
import dataclasses
import itertools
import json
import platform
import queue
import re
import secrets
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "host"))

import zirah_core as core  # noqa: E402
from zirah_core import (Cancelled, Device, FileDigest, Link, LinkError, ProtocolError,  # noqa: E402
                        SerialTransport, TrustStore, ZirahError)

PEEK_PERIOD_S = 1.0
INFO_PERIOD_S = 10.0
# Listing serial ports is slow on some Windows PCs (Bluetooth COM ports in
# particular), so the search looks often only for the first seconds and then
# backs off; on Windows the window also asks for a scan the moment a USB
# device is plugged in.
SCAN_FAST_S = 1.5
SCAN_SLOW_S = 6.0
SCAN_FAST_FOR_S = 15.0


class Bus:
    """Fan-out of events from the device thread to the window (thread-safe)."""

    def __init__(self):
        self._callbacks: List[Callable[[str, object], None]] = []
        self._lock = threading.Lock()

    def subscribe_callback(self, fn: Callable[[str, object], None]) -> None:
        """Register a listener (the window). It is called on the device
        thread, so the window hands the event to Qt with a signal."""
        with self._lock:
            self._callbacks.append(fn)

    def publish(self, kind: str, payload) -> None:
        with self._lock:
            cbs = list(self._callbacks)
        for fn in cbs:
            try:
                fn(kind, payload)
            except Exception:
                pass  # a listener error must not take down the device thread


def err_dict(e: BaseException) -> dict:
    if isinstance(e, ZirahError):
        return e.as_dict()
    return {"code": "INTERNAL", "message": "Unexpected error", "detail": core.clean_text(repr(e), 200)}


_ids = itertools.count(1)


@dataclasses.dataclass
class Job:
    kind: str
    id: str = dataclasses.field(default_factory=lambda: f"j{next(_ids)}")
    status: str = "running"          # running, done, failed, cancelled
    stage: str = ""
    stages: List[str] = dataclasses.field(default_factory=list)
    done: List[str] = dataclasses.field(default_factory=list)
    failed: str = ""
    progress: float = 0.0
    cancellable: bool = False
    result: Optional[dict] = None
    error: Optional[dict] = None
    simulated: bool = False
    fields: Dict[str, str] = dataclasses.field(default_factory=dict)
    oled: str = ""                   # signing: the digits the OLED will show, from the manifest
    started: float = dataclasses.field(default_factory=time.time)
    elapsed: float = 0.0
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        # Built field by field: asdict() would try to deep-copy the cancel Event.
        d = {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != "cancel"}
        d = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
             for k, v in d.items()}
        d["elapsed"] = round(time.time() - self.started, 2) if self.status == "running" else self.elapsed
        return d


def device_view(info: Optional[core.DeviceInfo]) -> Optional[dict]:
    """What the pages see of the device: every field INFO reported. All of
    them are status, fingerprints and counters; INFO carries no secrets. An
    earlier hand-picked list left out the temperature, readout protection
    and the extra randomness sources, so those rows stayed empty on a real
    board while Simulation Mode, which shows them as simulated, looked
    right."""
    return None if info is None else info.to_dict()


class DeviceManager(threading.Thread):
    def __init__(self, bus: Bus, settings, history):
        super().__init__(name="zirah-device", daemon=True)
        self.bus = bus
        self.settings = settings
        self.history = history
        self._q: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._quit = threading.Event()
        self.state = "DISCONNECTED"
        self.port = ""
        self.simulated = False
        self.sim = None
        self.dev: Optional[Device] = None
        self.info: Optional[core.DeviceInfo] = None
        self.keys: Optional[core.DeviceKeys] = None
        self.trust: Optional[core.TrustState] = None
        self.checks: List[core.Check] = []
        self.error: Optional[dict] = None
        self.trust_hw = self._load_trust()
        self.trust_sim = TrustStore.memory()
        self.job: Optional[Job] = None
        self.ports: List[dict] = []
        self.last_peek: Optional[dict] = None
        self.log = collections.deque(maxlen=300)
        self.errors = collections.deque(maxlen=60)
        self.prepared: Dict[str, FileDigest] = {}
        self.outputs: List[Path] = []      # files this session created (Open Folder allow-list)
        self._next_peek = 0.0
        self._next_info = 0.0
        self._next_scan = 0.0
        self._search_since = time.monotonic()
        self._last_ports: List[dict] = []

    # ------------------------------------------------------------------
    # Thread plumbing
    # ------------------------------------------------------------------

    def _load_trust(self) -> TrustStore:
        try:
            return TrustStore()
        except ZirahError as e:
            self.errors.append({"t": core.now_utc(), **e.as_dict()})
            return TrustStore.memory()

    def call(self, fn: Callable[[], None]) -> None:
        self._q.put(fn)

    def stop(self) -> None:
        self._quit.set()
        self._q.put(lambda: None)

    def run(self) -> None:
        self._set_state("SEARCHING" if self.settings.get("auto_connect") else "DISCONNECTED")
        while not self._quit.is_set():
            try:
                fn = self._q.get(timeout=0.1)
            except queue.Empty:
                fn = None
            try:
                if fn is not None:
                    fn()
                self._tick()
            except Exception as e:  # never let the device thread die
                self._record_error(e)
                self._drop_link(e)
        self._close_link()

    def _tick(self) -> None:
        now = time.monotonic()
        if self.dev is None:
            retrying = self.state == "ERROR" and self.settings.get("auto_connect") and not self.simulated
            if (self.state == "SEARCHING" or retrying) and now >= self._next_scan:
                fast = self.state == "SEARCHING" and now - self._search_since < SCAN_FAST_FOR_S
                self._next_scan = now + (SCAN_FAST_S if fast else SCAN_SLOW_S)
                self._scan_and_connect(quiet=retrying)
            return
        if self.job is not None:
            return
        if now >= self._next_peek:
            self._next_peek = now + PEEK_PERIOD_S
            self._poll_peek()
        if now >= self._next_info:
            self._next_info = now + INFO_PERIOD_S
            self._refresh_status()

    def _log(self, direction: str, text: str) -> None:
        # Long hex payloads (keys, signatures, digests, samples) are elided:
        # the diagnostics log shows the conversation, not its data.
        text = re.sub(r"[0-9a-fA-F]{17,}", lambda m: f"<{len(m.group(0)) // 2} bytes>", text)
        self.log.append(f"{time.strftime('%H:%M:%S')} {direction} {core.clean_text(text, 160)}")

    def _record_error(self, e: BaseException) -> None:
        d = err_dict(e)
        d["t"] = core.now_utc()
        if d["code"] == "INTERNAL":
            d["detail"] = core.clean_text(traceback.format_exc(limit=3), 600)
        self.errors.append(d)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        dev = device_view(self.info)
        keys = None
        if self.keys is not None:
            keys = {"pq_fp": self.keys.mldsa_fp, "ec_fp": self.keys.ecc_fp,
                    "trust": self.trust.status if self.trust else "unknown",
                    "pinned_at": self.trust.entry.pinned_at if (self.trust and self.trust.entry) else ""}
        return {
            "conn": {"state": self.state, "port": self.port, "simulated": self.simulated,
                     "error": self.error},
            "device": dev,
            "keys": keys,
            "checks": [dataclasses.asdict(c) for c in self.checks],
            "job": self.job.public() if self.job else None,
            "settings": dict(self.settings.values),
            "ports": self.ports,
            "peek": self.last_peek,
        }

    def publish_state(self) -> None:
        snap = self.snapshot()
        snap.pop("peek", None)
        self.bus.publish("state", snap)

    def _set_state(self, state: str, error: Optional[dict] = None) -> None:
        if state == "SEARCHING" and self.state != "SEARCHING":
            self._search_since = time.monotonic()
        self.state = state
        self.error = error
        self.publish_state()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _scan_and_connect(self, quiet: bool = False) -> None:
        """quiet: a retry after an error, which only changes what the window
        shows if the outcome changes (no flicker, no repeated error toast)."""
        ports = core.list_ports()
        self.ports = [{"device": p.device, "kind": p.kind, "description": p.description} for p in ports]
        wanted = self.settings.get("port")
        cands = [p for p in ports if (p.device == wanted if wanted else p.kind == "zirah")]
        if not cands and not wanted:
            # No board on its own USB port: a single USB-TTL adapter (CP2102,
            # CH340, FTDI) wired to PA2/PA3 is the other way in, as on the CLI.
            ttl = [p for p in ports if p.kind == "usb-ttl"]
            if len(ttl) == 1:
                cands = ttl
        if len(cands) == 1:
            self._connect_serial(cands[0].device, quiet=quiet)
        elif quiet:
            self._set_state("SEARCHING")   # the board went away: back to looking for it
        elif self.ports != self._last_ports:
            self.publish_state()       # only when the list of ports actually changed
        self._last_ports = list(self.ports)

    def _connect_serial(self, port: str, quiet: bool = False) -> None:
        self.port = port
        if not quiet:
            self._set_state("CONNECTING")
        try:
            t = SerialTransport(port)
        except ZirahError as e:
            same = quiet and self.state == "ERROR" and (self.error or {}).get("code") == e.code
            if not same:
                self._record_error(e)
                self._set_state("ERROR", e.as_dict())
            self._retry_later()
            return
        self._attach(Device(Link(t, log=self._log)))

    def _attach(self, dev: Device) -> None:
        self.dev = dev
        self._set_state("HANDSHAKE")
        try:
            dev.handshake()
            self._set_state("SELF_CHECK")
            self._refresh_status(full=True)
        except ZirahError as e:
            self._record_error(e)
            self._close_link()
            self._set_state("ERROR", e.as_dict())
            self._retry_later()

    def _retry_later(self) -> None:
        # The error stays on screen; _tick retries quietly every SCAN_SLOW_S
        # while auto-connect is on.
        if self.settings.get("auto_connect") and not self.simulated:
            self._next_scan = time.monotonic() + SCAN_SLOW_S

    def _refresh_status(self, full: bool = False) -> None:
        if self.dev is None:
            return
        info = self.dev.refresh_info()
        self.info = info
        if full or self.keys is None or (info.pk_fp and info.pk_fp != self.keys.mldsa_fp) or \
                (info.ecc_fp or "") != (self.keys.ecc_fp if self.keys else ""):
            self.keys = self.dev.keys() if info.key == "present" else None
        store = self.trust_sim if self.simulated else self.trust_hw
        if self.simulated and self.keys is not None and store.find(info.uid) is None:
            store.pin(info.uid, self.keys, "Simulation")   # throwaway key, this session only
        self.trust = store.evaluate(info.uid, self.keys) if self.keys else None
        state, checks = core.readiness(info, self.trust)
        if state == "ERROR":
            failing = [c for c in checks if c.required and not c.ok]
            if [c.key for c in failing] == ["trusted"]:
                state = "NEEDS_TRUST"
        self.checks = checks
        if self.job is None:
            self._set_state(state)

    def _poll_peek(self) -> None:
        if self.dev is None:
            return
        blk = self.dev.peek()
        if blk is None:
            return
        if self.last_peek and self.last_peek.get("seq") == blk.seq:
            return
        self.last_peek = blk.to_dict()
        self.bus.publish("peek", self.last_peek)

    def _close_link(self) -> None:
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.info = None
        self.keys = None
        self.trust = None
        self.checks = []
        self.last_peek = None
        self.bus.publish("peek", None)

    def _drop_link(self, e: BaseException) -> None:
        if not isinstance(e, (LinkError, ProtocolError)):
            return
        was_sim = self.simulated
        self._close_link()
        if was_sim:
            self.simulated = False
            self.sim = None
        self.port = ""
        nxt = "SEARCHING" if (self.settings.get("auto_connect") and not was_sim) else "DISCONNECTED"
        self._set_state(nxt, err_dict(e))

    # Requests from the UI (run on this thread through call()).

    def req_connect(self, port: str = "") -> None:
        def go():
            if self.dev is not None:
                return
            self.simulated = False
            if port:
                self._connect_serial(port)
            else:
                self._next_scan = 0.0
                self._set_state("SEARCHING")
        self.call(go)

    def req_disconnect(self) -> None:
        def go():
            self._close_link()
            self.simulated = False
            self.sim = None
            self.port = ""
            self._set_state("DISCONNECTED")
        self.call(go)

    def req_simulation(self, on: bool) -> None:
        def go():
            self._close_link()
            self.sim = None
            self.port = ""
            if on:
                from zirah_sim import SimTransport
                self.simulated = True
                self.sim = SimTransport()
                self.port = "Simulation"
                self._attach(Device(Link(self.sim, log=self._log)))
            else:
                self.simulated = False
                self._set_state("SEARCHING" if self.settings.get("auto_connect") else "DISCONNECTED")
        self.call(go)

    def req_rescan(self) -> None:
        """A USB device came or went: look for the board now and search fast again."""
        def go():
            if self.state == "SEARCHING":
                self._search_since = time.monotonic()
                self._next_scan = 0.0
        self.call(go)

    def press_sim_button(self) -> None:
        """The simulated USER button. Called from the window thread directly,
        because the device thread is busy waiting for exactly this press."""
        if self.simulated and self.sim is not None:
            self.sim.press_button()

    # ------------------------------------------------------------------
    # Jobs that use the device
    # ------------------------------------------------------------------

    def _start_job(self, job: Job) -> Job:
        if self.job is not None and self.job.status == "running":
            raise ZirahError("BUSY", "Another operation is running")
        job.simulated = self.simulated
        self.job = job
        self._set_state("BUSY")
        return job

    def _job_update(self, job: Job) -> None:
        self.bus.publish("job", job.public())

    def _finish_job(self, job: Job, status: str, result=None, error=None) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.elapsed = round(time.time() - job.started, 2)
        self._job_update(job)
        self.job = None
        try:
            self._refresh_status()
        except ZirahError as e:
            self._record_error(e)
            self._drop_link(e)

    def _device_job(self, kind: str, stages: List[str], body: Callable[[Job], dict],
                    cancellable: bool = False) -> str:
        job = Job(kind, stages=list(stages), cancellable=cancellable)

        def go():
            if self.dev is None:
                job.status = "failed"
                job.error = {"code": "NO_DEVICE", "message": "No device connected", "detail": ""}
                self._job_update(job)
                return
            self._start_job(job)
            self._job_update(job)
            try:
                result = body(job)
                self._finish_job(job, "done", result=result)
            except Cancelled as e:
                self._finish_job(job, "cancelled", error=e.as_dict())
            except ZirahError as e:
                job.failed = job.stage
                self._record_error(e)
                self._finish_job(job, "failed", error=e.as_dict())
                self._drop_link(e)
            except Exception as e:
                job.failed = job.stage
                self._record_error(e)
                self._finish_job(job, "failed", error=err_dict(e))
        self.call(go)
        return job.id

    def _stage(self, job: Job, stage: str, fields: Optional[Dict[str, str]] = None) -> None:
        if job.stage and job.stage not in job.done:
            job.done.append(job.stage)
        job.stage = stage
        job.fields = {k: core.clean_text(v, 40) for k, v in (fields or {}).items()}
        if stage != "CONFIRM":
            job.cancellable = False
        self._job_update(job)

    def cancel(self, job_id: str) -> bool:
        j = self.job
        if j is not None and j.id == job_id and j.cancellable:
            j.cancel.set()
            return True
        return False

    def sign(self, prep_id: str, dest: Optional[Path]) -> str:
        dg = self.prepared.get(prep_id)
        if dg is None:
            raise ZirahError("NO_FILE", "Choose the file again")
        if dest is None and dg.path is None:
            raise ZirahError("NO_DEST", "Choose where to save the signature file")
        info = self.info
        stages = []
        if info is None or info.confirm:
            stages.append("CONFIRM")
        stages += ["ENTROPY", "REFRESH", "SIGN", "VERIFY"]
        if info is not None and info.atecc == "ready":
            stages.append("ECDSA")
        stages += ["PC_VERIFY", "SAVE"]

        def body(job: Job) -> dict:
            store = self.trust_sim if self.simulated else self.trust_hw

            def on_event(stage, fields):
                if stage == "MANIFEST":          # computed on this PC, not a device stage
                    job.oled = core.clean_text(fields.get("oled", ""), 9)
                    self._job_update(job)
                    return
                self._stage(job, stage, fields)

            if "CONFIRM" in stages:
                job.cancellable = True
            path, rec = core.sign_file(self.dev, store, None, dest, digest=dg,
                                           on_event=on_event, cancel=job.cancel)
            self._stage(job, "SAVE")
            job.done.append("SAVE")
            self.outputs.append(path)
            self.history.add("sign", dg.name, dg.sha256[:4].hex(), "Signed", self.simulated)
            return {"path": str(path), "name": path.name, "folder": str(path.parent),
                    "embedded": rec["manifest"]["container"] != "detached",
                    "container": rec["manifest"]["container"],
                    "pq_fp": rec["mldsa65"]["fingerprint"],
                    "ec_fp": rec["ecdsa_p256"]["fingerprint"] if rec["ecdsa_p256"] else "",
                    "hybrid": rec["ecdsa_p256"] is not None, "stats": rec["device_stats"],
                    "simulated": rec["simulated"], "sha256": dg.sha256.hex().upper()}
        return self._device_job("sign", stages, body, cancellable="CONFIRM" in stages)

    def self_check(self) -> str:
        steps = ["LINK", "FIRMWARE", "KAT", "HEALTH", "SIGNAL", "KEYS", "IDENTITY", "HARDENING", "RDP",
                 "ATECC"]

        def body(job: Job) -> dict:
            out = []

            def step(key, fn):
                self._stage(job, key)
                t0 = time.monotonic()
                try:
                    ok, required, data = fn()
                except ZirahError as e:
                    ok, required, data = False, True, {"error": e.code}
                out.append({"key": key, "ok": bool(ok), "required": required, "data": data,
                            "ms": int((time.monotonic() - t0) * 1000)})
                job.result = {"steps": out}
                self._job_update(job)

            d = self.dev

            def link():
                r = d.link.command("PING", timeout=3)
                return r.ok and "PONG" in r.lines, True, {}

            def firmware():
                i = d.refresh_info()
                return (i.protocol == core.PROTO_VERSION, True,
                        {"firmware": i.firmware, "protocol": i.protocol})

            def kat():
                return d.info.kat_ok, True, {"scope": d.info.kat_scope}

            def health():
                r = d.health()
                return r.ok, True, {"trng": (r.get("TRNG") or "").strip()}

            def signal():
                time.sleep(1.1)   # let the idle monitor take a fresh block
                b = d.peek()
                if b is None:
                    return False, True, {}
                good = b.valid and b.std_x10 >= 80 and b.clipped <= len(b.samples) // 20
                return good, True, {"std_x10": b.std_x10, "clipped": b.clipped, "seq": b.seq}

            def keys():
                k = d.keys()
                store = self.trust_sim if self.simulated else self.trust_hw
                t = store.evaluate(d.info.uid, k)
                return t.status == "trusted", True, {"trust": t.status, "pq_fp": k.mldsa_fp}

            def identity():
                k = d.keys()
                ok, _ = core.check_identity(d, k)
                return ok, True, {}

            def hardening():
                i = d.info
                return i.mpu and i.canary, False, {"mpu": i.mpu, "canary": i.canary,
                                                   "stack": i.stack_bytes}

            def rdp():
                return d.info.rdp >= 1, False, {"rdp": d.info.rdp}

            def atecc():
                return d.info.atecc == "ready", False, {"atecc": d.info.atecc}

            for key, fn in zip(steps, (link, firmware, kat, health, signal, keys, identity, hardening,
                                       rdp, atecc)):
                step(key, fn)
            job.done.append(job.stage)
            passed = all(s["ok"] for s in out if s["required"])
            return {"steps": out, "passed": passed}
        return self._device_job("selfcheck", steps, body)

    def bench(self, count: int = 20) -> str:
        """Times the masked signing path on the board (throwaway key) and saves
        the numbers as JSON in the Zirah data folder."""
        def body(job):
            self._stage(job, "BENCH", {"done": "0", "of": str(count)})

            def on_line(ln):
                if ln.startswith("EVT BENCH"):
                    self._stage(job, "BENCH", core.kv_fields(ln[len("EVT BENCH"):]))
            res = self.dev.bench(count, on_line=on_line)
            info = self.dev.refresh_info()
            job.done.append("BENCH")
            record = {"tool": "Zirah app", "count": count, "firmware": info.firmware,
                      "firmware_hash": info.fw_hash,
                      "die_temp_c": None if info.temp_c10 is None else info.temp_c10 / 10,
                      "second_source": info.source_b or "not reported", "result": res}
            folder = core.data_dir() / "benchmarks"
            path = folder / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
            try:
                folder.mkdir(parents=True, exist_ok=True)
                core.write_new_file(path, json.dumps(record, indent=2).encode("utf-8"))
            except (OSError, ZirahError):
                path = None   # the numbers are still shown; only the file is missing
            return {"bench": res, "path": str(path) if path else ""}
        return self._device_job("bench", ["BENCH"], body)

    def health(self) -> str:
        def body(job):
            self._stage(job, "HEALTH")
            r = self.dev.health()
            job.done.append("HEALTH")
            if not r.ok:
                raise core.DeviceError(r.err_code, r.err_text)
            return {"trng": (r.get("TRNG") or "").strip(), "fields": core.kv_fields(r.get("HEALTH") or "")}
        return self._device_job("health", ["HEALTH"], body)

    def keygen(self, force: bool) -> str:
        stages = (["CONFIRM"] if force else []) + ["ENTROPY", "REFRESH", "SIGN", "VERIFY"]

        def body(job):
            def on_line(ln):
                if ln.startswith("EVT "):
                    parts = ln[4:].split(" ", 1)
                    self._stage(job, parts[0], core.kv_fields(parts[1] if len(parts) > 1 else ""))
            r = self.dev.keygen(force, on_line=on_line, poll=self._cancel_poll(job))
            if not r.ok:
                raise core.DeviceError(r.err_code, r.err_text)
            if job.stage:
                job.done.append(job.stage)
            self.keys = self.dev.keys()
            return {"pq_fp": self.keys.mldsa_fp}
        return self._device_job("keygen", stages, body, cancellable=force)

    def set_confirm(self, on: bool) -> str:
        stages = [] if on else ["CONFIRM"]

        def body(job):
            def on_line(ln):
                if ln.startswith("EVT CONFIRM"):
                    self._stage(job, "CONFIRM", core.kv_fields(ln[len("EVT CONFIRM"):]))
            r = self.dev.set_confirm(on, on_line=on_line, poll=self._cancel_poll(job))
            if not r.ok:
                raise core.DeviceError(r.err_code, r.err_text)
            return {"confirm": on}
        return self._device_job("confirm", stages, body, cancellable=not on)

    def _cancel_poll(self, job: Job):
        sent = {"done": False}

        def poll():
            if job.cancel.is_set() and not sent["done"] and job.stage in ("", "CONFIRM"):
                self.dev.link.send_raw(b"\n")
                sent["done"] = True
        return poll

    def trust_device(self, typed_fp: str, replace: bool) -> dict:
        """Pin the connected device's keys. The person must type the PQ
        fingerprint they read on the OLED, so trust is never one click."""
        result: Dict[str, object] = {}
        done = threading.Event()

        def go():
            try:
                if self.dev is None or self.keys is None or self.info is None:
                    raise ZirahError("NO_DEVICE", "No device connected")
                if self.simulated:
                    raise ZirahError("NOT_IN_SIMULATION", "The simulated key is trusted for this session only")
                if re.sub(r"[\s-]", "", typed_fp.upper()) != self.keys.mldsa_fp:
                    raise ZirahError("FP_TYPED_WRONG", "The fingerprint you typed does not match the device")
                self.trust_hw.pin(self.info.uid, self.keys, replace=replace)
                self._refresh_status(full=True)
                result["ok"] = True
            except ZirahError as e:
                result["error"] = e.as_dict()
            finally:
                done.set()
        self.call(go)
        done.wait(10)
        return result or {"error": {"code": "TIMEOUT", "message": "The device thread is busy", "detail": ""}}

    # ------------------------------------------------------------------
    # Work that needs no device (runs on its own worker thread)
    # ------------------------------------------------------------------

    def _worker(self, kind: str, stages: List[str], body: Callable[[Job], dict]) -> str:
        job = Job(kind, stages=stages)

        def run():
            self._job_update(job)
            try:
                job.result = body(job)
                job.status = "done"
            except Cancelled as e:
                job.status, job.error = "cancelled", e.as_dict()
            except ZirahError as e:
                job.status, job.error = "failed", e.as_dict()
                self._record_error(e)
            except Exception as e:
                job.status, job.error = "failed", err_dict(e)
                self._record_error(e)
            job.elapsed = round(time.time() - job.started, 2)
            self._job_update(job)
        threading.Thread(target=run, name=f"zirah-{kind}", daemon=True).start()
        return job.id

    def prepare_file(self, path: Path) -> str:
        doc = core.safe_file_path(path)

        def body(job: Job) -> dict:
            job.stage = "HASH"
            last = [0.0]

            def progress(done, total):
                now = time.monotonic()
                if now - last[0] > 0.1:
                    last[0] = now
                    job.progress = done / total if total else 1.0
                    self._job_update(job)
            dg = core.hash_file(doc, progress=progress)
            pid = "p" + secrets.token_hex(6)
            self.prepared[pid] = dg
            while len(self.prepared) > 64:          # digests only, but never an unbounded list
                self.prepared.pop(next(iter(self.prepared)))
            embedded = core.embedded_signature(doc)
            container, dest = "detached", None
            if not embedded:
                try:
                    container = core.signing_container(doc)
                    dest = core.default_signature_path(doc, self.simulated, container)
                except ZirahError:
                    dest = None
            return {"id": pid, "name": dg.name, "size": dg.size, "sha256": dg.sha256.hex().upper(),
                    "short": dg.short, "folder": str(doc.parent), "container": container,
                    "embedded": embedded or "", "dest": str(dest) if dest else ""}
        return self._worker("prepare", ["HASH"], body)

    def verify(self, doc_ref: str, sig_path: Optional[Path], keys_path: Optional[Path]) -> str:
        """sig_path None: the signature inside a signed copy."""
        def body(job: Job) -> dict:
            job.stage = "VERIFY"
            dg = self.prepared.get(doc_ref)
            if dg is None or dg.path is None:
                raise ZirahError("NO_FILE", "Choose the file again")
            # Only hardware keys count as trusted. The simulation key is never
            # used here, so a simulated file cannot pass as a device signature
            # even if someone edits its "simulated" field.
            if keys_path is not None:
                trusted = core.load_keys_file(keys_path)
            else:
                trusted = list(self.trust_hw.devices)
            rep, dg = core.verify_file(dg.path, sig_path, trusted)   # what is on disk now
            self.history.add("verify", dg.name, dg.sha256[:4].hex(), rep.verdict,
                             bool(rep.sig and rep.sig.simulated))
            out = rep.to_dict()
            out["file_sha256"] = dg.sha256.hex().upper()
            out["signature_file"] = sig_path.name if sig_path else ""
            return out
        return self._worker("verify", ["VERIFY"], body)

    # ------------------------------------------------------------------
    # Diagnostics (non-sensitive)
    # ------------------------------------------------------------------

    def diagnostics(self, include_uid: bool = False) -> dict:
        def ver(mod):
            try:
                m = __import__(mod)
                return getattr(m, "__version__", "installed")
            except Exception:
                return "missing"
        info = None
        if self.info is not None:
            info = self.info.to_dict()
            if not include_uid and info.get("uid"):
                info["uid"] = "…" + info["uid"][-4:]
        return {
            "generated_utc": core.now_utc(),
            "note": "Non-sensitive diagnostics: no keys, signatures, file contents or file hashes.",
            "app": {"host_version": core.HOST_VERSION, "protocol": core.PROTO_VERSION,
                    "python": platform.python_version(), "os": platform.platform(),
                    "pyserial": ver("serial"), "dilithium_py": ver("dilithium_py"),
                    "cryptography": ver("cryptography")},
            "connection": {"state": self.state, "port": self.port, "simulated": self.simulated},
            "device": info,
            "checks": [dataclasses.asdict(c) for c in self.checks],
            "ports": self.ports,
            "trusted_devices": len(self.trust_hw.devices),
            "recent_errors": list(self.errors)[-20:],
            "protocol_log": list(self.log)[-80:],
        }
