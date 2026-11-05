"""Zirah host core, shared by the command line tool (host/zirah.py) and the
desktop app (ui/zirah_app.py).

Everything that touches the device, a file, a signature file or the
trusted-key store goes through this module, so the two front ends enforce
exactly the same rules:

  * Device replies are read with a bounded line reader and parsed strictly.
    Every hex field must have the exact length of its type; anything else is
    a protocol error, never a best-effort guess.
  * The FIPS 204 context is pinned in code ("zirah-doc-v1" for files,
    "zirah-attest-v1" for identity proofs). A signature file cannot choose
    its own context.
  * Signing refuses to run unless the device's public keys are pinned in the
    trusted-key store. A key that changes is reported, never replaced
    silently.
  * A new signature is verified on the PC (ML-DSA-65 with dilithium-py,
    ECDSA P-256 with cryptography) and the file is hashed a second time
    before the signature file is written, so a signature that does not match
    the bytes on disk is never saved.
  * Signature files are published atomically and never overwrite an
    existing file.
  * Simulated devices are a separate transport. Their results carry
    simulated = true, use a different file suffix and can never verify as a
    hardware signature.

Nothing here stores file contents or secret keys. The device never sends
a secret key, and the host never asks for one.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import BinaryIO, Callable, Dict, List, Optional, Tuple

import zirah_embed as embed

HOST_VERSION = "1.0.0"
PROTO_VERSION = 1
FW_MAJOR = 1

CTX_DOC = b"zirah-doc-v1"
CTX_PROVE = b"zirah-attest-v1"
PROVE_PREFIX = b"ZIRAH-PROVE-v1"

SIG_FORMAT = "zirah-signature-v2"      # what Zirah writes: the device signs a manifest
SIG_FORMAT_V1 = "zirah-signature-v1"   # older files: the device signed the file's SHA-256
MANIFEST_LABEL = b"ZIRAH-MANIFEST-v1\n"
MANIFEST_KEYS = ("container", "content_sha256", "file_created_utc", "file_modified_utc", "name",
                 "signed_at_utc", "size")
CONTAINERS = ("detached",) + embed.KINDS
KEYS_FORMAT = "zirah-public-keys-v1"
SIG_SUFFIX = ".zirah.json"
SIM_SIG_SUFFIX = ".zirah-sim.json"

USB_VID, USB_PID = 0x0483, 0x5740
USB_TTL_VIDS = (0x10C4, 0x1A86, 0x0403)  # CP210x, CH340, FTDI

MLDSA_PK_BYTES = 1952
MLDSA_SIG_BYTES = 3309
ECC_PK_BYTES = 64
ECC_SIG_BYTES = 64
UID_BYTES = 12
MONITOR_SAMPLES = 512

MAX_LINE = 8192             # longest valid line is "MLDSA " + 6618 hex digits
MAX_COMMAND = 200
MAX_SIGNATURE_FILE = 64 * 1024
MAX_KEYS_FILE = 256 * 1024
HASH_CHUNK = 1 << 20

SIGN_TIMEOUT_S = 75.0       # 30 s button window + entropy + signing + checks


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ZirahError(Exception):
    """An error with a stable code (for the UI) and a readable message."""

    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ProtocolError(ZirahError):
    pass


class DeviceError(ZirahError):
    """The device answered ERR <CODE> <text>."""


class LinkError(ZirahError):
    pass


class Cancelled(ZirahError):
    def __init__(self, message: str = "Cancelled"):
        super().__init__("CANCELLED", message)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def clean_text(s: str, limit: int = 300) -> str:
    """Printable ASCII only, bounded. Used for anything a device or a file
    supplied before it is shown, so it cannot inject terminal escapes."""
    return _PRINTABLE.sub("?", str(s))[:limit]


def parse_hex(s: str, nbytes: int, what: str) -> bytes:
    if not isinstance(s, str) or len(s) != 2 * nbytes or not re.fullmatch(r"[0-9a-fA-F]*", s):
        raise ProtocolError("BAD_FIELD", f"{what} must be {nbytes} bytes of hex",
                            f"got {len(s) if isinstance(s, str) else type(s).__name__}")
    return bytes.fromhex(s)


def fingerprint(data: bytes) -> str:
    """Short key fingerprint shown on the OLED: SHAKE256, 4 bytes, upper hex."""
    return hashlib.shake_256(data).hexdigest(4).upper()


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_text(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_times(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """(created, last modified) as this PC's file system reports them, UTC.
    Creation time exists on Windows and macOS; Linux reports none."""
    try:
        st = path.stat()
    except OSError:
        return None, None
    created = getattr(st, "st_birthtime", None)
    if created is None and os.name == "nt":
        created = st.st_ctime            # Windows before Python 3.12: ctime is the creation time
    return utc_text(created), utc_text(st.st_mtime)


def data_dir() -> Path:
    """Per-user folder for the trusted-key store, history and settings."""
    env = os.environ.get("ZIRAH_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Zirah"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "zirah"
    base.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(base, 0o700)
        except OSError:
            pass
    return base


def trusted_keys_path() -> Path:
    return data_dir() / "trusted-keys.json"


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class Transport:
    """Byte pipe to a Zirah. `simulated` is decided by the transport class,
    never by anything the other end says."""

    simulated = False
    name = "?"

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout: float) -> bytes:
        """Return available bytes, waiting up to `timeout` seconds."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class SerialTransport(Transport):
    def __init__(self, port: str, baud: int = 115200):
        try:
            import serial  # pyserial
        except ImportError as e:  # pragma: no cover - depends on install
            raise LinkError("NO_PYSERIAL", "pyserial is not installed",
                            "Run setup.bat in the Zirah folder") from e
        kwargs = dict(baudrate=baud, timeout=0.05, write_timeout=2.0)
        if os.name != "nt":
            kwargs["exclusive"] = True  # Windows ports are exclusive already
        try:
            self._ser = serial.Serial(port, **kwargs)
        except Exception as e:
            raise LinkError("PORT_BUSY", f"Cannot open {clean_text(port, 40)}",
                            "Close other programs that use the port, then try again. "
                            + clean_text(str(e), 160)) from e
        self.name = port

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
            self._ser.flush()
        except Exception as e:
            raise LinkError("LINK_LOST", "The device stopped responding", clean_text(str(e))) from e

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + max(timeout, 0.0)
        try:
            while True:
                n = self._ser.in_waiting
                if n:
                    return self._ser.read(min(n, 65536))
                chunk = self._ser.read(1)
                if chunk:
                    return chunk
                if time.monotonic() >= end:
                    return b""
        except Exception as e:
            raise LinkError("LINK_LOST", "The device was disconnected", clean_text(str(e))) from e

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


@dataclasses.dataclass
class PortInfo:
    device: str
    description: str
    kind: str          # "zirah", "usb-ttl", "st-link", "other"
    serial: str = ""


def list_ports() -> List[PortInfo]:
    try:
        import serial.tools.list_ports
    except ImportError:
        return []
    out = []
    for p in serial.tools.list_ports.comports():
        if p.vid == USB_VID and p.pid == USB_PID:
            kind = "zirah"
        elif p.vid in USB_TTL_VIDS:
            kind = "usb-ttl"
        elif p.vid == USB_VID:
            kind = "st-link"
        else:
            kind = "other"
        out.append(PortInfo(p.device, clean_text(p.description or "", 80), kind,
                            clean_text(p.serial_number or "", 40)))
    return out


def pick_port(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ZIRAH_PORT")
    if env:
        return env
    ports = list_ports()
    zirah = [p for p in ports if p.kind == "zirah"]
    if len(zirah) == 1:
        return zirah[0].device
    if len(zirah) > 1:
        raise LinkError("MANY_DEVICES", "More than one Zirah is connected",
                        "Choose one with --port " + ", ".join(p.device for p in zirah))
    ttl = [p for p in ports if p.kind == "usb-ttl"]
    if len(ttl) == 1:
        return ttl[0].device
    found = ", ".join(f"{p.device} ({p.description})" for p in ports) or "none"
    raise LinkError("NO_DEVICE", "No Zirah found",
                    f"Ports found: {found}. Plug the micro-USB cable into the board's USB OTG "
                    "connector, or name the port with --port.")


# ---------------------------------------------------------------------------
# Line protocol
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Reply:
    ok: bool
    lines: List[str]
    err_code: str = ""
    err_text: str = ""

    def get(self, key: str) -> Optional[str]:
        prefix = key + " "
        for ln in self.lines:
            if ln.startswith(prefix):
                return ln[len(prefix):]
            if ln == key:
                return ""
        return None

    def raise_for_error(self) -> "Reply":
        if not self.ok:
            raise DeviceError(self.err_code or "DEVICE", self.err_text or "The device refused the command")
        return self


def kv_fields(text: str) -> Dict[str, str]:
    """'a=1 b=x' -> {'a': '1', 'b': 'x'}"""
    out = {}
    for item in (text or "").split():
        k, sep, v = item.partition("=")
        if sep and re.fullmatch(r"[a-z0-9_]{1,24}", k):
            out[k] = v
    return out


class Link:
    """Bounded line reader plus command/response framing over a Transport."""

    def __init__(self, transport: Transport, log: Optional[Callable[[str, str], None]] = None):
        self.t = transport
        self._buf = bytearray()
        self._log = log
        self._lock = threading.Lock()
        self._dirty = False   # a command was abandoned mid-reply: resync first

    @property
    def simulated(self) -> bool:
        return self.t.simulated

    def close(self) -> None:
        self.t.close()

    def _emit_log(self, direction: str, text: str) -> None:
        if self._log:
            self._log(direction, text)

    def send_raw(self, data: bytes) -> None:
        self.t.write(data)

    def send(self, line: str) -> None:
        if len(line) > MAX_COMMAND or _PRINTABLE.search(line):
            raise ProtocolError("BAD_COMMAND", "Command contains characters the device rejects")
        self._emit_log(">", line)
        self.t.write(line.encode("ascii") + b"\n")

    def drain(self, quiet_s: float = 0.15, max_s: float = 1.5) -> None:
        """Discard pending input until the line has been quiet for quiet_s."""
        end = time.monotonic() + max_s
        last = time.monotonic()
        while time.monotonic() < end:
            data = self.t.read(0.05)
            if data:
                last = time.monotonic()
            elif time.monotonic() - last >= quiet_s:
                break
        self._buf.clear()

    def read_exact(self, n: int, timeout: float, idle: Optional[float] = None) -> bytes:
        """Binary payload of known length (RAW capture), after its header line.
        `idle` ends the wait early when no byte at all arrives for that long,
        so a transfer that lost bytes fails in seconds instead of waiting for
        bytes that will never come."""
        out = bytearray(self._buf[:n])
        del self._buf[:len(out)]
        end = time.monotonic() + timeout
        last = time.monotonic()
        while len(out) < n:
            now = time.monotonic()
            if now > end or (idle is not None and now - last > idle):
                raise LinkError("TIMEOUT", f"Binary stream stopped at {len(out)} of {n} bytes")
            chunk = self.t.read(0.25)
            if chunk:
                last = time.monotonic()
            take = chunk[: n - len(out)]
            out += take
            if len(chunk) > len(take):
                self._buf += chunk[len(take):]
        return bytes(out)

    def readline(self, timeout: float) -> str:
        end = time.monotonic() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                raw = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                raw = raw.rstrip(b"\r")
                if len(raw) > MAX_LINE:
                    raise ProtocolError("LINE_TOO_LONG", "The device sent an oversized line")
                try:
                    text = raw.decode("ascii")
                except UnicodeDecodeError:
                    raise ProtocolError("BAD_BYTES", "The device sent non-text bytes")
                if _PRINTABLE.search(text):
                    raise ProtocolError("BAD_BYTES", "The device sent control characters")
                self._emit_log("<", text if len(text) < 200 else text[:200] + "...")
                return text
            if len(self._buf) > MAX_LINE + 2:
                self._buf.clear()
                raise ProtocolError("LINE_TOO_LONG", "The device sent an oversized line")
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise LinkError("TIMEOUT", "The device did not answer in time")
            self._buf += self.t.read(min(remaining, 0.25))

    def command(self, line: str, timeout: float = 10.0,
                on_line: Optional[Callable[[str], None]] = None,
                poll: Optional[Callable[[], None]] = None) -> Reply:
        """Send one command and collect lines until OK or ERR.

        on_line gets every data and EVT line as it arrives. poll is called at
        least every 0.25 s while waiting (used for cancellation)."""
        with self._lock:
            if self._dirty:
                self.drain()
                self._dirty = False
            try:
                return self._command(line, timeout, on_line, poll)
            except BaseException:
                self._dirty = True
                raise

    def _command(self, line, timeout, on_line, poll) -> Reply:
        self.send(line)
        lines: List[str] = []
        end = time.monotonic() + timeout
        while True:
            if poll:
                poll()
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise LinkError("TIMEOUT", f"No answer to {line.split(' ')[0]} in {timeout:.0f} s")
            try:
                ln = self.readline(min(remaining, 0.25))
            except LinkError as e:
                if e.code == "TIMEOUT" and time.monotonic() < end:
                    continue
                raise
            if not ln:
                continue
            if ln == "OK":
                return Reply(True, lines)
            if ln.startswith("ERR"):
                rest = ln[3:].strip()
                code, _, text = rest.partition(" ")
                if not re.fullmatch(r"[A-Z_]{2,20}", code):
                    code, text = "DEVICE", rest
                return Reply(False, lines, code, clean_text(text))
            if ln.startswith("FATAL"):
                return Reply(False, lines, "FATAL", clean_text(ln))
            if len(lines) > 400:
                raise ProtocolError("TOO_MANY_LINES", "The device sent an unexpected amount of data")
            lines.append(ln)
            if on_line:
                on_line(ln)


# ---------------------------------------------------------------------------
# Device status
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DeviceInfo:
    firmware: str = ""
    protocol: int = 0
    uid: str = ""                 # 24 hex, upper
    kat_ok: bool = False
    kat_scope: str = ""
    mpu: bool = False
    canary: bool = False
    stack_bytes: int = 0
    rdp: int = -1                 # flash readout protection 0, 1 or 2; -1 not reported
    fw_hash: str = ""             # 32 hex, upper: SHAKE256 (16 bytes) of the flashed image
    temp_c10: Optional[int] = None  # die temperature in tenths of a degree C
    source_b: str = ""            # second noise source: absent, present ("" not reported)
    b_std_x10: int = 0
    corr_x1000: Optional[int] = None  # A-B correlation of the last startup test
    stm32_rng: str = ""           # STM32 generator: ok, fault, off ("" not reported)
    jitter: str = ""              # LSI clock jitter: live, absent, fault ("" not reported)
    accel: str = ""               # the board's accelerometer: live, absent, fault ("" not reported)
    accel_chip: str = ""          # lis3dsh, lis302dl, none
    vdd_mv: int = 0
    vbias_mv: int = 0
    trng: str = ""
    h_assumed: str = ""
    health: Dict[str, str] = dataclasses.field(default_factory=dict)
    key: str = "none"             # present, stored, none
    pk_fp: str = ""
    mask_refreshes: int = 0
    atecc: str = "absent"         # absent, ready, not-provisioned
    atecc_detail: Dict[str, str] = dataclasses.field(default_factory=dict)
    ecc_fp: str = ""
    oled: bool = False
    usb: str = ""
    board: str = "discovery"      # discovery or devebox (older firmware does not say: discovery)
    hse_mhz: Optional[int] = None  # crystal the clocks run from; 0 = internal oscillator
    noise_pin: str = ""           # the ADC pin source A is wired to (older firmware does not say)
    confirm: bool = True
    signing: str = ""             # enabled, locked
    prove_left: int = 0
    sigs: int = 0
    simulated: bool = False

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _int(s: Optional[str], default: int = 0) -> int:
    try:
        return int((s or "").split()[0])
    except (ValueError, IndexError):
        return default


def parse_info(r: Reply, simulated: bool) -> DeviceInfo:
    i = DeviceInfo(simulated=simulated)
    i.firmware = clean_text(r.get("FW") or "", 24)
    i.protocol = _int(r.get("PROTO"))
    uid = (r.get("UID") or "").strip()
    i.uid = uid.upper() if re.fullmatch(r"[0-9a-fA-F]{24}", uid) else ""
    kat = r.get("KAT") or ""
    i.kat_ok = kat.split(" ")[0] == "ok"
    i.kat_scope = kv_fields(kat).get("scope", "")
    hard = kv_fields(r.get("HARDENING") or "")
    i.mpu = hard.get("mpu") == "1"
    i.canary = hard.get("canary") == "1"
    i.stack_bytes = _int(hard.get("stack"))
    i.rdp = _int(hard.get("rdp"), -1) if hard.get("rdp") in ("0", "1", "2") else -1
    fh = (r.get("FW_HASH") or "").strip()
    i.fw_hash = fh.upper() if re.fullmatch(r"[0-9a-fA-F]{32}", fh) else ""
    t = (r.get("TEMP_C10") or "").strip()
    i.temp_c10 = int(t) if re.fullmatch(r"-?\d{1,4}", t) else None
    sb = r.get("SOURCE_B") or ""
    i.source_b = sb.split(" ")[0] if sb.split(" ")[0] in ("absent", "present") else ""
    if i.source_b == "present":
        bf = kv_fields(sb)
        i.b_std_x10 = _int(bf.get("std_x10"))
        i.corr_x1000 = _int(bf.get("corr_x1000")) if re.fullmatch(r"-?\d{1,4}", bf.get("corr_x1000", "")) \
            else None
    rng = (r.get("STM32_RNG") or "").strip()
    i.stm32_rng = rng if rng in ("ok", "fault", "off") else ""
    jit = (r.get("JITTER") or "").split()
    i.jitter = jit[0] if jit and jit[0] in ("live", "absent", "fault") else ""
    acc = (r.get("ACCEL") or "").split()
    i.accel = acc[0] if acc and acc[0] in ("live", "absent", "fault") else ""
    chip = kv_fields(r.get("ACCEL") or "").get("chip", "")
    i.accel_chip = chip if chip in ("lis3dsh", "lis302dl", "none") else ""
    i.vdd_mv = _int(r.get("VDD_MV"))
    i.vbias_mv = _int(r.get("VBIAS_MV"))
    i.trng = clean_text(r.get("TRNG") or "", 20)
    i.h_assumed = clean_text(r.get("H_ASSUMED") or "", 40)
    i.health = kv_fields(r.get("HEALTH") or "")
    i.key = (r.get("KEY") or "none").strip()
    fp = (r.get("PK_FP") or "").strip()
    i.pk_fp = fp.upper() if re.fullmatch(r"[0-9a-fA-F]{8}", fp) else ""
    i.mask_refreshes = _int(r.get("MASK_REFRESHES"))
    at = r.get("ATECC") or "absent"
    i.atecc = at.split(" ")[0]
    i.atecc_detail = kv_fields(at)
    efp = (r.get("ECC_FP") or "").strip()
    i.ecc_fp = efp.upper() if re.fullmatch(r"[0-9a-fA-F]{8}", efp) else ""
    i.oled = (r.get("OLED") or "") == "present"
    bl = (r.get("BOARD") or "").split()
    if bl and bl[0] in BOARDS:
        i.board = bl[0]
        hse = kv_fields(r.get("BOARD") or "").get("hse_mhz", "")
        i.hse_mhz = int(hse) if re.fullmatch(r"\d{1,2}", hse) else None
        pin = kv_fields(r.get("BOARD") or "").get("noise", "")
        i.noise_pin = pin if re.fullmatch(r"P[A-I]\d{1,2}", pin) else ""
    i.usb = clean_text(r.get("USB") or "", 20)
    i.confirm = (r.get("CONFIRM") or "on") != "off"
    i.signing = (r.get("SIGNING") or "").strip()
    i.prove_left = _int(r.get("PROVE_LEFT"))
    i.sigs = _int(r.get("SIGS"))
    return i


@dataclasses.dataclass
class Check:
    key: str
    ok: bool
    required: bool
    title: str
    detail: str = ""


FIRMWARE_DIR = Path(__file__).resolve().parents[1] / "firmware"
# The board each build is for, and the file flash.bat (or flash-devebox.bat) writes.
BOARDS = {"discovery": "zirah.bin", "devebox": "zirah-devebox.bin"}
FIRMWARE_BIN = FIRMWARE_DIR / BOARDS["discovery"]


def button_name(board: str) -> str:
    """The name printed next to the confirmation button: K1 on the DevEBox,
    the blue USER button on the Discovery."""
    return "K1" if board == "devebox" else "USER"


def firmware_bin(board: str = "discovery") -> Path:
    return FIRMWARE_DIR / BOARDS.get(board, BOARDS["discovery"])
_fw_cache: Dict[Tuple[str, int, int], str] = {}


def local_firmware_hash(path: Optional[Path] = None, board: str = "discovery") -> str:
    """SHAKE256 (16 bytes, upper-case hex) of the firmware file that flashing
    would write for `board` (or of `path`), the same value the firmware
    reports as FW_HASH for itself; "" when there is no such file."""
    if path is None:
        path = firmware_bin(board)
    try:
        st = path.stat()
    except OSError:
        return ""
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _fw_cache:
        try:
            data = _read_bounded(path, 2 * 1024 * 1024)
        except (OSError, ZirahError):
            return ""
        _fw_cache.clear()
        _fw_cache[key] = hashlib.shake_256(data).digest(16).hex().upper()
    return _fw_cache[key]


def readiness(info: DeviceInfo, trust: Optional["TrustState"]) -> Tuple[str, List[Check]]:
    """Return ("READY" | "DEGRADED" | "SIMULATED" | "ERROR", checks). Never
    READY unless every required check passed on real hardware."""
    fw_major = _int(info.firmware.split(".")[0] if info.firmware else "")
    checks = [
        Check("protocol", info.protocol == PROTO_VERSION and fw_major == FW_MAJOR, True,
              "Firmware Compatible",
              f"Firmware {info.firmware or 'Unknown'}, Protocol {info.protocol}; this app needs "
              f"Protocol {PROTO_VERSION}"),
        Check("kat", info.kat_ok, True, "Boot Self-Test Passed",
              "Known-answer tests for SHAKE256 and ML-DSA-65 ran at power-up"),
        Check("trng", info.trng == "OK", True, "Entropy Source Healthy",
              f"State {info.trng or 'Unknown'}"),
        Check("key", info.key == "present", True, "Signing Key Loaded",
              {"present": "Key Masked in CCM", "stored": "Key Stored but Not Loaded",
               "none": "No Key Yet, Generate One"}.get(info.key, info.key)),
        Check("signing", info.signing == "enabled", True, "Signing Unlocked",
              "A failed self-check locks signing until power-cycle"),
        Check("trusted", bool(trust and trust.status == "trusted"), True, "Keys Trusted on This PC",
              trust.reason if trust else "Not Checked"),
        Check("oled", info.oled, False, "OLED Present",
              "Without the OLED the digest cannot be compared before pressing the button"),
        Check("confirm", info.confirm, False, "Button Confirmation On",
              "When off, the device signs without a button press"),
        Check("atecc", info.atecc == "ready", False, "ATECC608 Ready",
              "Without it signatures are post-quantum only"),
        Check("hardening", info.mpu and info.canary, False, "Firmware Hardening Active",
              f"MPU {'On' if info.mpu else 'Off'}, Stack Canary {'On' if info.canary else 'Off'}"),
        Check("rdp", info.rdp >= 1, False, "Readout Protection On",
              {0: "Level 0, an ST-LINK Can Read the Flash Including the ML-DSA Key",
               1: "Level 1, the Debugger Cannot Read the Flash",
               2: "Level 2, Permanent, the Debug Port Is Disabled"}.get(
                  info.rdp, "Not Reported by This Firmware")),
    ]
    local = "" if info.simulated else local_firmware_hash(board=info.board)
    if local:
        same = info.fw_hash == local
        name = BOARDS.get(info.board, BOARDS["discovery"])
        tool = "flash-devebox.bat" if info.board == "devebox" else "flash.bat"
        checks.append(Check("fwmatch", same, False, "Firmware Matches This PC's Copy",
                            f"The Board Runs the {name} in This Zirah Folder" if same else
                            (f"An Older Build That Does Not Report Its Hash, Run {tool}" if not info.fw_hash
                             else f"A Different Build from This Folder's {name}, Run {tool}")))
    if info.stm32_rng == "fault":
        checks.append(Check("mcurng", False, False, "STM32 Generator Healthy",
                            "Its Self-Test Failed, so Zirah Mixes in Only the Other Sources"))
    if info.jitter == "fault":
        checks.append(Check("jitter", False, False, "Clock Jitter Healthy",
                            "Its Check Failed, so Zirah Mixes in Only the Other Sources"))
    if info.accel == "fault":
        checks.append(Check("accel", False, False, "Board Accelerometer Healthy",
                            "Its Noise Check Failed, so Zirah Mixes in Only the Other Sources"))
    if info.simulated:
        for c in checks:
            if c.key in ("kat", "trng", "key", "hardening", "rdp"):
                c.detail = "Simulated, No Hardware Involved"
        checks.insert(0, Check("hardware", False, False, "Real Zirah Hardware",
                               "Simulation Mode: Results Are Marked Simulated"))
    if any(c.required and not c.ok for c in checks):
        return "ERROR", checks
    if info.simulated:
        return "SIMULATED", checks
    if any(not c.ok for c in checks):
        return "DEGRADED", checks
    return "READY", checks


@dataclasses.dataclass
class PeekBlock:
    seq: int
    valid: bool
    rate: int
    mean: int
    std_x10: int
    min: int
    max: int
    clipped: int
    trng: str
    samples: List[int]
    simulated: bool = False
    b: bool = False               # a second noise source is present
    b_mean: int = 0
    b_std_x10: int = 0
    b_clipped: int = 0
    corr_x1000: Optional[int] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_peek(r: Reply, simulated: bool) -> Optional[PeekBlock]:
    head = r.get("PEEK")
    if head is None:
        raise ProtocolError("BAD_PEEK", "PEEK reply without a header")
    f = kv_fields(head)
    seq = _int(f.get("seq"))
    if seq == 0:
        return None
    hexs = r.get("SAMPLES") or ""
    if len(hexs) != 3 * MONITOR_SAMPLES or not re.fullmatch(r"[0-9a-f]*", hexs):
        raise ProtocolError("BAD_PEEK", "PEEK samples have the wrong length")
    samples = [int(hexs[i:i + 3], 16) for i in range(0, len(hexs), 3)]
    b = f.get("b") == "1"
    corr = f.get("corr_x1000", "")
    return PeekBlock(seq=seq, valid=f.get("valid") == "1", rate=_int(f.get("rate")),
                     mean=_int(f.get("mean")), std_x10=_int(f.get("std_x10")),
                     min=_int(f.get("min")), max=_int(f.get("max")),
                     clipped=_int(f.get("clipped")), trng=clean_text(f.get("trng", ""), 20),
                     samples=samples, simulated=simulated, b=b,
                     b_mean=_int(f.get("b_mean")) if b else 0,
                     b_std_x10=_int(f.get("b_std_x10")) if b else 0,
                     b_clipped=_int(f.get("b_clipped")) if b else 0,
                     corr_x1000=int(corr) if b and re.fullmatch(r"-?\d{1,4}", corr) else None)


BENCH_STAGES = ("trng32_us", "refresh_us", "sign_us", "verify_us", "attempts")


def parse_bench(r: Reply) -> dict:
    """The BENCH reply as {"build": {...}, "stages": {name: {min, median, max,
    mean, n}}, "values": {name: {...}}}; every number an int."""
    out: dict = {"build": {}, "stages": {}, "values": {}}
    for ln in r.lines:
        if not ln.startswith("BENCH "):
            continue
        name, _, rest = ln[6:].partition(" ")
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            raise ProtocolError("BAD_BENCH", "The benchmark reply is malformed")
        fields = kv_fields(rest)
        if name == "build":
            out["build"] = {k: clean_text(v, 16) for k, v in fields.items()}
            continue
        nums = {k: _int(v) for k, v in fields.items()}
        if name in BENCH_STAGES:
            out["stages"][name] = nums
        else:
            out["values"][name] = nums
    if not all(k in out["stages"] for k in BENCH_STAGES):
        raise ProtocolError("BAD_BENCH", "The benchmark reply is incomplete")
    return out


# ---------------------------------------------------------------------------
# Signing result
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DeviceKeys:
    mldsa_pk: bytes
    ecc_pk: Optional[bytes]

    @property
    def mldsa_fp(self) -> str:
        return fingerprint(self.mldsa_pk)

    @property
    def ecc_fp(self) -> str:
        return fingerprint(self.ecc_pk) if self.ecc_pk else ""


@dataclasses.dataclass
class SignResult:
    mldsa_sig: bytes
    ecdsa_sig: Optional[bytes]
    ecdsa_note: str
    stats: Dict[str, int]


STAGES = ("CONFIRM", "ENTROPY", "REFRESH", "SIGN", "VERIFY", "ECDSA")


class Device:
    """High-level operations on one connected Zirah."""

    def __init__(self, link: Link):
        self.link = link
        self.info: Optional[DeviceInfo] = None

    @property
    def simulated(self) -> bool:
        return self.link.simulated

    @classmethod
    def open(cls, port: Optional[str] = None, log=None) -> "Device":
        dev = cls(Link(SerialTransport(pick_port(port)), log=log))
        try:
            dev.handshake()
        except Exception:
            dev.close()
            raise
        return dev

    def close(self) -> None:
        self.link.close()

    def handshake(self) -> DeviceInfo:
        # A stray partial line in the device's buffer is flushed by a bare
        # newline (the device answers it with ERR BAD_LINE, which we discard).
        self.link.send_raw(b"\n")
        self.link.drain()
        last: Optional[Exception] = None
        for _ in range(3):
            try:
                r = self.link.command("PING", timeout=2.0)
                if r.ok and "PONG" in r.lines:
                    break
                last = ProtocolError("NO_PONG", "The device did not answer PING with PONG")
            except (LinkError, ProtocolError) as e:
                last = e
                self.link.drain()
        else:
            raise LinkError("NOT_ZIRAH", "This port does not answer like a Zirah",
                            "Is the Zirah firmware flashed, and is this the board's USB data port? "
                            + (getattr(last, "message", "") or ""))
        return self.refresh_info()

    def refresh_info(self) -> DeviceInfo:
        r = self.link.command("INFO", timeout=5.0).raise_for_error()
        self.info = parse_info(r, self.simulated)
        return self.info

    def peek(self) -> Optional[PeekBlock]:
        r = self.link.command("PEEK", timeout=3.0).raise_for_error()
        return parse_peek(r, self.simulated)

    def keys(self) -> DeviceKeys:
        r = self.link.command("PK", timeout=5.0).raise_for_error()
        pk = parse_hex(r.get("PK") or "", MLDSA_PK_BYTES, "ML-DSA-65 public key")
        dev_fp = (r.get("PK_FP") or "").upper()
        if dev_fp and dev_fp != fingerprint(pk):
            raise ProtocolError("FP_MISMATCH", "The device's key fingerprint does not match its key")
        e = self.link.command("ECCPK", timeout=5.0)
        ecc = parse_hex(e.get("ECCPK") or "", ECC_PK_BYTES, "ECDSA P-256 public key") if e.ok else None
        if ecc is not None:
            _ecc_public_key(ecc)  # must be a valid curve point
        return DeviceKeys(pk, ecc)

    def health(self) -> Reply:
        return self.link.command("HEALTH", timeout=10.0)

    def analyze(self, n: int = 4096, source: str = "a") -> Reply:
        if not 64 <= n <= 4096:
            raise ValueError("n must be 64..4096")
        if source not in ("a", "b"):
            raise ValueError("source must be a or b")
        return self.link.command(f"ANALYZE {n}" + (" B" if source == "b" else ""), timeout=20.0)

    def bench(self, count: int = 20, on_line=None) -> dict:
        """Times the masked signing path on the board with a throwaway key."""
        if not 1 <= count <= 100:
            raise ValueError("count must be 1..100")
        r = self.link.command(f"BENCH {count}", timeout=60.0 + 1.5 * count, on_line=on_line)
        r.raise_for_error()
        return parse_bench(r)

    def keygen(self, force: bool, on_line=None, poll=None) -> Reply:
        return self.link.command("KEYGEN FORCE" if force else "KEYGEN", timeout=SIGN_TIMEOUT_S,
                                 on_line=on_line, poll=poll)

    def set_confirm(self, on: bool, on_line=None, poll=None) -> Reply:
        return self.link.command("CONFIRM ON" if on else "CONFIRM OFF", timeout=40.0,
                                 on_line=on_line, poll=poll)

    def sign_digest(self, digest: bytes, on_event: Optional[Callable[[str, Dict[str, str]], None]] = None,
                    cancel: Optional[threading.Event] = None) -> SignResult:
        """Ask the device to sign a SHA-256 digest.

        on_event(stage, fields) is called for each EVT line as the device
        reaches that stage. `cancel` is honoured only while the device waits
        for the USER button; once signing has started it runs to the end."""
        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes")
        state = {"stage": "", "cancel_sent": False}
        expect = digest[:4].hex().upper()

        def on_line(ln: str) -> None:
            if not ln.startswith("EVT "):
                return
            parts = ln[4:].split(" ", 1)
            stage = parts[0]
            if stage not in STAGES:
                raise ProtocolError("BAD_EVT", "Unknown stage from the device")
            fields = kv_fields(parts[1] if len(parts) > 1 else "")
            if stage == "CONFIRM" and fields.get("doc", "").upper() != expect:
                # The device would show a different digest than ours: stop.
                self.link.send_raw(b"\n")
                state["cancel_sent"] = True
                raise ProtocolError("DIGEST_MISMATCH",
                                    "The device received a different digest; signing was cancelled")
            state["stage"] = stage
            if on_event:
                on_event(stage, fields)

        def poll() -> None:
            if cancel is not None and cancel.is_set() and not state["cancel_sent"]:
                if state["stage"] in ("", "CONFIRM"):
                    self.link.send_raw(b"\n")
                    state["cancel_sent"] = True

        r = self.link.command("SIGN " + digest.hex(), timeout=SIGN_TIMEOUT_S, on_line=on_line, poll=poll)
        if not r.ok:
            if r.err_code == "CANCELLED":
                raise Cancelled("Signing was cancelled before the USER button was pressed")
            raise DeviceError(r.err_code, r.err_text or "The device refused to sign")
        sig = parse_hex(r.get("MLDSA") or "", MLDSA_SIG_BYTES, "ML-DSA-65 signature")
        ec_line = r.get("ECDSA") or ""
        ecdsa, note = None, ""
        if ec_line.startswith("NONE"):
            note = clean_text(ec_line[4:].strip(), 120)
        elif ec_line:
            ecdsa = parse_hex(ec_line, ECC_SIG_BYTES, "ECDSA P-256 signature")
        stats = {k: _int(v) for k, v in kv_fields(r.get("STATS") or "").items()}
        return SignResult(sig, ecdsa, note, stats)

    def prove(self, nonce: bytes) -> bytes:
        if len(nonce) != 32:
            raise ValueError("nonce must be 32 bytes")
        r = self.link.command("PROVE " + nonce.hex(), timeout=30.0).raise_for_error()
        return parse_hex(r.get("PROOF") or "", MLDSA_SIG_BYTES, "identity proof")


# ---------------------------------------------------------------------------
# Cryptographic checks on the PC
# ---------------------------------------------------------------------------

def _mldsa():
    try:
        from dilithium_py.ml_dsa import ML_DSA_65
    except ImportError as e:  # pragma: no cover - depends on install
        raise ZirahError("NO_DILITHIUM", "dilithium-py is not installed", "Run setup.bat") from e
    return ML_DSA_65


def verify_mldsa(pk: bytes, msg: bytes, sig: bytes, ctx: bytes) -> bool:
    if len(pk) != MLDSA_PK_BYTES or len(sig) != MLDSA_SIG_BYTES:
        return False
    try:
        return bool(_mldsa().verify(pk, msg, sig, ctx=ctx))
    except Exception:
        return False


def _ecc_public_key(pub64: bytes):
    from cryptography.hazmat.primitives.asymmetric import ec
    if len(pub64) != ECC_PK_BYTES:
        raise ProtocolError("BAD_ECC_KEY", "ECDSA public key has the wrong length")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + pub64)
    except ValueError as e:
        raise ProtocolError("BAD_ECC_KEY", "ECDSA public key is not a P-256 point") from e


def verify_ecdsa(pub64: bytes, digest: bytes, sig64: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    if len(sig64) != ECC_SIG_BYTES or len(digest) != 32:
        return False
    try:
        key = _ecc_public_key(pub64)
        r = int.from_bytes(sig64[:32], "big")
        s = int.from_bytes(sig64[32:], "big")
        key.verify(utils.encode_dss_signature(r, s), digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except (InvalidSignature, ValueError, ProtocolError):
        return False


def prove_message(uid_hex: str, nonce: bytes) -> bytes:
    return PROVE_PREFIX + bytes.fromhex(uid_hex) + nonce


def check_identity(dev: Device, keys: DeviceKeys) -> Tuple[bool, str]:
    """Challenge the device with a fresh nonce and check the proof against
    its public key, under the identity context (never the file-signing context)."""
    if not dev.info or not dev.info.uid:
        return False, "Device ID Unknown"
    nonce = secrets.token_bytes(32)
    proof = dev.prove(nonce)
    ok = verify_mldsa(keys.mldsa_pk, prove_message(dev.info.uid, nonce), proof, CTX_PROVE)
    return ok, "Proof Verified on This PC" if ok else "Proof Did Not Verify"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FileDigest:
    path: Optional[Path]
    name: str
    size: int
    sha256: bytes
    mtime_ns: int = 0

    @property
    def short(self) -> str:
        """The 8 hex digits the OLED shows, as 'XXXX XXXX'."""
        h = self.sha256[:4].hex().upper()
        return f"{h[:4]} {h[4:]}"


def safe_file_path(p: str | os.PathLike) -> Path:
    path = Path(p)
    if not path.is_absolute():
        raise ZirahError("BAD_PATH", "Use a full file path")
    try:
        path = path.resolve(strict=True)
        st = path.stat()
    except OSError as e:
        raise ZirahError("NO_FILE", "The file does not exist or cannot be read", clean_text(str(e))) from e
    if not stat.S_ISREG(st.st_mode):
        raise ZirahError("NOT_A_FILE", "Only regular files can be signed")
    return path


def hash_file(path: Path, progress: Optional[Callable[[int, int], None]] = None,
              cancel: Optional[threading.Event] = None) -> FileDigest:
    h = hashlib.sha256()
    done = 0
    try:
        st = path.stat()
        with open(path, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("Hashing was cancelled")
                chunk = f.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, st.st_size)
        st2 = path.stat()
    except OSError as e:     # locked by another program, a cloud placeholder that failed, a lost drive
        raise ZirahError("READ_FAILED", "The file could not be read", clean_text(str(e))) from e
    if st2.st_size != done or st2.st_mtime_ns != st.st_mtime_ns:
        raise ZirahError("FILE_CHANGED", "The file changed while it was being read",
                         "Close any program that is writing to it and try again")
    return FileDigest(path, path.name, done, h.digest(), st.st_mtime_ns)


def default_signature_path(doc: Path, simulated: bool = False, container: str = "detached") -> Path:
    """A free name next to the file: CV.pdf.zirah.json for a separate
    signature file, CV.signed.pdf for a signed copy."""
    if container == "detached":
        suffix = SIM_SIG_SUFFIX if simulated else SIG_SUFFIX
        names = [doc.name + suffix] + [f"{doc.name} ({i}){suffix}" for i in range(2, 1000)]
    else:
        first = embed.signed_name(doc, simulated)
        stem, ext = first[:len(first) - len(doc.suffix)], doc.suffix
        names = [first] + [f"{stem} ({i}){ext}" for i in range(2, 1000)]
    for n in names:
        cand = doc.with_name(n)
        if not cand.exists():
            return cand
    raise ZirahError("NO_NAME", "Could not find a free name for the signature file")


def check_destination(dest: Path, simulated: bool, container: str = "detached",
                      doc: Optional[Path] = None) -> Path:
    dest = Path(dest)
    if not dest.is_absolute():
        raise ZirahError("BAD_PATH", "Choose a full path for the signature file")
    if container == "detached":
        suffix = SIM_SIG_SUFFIX if simulated else SIG_SUFFIX
        if not dest.name.endswith(suffix):
            raise ZirahError("BAD_SUFFIX", f"Signature files must end in {suffix}")
    else:
        if doc is not None and doc.suffix and dest.suffix.lower() != doc.suffix.lower():
            raise ZirahError("BAD_SUFFIX", f"The signed copy must keep the ending {doc.suffix or '(none)'}")
        if simulated and ".signed-sim" not in dest.name:
            raise ZirahError("BAD_SUFFIX", "Simulated signed copies must have .signed-sim in their name")
        if not simulated and ".signed-sim" in dest.name:
            raise ZirahError("BAD_SUFFIX", ".signed-sim is reserved for Simulation Mode")
    if not dest.parent.is_dir():
        raise ZirahError("NO_FOLDER", "The destination folder does not exist")
    if dest.exists():
        raise ZirahError("EXISTS", "A file with that name already exists; Zirah never overwrites files")
    return dest


def write_new_file(dest: Path, data: bytes) -> None:
    """Atomically create `dest` with `data`. Fails if `dest` exists."""
    publish_new_file(dest, lambda f: f.write(data))


def publish_new_file(dest: Path, fill: Callable[[BinaryIO], None],
                     check: Optional[Callable[[Path], None]] = None) -> None:
    """Atomically create `dest` from what `fill` writes, after `check` (if
    given) has accepted the finished temporary file. Never overwrites."""
    dest = Path(dest)
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    except OSError as e:
        raise ZirahError("WRITE_FAILED", f"{dest.name} could not be saved", clean_text(str(e))) from e
    try:
        with os.fdopen(fd, "wb") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        if check is not None:
            check(tmp)
        if os.name == "nt":
            os.rename(tmp, dest)            # raises FileExistsError if dest exists
        else:
            try:
                os.link(tmp, dest)          # raises FileExistsError if dest exists
                os.unlink(tmp)
            except FileExistsError:
                raise
            except OSError:
                # File systems without hard links (FAT, some shares): exclusive create.
                with open(tmp, "rb") as src, open(dest, "xb") as f:
                    while True:
                        chunk = src.read(HASH_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                os.unlink(tmp)
    except FileExistsError as e:
        raise ZirahError("EXISTS", "A file with that name already exists; Zirah never overwrites files") from e
    except OSError as e:     # a read-only or protected folder, a full disk, a sync client holding a lock
        raise ZirahError("WRITE_FAILED", f"{dest.name} could not be saved", clean_text(str(e))) from e
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Trusted keys
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TrustedDevice:
    uid: str
    mldsa_pk: bytes
    ecc_pk: Optional[bytes]
    label: str = ""
    pinned_at: str = ""

    def to_json(self) -> dict:
        return {
            "device_uid": self.uid,
            "label": self.label,
            "pinned_at_utc": self.pinned_at,
            "mldsa65_public_key": self.mldsa_pk.hex(),
            "mldsa65_fingerprint": fingerprint(self.mldsa_pk),
            "ecdsa_p256_public_key": self.ecc_pk.hex() if self.ecc_pk else None,
            "ecdsa_p256_fingerprint": fingerprint(self.ecc_pk) if self.ecc_pk else None,
        }


@dataclasses.dataclass
class TrustState:
    status: str     # trusted, unpinned, changed
    reason: str
    entry: Optional[TrustedDevice] = None


def _read_bounded(path: Path, limit: int) -> bytes:
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ZirahError("TOO_LARGE", f"{path.name} is larger than expected")
    return data


class TrustStore:
    """Pinned device public keys (no secrets). One JSON file per user."""

    def __init__(self, path: Optional[Path] = None, persist: bool = True):
        self.persist = persist
        self.path = Path(path) if path else (trusted_keys_path() if persist else None)
        self.devices: List[TrustedDevice] = []
        if self.path is not None and self.path.exists():
            self.devices = load_keys_file(self.path)

    @classmethod
    def memory(cls) -> "TrustStore":
        """A store that is never written to disk (simulation mode)."""
        return cls(path=None, persist=False)

    def find(self, uid: str) -> Optional[TrustedDevice]:
        for d in self.devices:
            if d.uid == uid:
                return d
        return None

    def evaluate(self, uid: str, keys: DeviceKeys) -> TrustState:
        e = self.find(uid)
        if e is None:
            return TrustState("unpinned", "This Device Is Not Trusted Yet; Compare the Fingerprints "
                                          "With the OLED, Then Trust It")
        if e.mldsa_pk != keys.mldsa_pk:
            return TrustState("changed", "The ML-DSA Key Changed Since It Was Trusted", e)
        if (e.ecc_pk or None) != (keys.ecc_pk or None):
            return TrustState("changed", "The ECDSA Key Changed Since It Was Trusted", e)
        return TrustState("trusted", "Keys Match the Trusted Copy", e)

    def pin(self, uid: str, keys: DeviceKeys, label: str = "", replace: bool = False) -> TrustedDevice:
        """Trust a device's keys. Replacing a different key needs replace=True,
        which the front ends only pass after an explicit user confirmation."""
        if not re.fullmatch(r"[0-9A-F]{24}", uid):
            raise ZirahError("BAD_UID", "Device ID is missing or malformed")
        cur = self.find(uid)
        if cur and (cur.mldsa_pk != keys.mldsa_pk or (cur.ecc_pk or None) != (keys.ecc_pk or None)) \
                and not replace:
            raise ZirahError("KEY_CHANGED", "This device's keys changed; confirm the replacement first")
        entry = TrustedDevice(uid, keys.mldsa_pk, keys.ecc_pk, clean_text(label, 60), now_utc())
        self.devices = [d for d in self.devices if d.uid != uid] + [entry]
        self.save()
        return entry

    def forget(self, uid: str) -> None:
        self.devices = [d for d in self.devices if d.uid != uid]
        self.save()

    def save(self) -> None:
        if not self.persist or self.path is None:
            return
        doc = {"format": KEYS_FORMAT, "devices": [d.to_json() for d in self.devices]}
        data = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # the store itself is replaced atomically


def load_keys_file(path: Path) -> List[TrustedDevice]:
    try:
        doc = json.loads(_read_bounded(Path(path), MAX_KEYS_FILE).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ZirahError("BAD_KEYS_FILE", "The trusted-key file is not valid JSON") from e
    if not isinstance(doc, dict) or doc.get("format") != KEYS_FORMAT or not isinstance(doc.get("devices"), list):
        raise ZirahError("BAD_KEYS_FILE", "Not a Zirah public-key file")
    out = []
    for d in doc["devices"][:64]:
        if not isinstance(d, dict):
            raise ZirahError("BAD_KEYS_FILE", "Malformed device entry")
        uid = str(d.get("device_uid", "")).upper()
        if not re.fullmatch(r"[0-9A-F]{24}", uid):
            raise ZirahError("BAD_KEYS_FILE", "Malformed device ID in key file")
        pk = parse_hex(d.get("mldsa65_public_key", ""), MLDSA_PK_BYTES, "ML-DSA-65 public key")
        ecc_hex = d.get("ecdsa_p256_public_key")
        ecc = parse_hex(ecc_hex, ECC_PK_BYTES, "ECDSA public key") if ecc_hex else None
        out.append(TrustedDevice(uid, pk, ecc, clean_text(d.get("label", ""), 60),
                                 clean_text(d.get("pinned_at_utc", ""), 30)))
    return out


def export_public_keys(dest: Path, entries: List[TrustedDevice]) -> None:
    doc = {"format": KEYS_FORMAT, "devices": [e.to_json() for e in entries]}
    write_new_file(Path(dest), (json.dumps(doc, indent=2) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# Signature files
# ---------------------------------------------------------------------------

def make_manifest(doc: FileDigest, container: str, signed_at: Optional[str] = None,
                  times: Optional[Tuple[Optional[str], Optional[str]]] = None) -> dict:
    """What the device signs, through manifest_digest(): the file's
    SHA-256, name, size and the format it is carried in, the signing time
    and the file's creation and modification times, all as this PC reports
    them."""
    if container not in CONTAINERS:
        raise ValueError("unknown container")
    created, modified = times if times is not None else (file_times(doc.path) if doc.path else (None, None))
    return {"container": container, "content_sha256": doc.sha256.hex(), "file_created_utc": created,
            "file_modified_utc": modified, "name": doc.name, "signed_at_utc": signed_at or now_utc(),
            "size": doc.size}


def manifest_digest(m: dict) -> bytes:
    """SHA-256 of a fixed label and the manifest as sorted, compact ASCII
    JSON. The device signs these 32 bytes, and the OLED shows the first four."""
    text = json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(MANIFEST_LABEL + text.encode("ascii")).digest()


def short_hex(d: bytes) -> str:
    """The 8 hex digits the OLED shows, as 'XXXX XXXX'."""
    h = d[:4].hex().upper()
    return f"{h[:4]} {h[4:]}"


def build_signature(manifest: dict, info: DeviceInfo, keys: DeviceKeys,
                    res: SignResult, simulated: bool) -> dict:
    return {
        "format": SIG_FORMAT,
        "covers": "ML-DSA-65 and ECDSA P-256 sign SHA-256 of 'ZIRAH-MANIFEST-v1' and the manifest below: "
                  "the file's SHA-256, name, size and container, the signing time, and its "
                  "creation and modification times, as the signing PC reported them.",
        "manifest": manifest,
        "context": CTX_DOC.decode(),
        "signer": {"device_uid": info.uid, "firmware": info.firmware, "protocol": info.protocol},
        "mldsa65": {"public_key": keys.mldsa_pk.hex(), "fingerprint": keys.mldsa_fp,
                    "signature": res.mldsa_sig.hex()},
        "ecdsa_p256": ({"public_key": keys.ecc_pk.hex(), "fingerprint": keys.ecc_fp,
                        "signature": res.ecdsa_sig.hex()} if (res.ecdsa_sig and keys.ecc_pk) else None),
        "device_stats": {k: v for k, v in res.stats.items() if isinstance(v, int)},
        "simulated": bool(simulated),
    }


@dataclasses.dataclass
class SignatureFile:
    version: int                    # 1: the device signed the file's SHA-256; 2: a manifest
    doc_name: str
    doc_size: int
    content_sha256: bytes           # SHA-256 of the file itself
    signed_digest: bytes            # the 32 bytes both signatures cover
    container: str                  # detached, pdf, zip, png or jpeg
    signed_at: str                  # signed in version 2, a claim in version 1
    file_created: Optional[str]     # the signing PC's file times, signed (version 2)
    file_modified: Optional[str]
    uid: str
    firmware: str
    mldsa_pk: bytes
    mldsa_sig: bytes
    ecc_pk: Optional[bytes]
    ecc_sig: Optional[bytes]
    simulated: bool
    context: str

    @property
    def sha256(self) -> bytes:
        return self.content_sha256


def _opt_text(v, limit: int) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("expected text")
    return clean_text(v, limit)


def parse_record(doc) -> SignatureFile:
    """A signature record, from a .zirah.json file or from inside a signed copy."""
    if not isinstance(doc, dict) or doc.get("format") not in (SIG_FORMAT, SIG_FORMAT_V1):
        raise ZirahError("BAD_SIGNATURE_FILE", "Not a Zirah signature file")
    try:
        m = doc["mldsa65"]
        e = doc.get("ecdsa_p256")
        signer = doc.get("signer") or {}
        common = dict(
            uid=clean_text(signer.get("device_uid", ""), 24).upper(),
            firmware=clean_text(signer.get("firmware", ""), 24),
            mldsa_pk=parse_hex(m["public_key"], MLDSA_PK_BYTES, "ML-DSA-65 public key"),
            mldsa_sig=parse_hex(m["signature"], MLDSA_SIG_BYTES, "ML-DSA-65 signature"),
            ecc_pk=parse_hex(e["public_key"], ECC_PK_BYTES, "ECDSA public key") if e else None,
            ecc_sig=parse_hex(e["signature"], ECC_SIG_BYTES, "ECDSA signature") if e else None,
            simulated=doc.get("simulated") is not False,   # anything but an explicit false
            context=clean_text(doc.get("context", ""), 40))
        if doc["format"] == SIG_FORMAT_V1:
            d = doc["document"]
            sha = parse_hex(d["sha256"], 32, "file SHA-256")
            return SignatureFile(1, clean_text(d.get("name", ""), 255), int(d.get("size", -1)), sha, sha,
                                 "detached", clean_text(doc.get("signed_at_utc", ""), 30), None, None, **common)
        mf = doc["manifest"]
        if not isinstance(mf, dict) or sorted(mf) != sorted(MANIFEST_KEYS):
            raise TypeError("manifest fields")
        if mf["container"] not in CONTAINERS or not isinstance(mf["name"], str) \
                or not isinstance(mf["signed_at_utc"], str) \
                or not isinstance(mf["size"], int) or isinstance(mf["size"], bool) or mf["size"] < 0:
            raise TypeError("manifest values")
        sha = parse_hex(mf["content_sha256"], 32, "file SHA-256")
        return SignatureFile(2, clean_text(mf["name"], 255), mf["size"], sha, manifest_digest(mf),
                             mf["container"], clean_text(mf["signed_at_utc"], 30),
                             _opt_text(mf["file_created_utc"], 30), _opt_text(mf["file_modified_utc"], 30),
                             **common)
    except (KeyError, TypeError, AttributeError, ValueError) as ex:
        if isinstance(ex, ZirahError):
            raise
        raise ZirahError("BAD_SIGNATURE_FILE", "The signature file is missing required fields") from ex


def load_signature(path: Path) -> SignatureFile:
    try:
        doc = json.loads(_read_bounded(Path(path), MAX_SIGNATURE_FILE).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ZirahError("BAD_SIGNATURE_FILE", "The signature file is not valid JSON") from e
    return parse_record(doc)


def find_signature_for(doc: Path) -> Optional[Path]:
    for suffix in (SIG_SUFFIX, SIM_SIG_SUFFIX):
        cand = doc.with_name(doc.name + suffix)
        if cand.is_file():
            return cand
    return None


def embedded_signature(path: Path) -> Optional[str]:
    """The format of the Zirah signature inside a signed copy, or None."""
    try:
        e = embed.find(path)
    except OSError:
        return None
    return e.kind if e else None


def classify_file(path: Path) -> str:
    """What a dropped file is: "signature" (a Zirah signature file), "keys"
    (an exported public-key file) or "file" (anything else, including a
    JSON file that is neither, and a signed copy). Only small JSON files are
    opened."""
    try:
        if path.suffix.lower() == ".json" and path.stat().st_size <= MAX_KEYS_FILE:
            fmt = json.loads(_read_bounded(path, MAX_KEYS_FILE).decode("utf-8")).get("format")
            if fmt in (SIG_FORMAT, SIG_FORMAT_V1):
                return "signature"
            if fmt == KEYS_FORMAT:
                return "keys"
    except (OSError, ValueError, AttributeError, ZirahError):
        pass
    return "file"


@dataclasses.dataclass
class VerifyReport:
    verdict: str                    # VALID, VALID_PQ_ONLY, UNTRUSTED, INVALID, SIMULATED
    checks: List[Check]
    sig: Optional[SignatureFile]
    title: str
    detail: str
    signer: str = ""                # label or device ID of the trusted entry that matched
    found_in: str = "detached"      # where the signature was: detached, or the signed copy's format
    times: Dict[str, Optional[str]] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"verdict": self.verdict, "title": self.title, "detail": self.detail, "signer": self.signer,
             "found_in": self.found_in, "times": dict(self.times),
             "checks": [dataclasses.asdict(c) for c in self.checks]}
        if self.sig:
            d["signature"] = {"file": self.sig.doc_name, "size": self.sig.doc_size,
                              "sha256": self.sig.content_sha256.hex().upper(),
                              "signed_digest": self.sig.signed_digest.hex().upper(),
                              "version": self.sig.version, "container": self.sig.container,
                              "signed_at": self.sig.signed_at, "device_uid": self.sig.uid,
                              "firmware": self.sig.firmware,
                              "mldsa_fp": fingerprint(self.sig.mldsa_pk),
                              "ecc_fp": fingerprint(self.sig.ecc_pk) if self.sig.ecc_pk else "",
                              "simulated": self.sig.simulated}
        return d


def verify_record(sf: SignatureFile, content: bytes, size: int, found_in: str,
                  trusted: Optional[List[TrustedDevice]], clean: bool = True,
                  path: Optional[Path] = None) -> VerifyReport:
    """The checks and the verdict for one signature record against the SHA-256
    and size of the file it is meant for."""
    checks: List[Check] = []
    same_content = sf.content_sha256 == content
    same_place = sf.version == 1 or (sf.doc_size == size and sf.container == found_in)
    hash_ok = same_content and same_place
    checks.append(Check("hash", hash_ok, True, "File Matches the Signature",
                        "SHA-256 of the file equals the signed digest" if hash_ok
                        else "The file is not the one that was signed, or it changed" if not same_content
                        else "The signature was made for this content in another form"))
    if found_in != "detached":
        checks.append(Check("intact", clean, True, "Nothing Added After Signing",
                            "The signed copy ends where its signature says" if clean
                            else "Data was added to the signed copy after it was signed"))
    ctx_ok = sf.context == CTX_DOC.decode()
    checks.append(Check("context", ctx_ok, True, "File Signature Context",
                        "zirah-doc-v1" if ctx_ok else "Unexpected context; refused"))
    pq_ok = hash_ok and ctx_ok and verify_mldsa(sf.mldsa_pk, sf.signed_digest, sf.mldsa_sig, CTX_DOC)
    checks.append(Check("mldsa", pq_ok, True, "ML-DSA-65 Signature",
                        "FIPS 204 verification passed" if pq_ok else "Did not verify"))

    ec_present = sf.ecc_sig is not None and sf.ecc_pk is not None
    ec_ok = ec_present and hash_ok and verify_ecdsa(sf.ecc_pk, sf.signed_digest, sf.ecc_sig)

    entry = None
    if trusted:
        entry = next((t for t in trusted if t.mldsa_pk == sf.mldsa_pk), None)
    key_trusted = entry is not None and (not ec_present or entry.ecc_pk == sf.ecc_pk)
    hybrid_required = bool(entry and entry.ecc_pk)

    if ec_present:
        checks.append(Check("ecdsa", ec_ok, hybrid_required, "ECDSA P-256 Signature",
                            "Verification passed" if ec_ok else "Did not verify"))
    elif hybrid_required:
        checks.append(Check("ecdsa_missing", False, True, "ECDSA P-256 Signature",
                            "Missing, but the trusted keys include an ECDSA key"))
    else:
        checks.append(Check("ecdsa_absent", True, False, "ECDSA P-256 Signature",
                            "Not present (post-quantum only)"))
    checks.append(Check("trusted", key_trusted, True, "Signed by a Trusted Device",
                        f"Matches {entry.label or entry.uid}" if key_trusted
                        else "The signing key is not in the trusted-key file"))
    checks.append(Check("hardware", not sf.simulated, True, "Made by Zirah Hardware",
                        "Simulation Mode Signature, Not From the Device" if sf.simulated
                        else "Device signature"))

    math_ok = hash_ok and clean and ctx_ok and pq_ok and (ec_ok or not ec_present)
    if sf.simulated:
        verdict, title = "SIMULATED", "Simulated Signature, Not From Zirah Hardware"
    elif not math_ok or (hybrid_required and not ec_ok):
        verdict, title = "INVALID", "Invalid Signature"
    elif not key_trusted:
        verdict, title = "UNTRUSTED", "Valid Math, Unknown Signer"
    elif ec_present:
        verdict, title = "VALID", "Valid Hybrid Signature"
    else:
        verdict, title = "VALID_PQ_ONLY", "Valid Post-Quantum Signature"
    detail = {"VALID": "Both halves verified against the trusted keys",
              "VALID_PQ_ONLY": "ML-DSA-65 verified; this device signs without an ECDSA half",
              "UNTRUSTED": "Anyone can make a valid signature with their own key; trust the signer first",
              "INVALID": "Do not rely on this file",
              "SIMULATED": "Made in Simulation Mode with a throwaway key"}[verdict]

    # Times: what the signature vouches for, next to what this PC's file system
    # says now. Only the first group is covered by the signatures.
    created_now, modified_now = file_times(path) if path is not None else (None, None)
    signed_ok = math_ok and sf.version >= 2
    # signed: inside the signed manifest and the signatures verified.
    # unverified: inside the signed manifest, but the signatures failed, so the
    #   times prove nothing. not_signed: format version 1 did not sign them.
    times_state = "signed" if signed_ok else ("unverified" if sf.version >= 2 else "not_signed")
    times = {"signed_at": sf.signed_at or None, "signed_at_signed": "yes" if signed_ok else "no",
             "times_state": times_state,
             "created_at_signing": sf.file_created, "modified_at_signing": sf.file_modified,
             "created_now": created_now, "modified_now": modified_now, "verified_at": now_utc(),
             "changed_after_signing": "yes" if (not same_content and modified_now and sf.signed_at
                                               and modified_now > sf.signed_at) else "no"}
    return VerifyReport(verdict, checks, sf, title, detail,
                        (entry.label or entry.uid) if key_trusted and entry else "", found_in, times)


def verify_detached(doc: FileDigest, sig_path: Path,
                    trusted: Optional[List[TrustedDevice]]) -> VerifyReport:
    """A file against its separate .zirah.json signature file."""
    return verify_record(load_signature(sig_path), doc.sha256, doc.size, "detached", trusted, path=doc.path)


def verify_file(path: Path, sig_path: Optional[Path], trusted: Optional[List[TrustedDevice]],
                cancel: Optional[threading.Event] = None) -> Tuple[VerifyReport, FileDigest]:
    """A file against a separate signature file, or, without one, the
    signature inside it. Returns the report and the digest that was checked
    (of the original inside a signed copy)."""
    if sig_path is not None:
        dg = hash_file(path, cancel=cancel)
        return verify_detached(dg, sig_path, trusted), dg
    try:
        st = path.stat()
        e = embed.find(path)
        if e is None:
            raise ZirahError("NO_SIGNATURE", "This file carries no Zirah signature",
                             "Choose its .zirah.json signature file as well")
        try:
            rec = json.loads(e.record.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as ex:
            raise ZirahError("BAD_SIGNATURE_FILE", "The signature inside this file is damaged",
                             "The file was changed after it was signed") from ex
        sf = parse_record(rec)
        content = embed.hash_original(path, e, cancel)
        st2 = path.stat()
    except InterruptedError as ex:
        raise Cancelled("Verification was cancelled") from ex
    except (OSError, ValueError) as ex:
        raise ZirahError("READ_FAILED", "The file could not be read", clean_text(str(ex))) from ex
    if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
        raise ZirahError("FILE_CHANGED", "The file changed while it was being read",
                         "Close any program that is writing to it and try again")
    dg = FileDigest(path, path.name, e.size, content, st2.st_mtime_ns)
    return verify_record(sf, content, e.size, e.kind, trusted, clean=e.clean, path=path), dg


# ---------------------------------------------------------------------------
# The complete signing flow
# ---------------------------------------------------------------------------

def signing_container(path: Optional[Path], dest: Optional[Path] = None, detached: bool = False) -> str:
    """Where a new signature goes: inside a signed copy when the file's format
    allows it, otherwise (or when asked, or when the chosen destination is a
    .zirah.json name) in a separate signature file. Refuses a file that
    already carries a Zirah signature."""
    if path is None:
        return "detached"
    try:
        if embed.find(path) is not None:
            raise ZirahError("ALREADY_SIGNED", "This file already carries a Zirah signature",
                             "Sign the original file, or verify this one")
        if detached or (dest is not None and Path(dest).name.endswith((SIG_SUFFIX, SIM_SIG_SUFFIX))):
            return "detached"
        p = embed.plan(path)
    except OSError as e:
        raise ZirahError("READ_FAILED", "The file could not be read", clean_text(str(e))) from e
    return p.kind if p else "detached"


def sign_file(dev: Device, trust: TrustStore, doc_path: Optional[Path], dest: Optional[Path],
              digest: Optional[FileDigest] = None,
              on_event: Optional[Callable[[str, Dict[str, str]], None]] = None,
              cancel: Optional[threading.Event] = None, detached: bool = False) -> Tuple[Path, dict]:
    """Hash (or take a digest), build the manifest, sign it on the device,
    check everything on the PC, re-hash, then publish either a signed copy
    with the signature inside or a separate signature file. Returns (path,
    record). on_event also receives ("MANIFEST", {"oled": "XXXX XXXX"}) with
    the digits the OLED will show, before the device is asked."""
    simulated = dev.simulated
    info = dev.refresh_info()
    state, checks = readiness(info, None)
    blocking = [c for c in checks if c.required and not c.ok and c.key != "trusted"]
    if blocking:
        raise ZirahError("NOT_READY", f"Device Not Ready: {blocking[0].title}", blocking[0].detail)

    keys = dev.keys()
    t = trust.evaluate(info.uid, keys)
    if t.status != "trusted":
        raise ZirahError("UNTRUSTED_KEY" if t.status == "unpinned" else "KEY_CHANGED", t.reason)

    if digest is None:
        if doc_path is None:
            raise ValueError("need a file path or a digest")
        digest = hash_file(doc_path, cancel=cancel)
    src = digest.path
    container = signing_container(src, dest, detached)
    if dest is None:
        if src is None:
            raise ZirahError("NO_DEST", "Choose where to save the signature file")
        dest = default_signature_path(src, simulated, container)
    dest = check_destination(dest, simulated, container, src)
    plan = embed.plan(src) if container != "detached" else None
    if container != "detached" and (plan is None or plan.kind != container):
        raise ZirahError("FILE_CHANGED", "The file changed while it was being prepared", "Sign it again")

    manifest = make_manifest(digest, container, now_utc(), file_times(src) if src else (None, None))
    signed = manifest_digest(manifest)

    def emit(stage, fields):
        if on_event:
            on_event(stage, fields)

    emit("MANIFEST", {"oled": short_hex(signed)})
    res = dev.sign_digest(signed, on_event=emit, cancel=cancel)

    if on_event:
        on_event("PC_VERIFY", {})
    if not verify_mldsa(keys.mldsa_pk, signed, res.mldsa_sig, CTX_DOC):
        raise ZirahError("PC_VERIFY_FAILED", "The ML-DSA-65 signature did not verify on this PC; nothing was saved")
    if res.ecdsa_sig is not None:
        if not keys.ecc_pk or not verify_ecdsa(keys.ecc_pk, signed, res.ecdsa_sig):
            raise ZirahError("PC_VERIFY_FAILED", "The ECDSA signature did not verify on this PC; nothing was saved")

    if src is not None:
        again = hash_file(src)
        if again.sha256 != digest.sha256:
            raise ZirahError("FILE_CHANGED", "The file changed during signing; nothing was saved",
                             "Sign it again once nothing is editing it")

    record = build_signature(manifest, info, keys, res, simulated)
    if container == "detached":
        write_new_file(dest, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
        return dest, record

    line = json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    def check(tmp: Path) -> None:
        # The copy must give back exactly the signed original and this record.
        e = embed.find(tmp)
        if e is None or e.kind != container or e.record != line or not e.clean \
                or e.size != digest.size or embed.hash_original(tmp, e) != digest.sha256:
            raise ZirahError("WRITE_FAILED", "The signed copy did not read back correctly; nothing was saved")

    try:
        publish_new_file(dest, lambda f: embed.write_copy(src, f, plan, line), check)
    except ValueError as e:
        raise ZirahError("WRITE_FAILED", f"{dest.name} could not be made", clean_text(str(e))) from e
    return dest, record
