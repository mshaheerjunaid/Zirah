"""Simulated Zirah for demonstrations and tests, with no hardware attached.

It speaks the same line protocol as the firmware (docs/PROTOCOL.md) so the
desktop app and the tests exercise the real host code, but it is a separate
transport class: SimTransport.simulated is True, and the host core marks
everything that comes from it as simulated (signature files get the suffix
.zirah-sim.json and "simulated": true, and never verify as hardware
signatures).

What is simulated and how:
  * noise samples   Gaussian pseudo-random values (mean 2048, sd 210), NOT
                    avalanche noise and not an entropy source
  * keys            a throwaway ML-DSA-65 key (dilithium-py) and P-256 key
                    (cryptography) made fresh for each session
  * signatures      real ML-DSA-65 / ECDSA computations with those keys, so
                    the verification path is exercised end to end
  * timings         none are reported (STATS simulated=1), and BENCH refuses
  * second source   a second simulated noise block for the source B fields
  * USER button     press_button(), wired to a clearly labelled control
"""
from __future__ import annotations

import hashlib
import os
import random
import secrets
import threading
import time
from typing import List, Optional

from zirah_core import (CTX_DOC, CTX_PROVE, MONITOR_SAMPLES, PROVE_PREFIX, LinkError, Transport,
                        fingerprint)

CONFIRM_TIMEOUT_S = 30.0


class SimTransport(Transport):
    simulated = True
    name = "Simulation"

    def __init__(self, with_key: bool = True, with_atecc: bool = True, seed: Optional[int] = None):
        self._rng = random.Random(seed if seed is not None else secrets.randbits(64))
        self._in = bytearray()
        self._out = bytearray()
        self._cv = threading.Condition()
        self._closed = False
        self._unplugged = False
        self._button = threading.Event()
        self.uid = self._rng.getrandbits(96).to_bytes(12, "big").hex().upper()
        self.trng = "OK"
        self.confirm = True
        self.locked = False
        self.sigs = 0
        self.prove_left = 64
        self.mon_seq = 0
        self.monitor: List[int] = [2048] * MONITOR_SAMPLES
        self.monitor_b: List[int] = [1860] * MONITOR_SAMPLES
        self.corr_x1000 = 0
        self._mon_next = 0.0
        self.sk = self.pk = None
        self.ecc_key = None
        if with_key:
            self._new_key()
        if with_atecc:
            from cryptography.hazmat.primitives.asymmetric import ec
            self.ecc_key = ec.generate_private_key(ec.SECP256R1())
        self._thread = threading.Thread(target=self._run, name="zirah-sim", daemon=True)
        self._thread.start()

    # -- controls for the UI and tests -------------------------------------

    def press_button(self) -> None:
        self._button.set()

    def set_trng_fault(self, state: str = "FAULT_BIAS") -> None:
        self.trng = state

    def unplug(self) -> None:
        self._unplugged = True
        with self._cv:
            self._cv.notify_all()

    # -- Transport ---------------------------------------------------------

    def write(self, data: bytes) -> None:
        if self._unplugged or self._closed:
            raise LinkError("LINK_LOST", "The simulated device was disconnected")
        with self._cv:
            self._in += data
            self._cv.notify_all()

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + max(timeout, 0.0)
        with self._cv:
            while not self._out:
                if self._unplugged or self._closed:
                    raise LinkError("LINK_LOST", "The simulated device was disconnected")
                left = end - time.monotonic()
                if left <= 0:
                    return b""
                self._cv.wait(left)
            data = bytes(self._out)
            self._out.clear()
            return data

    def close(self) -> None:
        self._closed = True
        with self._cv:
            self._cv.notify_all()

    # -- simulated firmware --------------------------------------------------

    def _new_key(self) -> None:
        from dilithium_py.ml_dsa import ML_DSA_65
        self.pk, self.sk = ML_DSA_65.key_derive(secrets.token_bytes(32))

    def _ecc_pub(self) -> Optional[bytes]:
        if not self.ecc_key:
            return None
        n = self.ecc_key.public_key().public_numbers()
        return n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")

    def _put(self, text: str) -> None:
        with self._cv:
            self._out += text.encode("ascii") + b"\n"
            self._cv.notify_all()

    def _ok(self) -> None:
        self._put("OK")

    def _err(self, text: str) -> None:
        self._put("ERR " + text)

    def _input_pending(self) -> bool:
        with self._cv:
            return bool(self._in)

    def _take_line(self, timeout: float) -> Optional[str]:
        end = time.monotonic() + timeout
        with self._cv:
            while b"\n" not in self._in:
                if self._closed:
                    return None
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(min(left, 0.1))
            i = self._in.index(b"\n")
            raw = bytes(self._in[:i]).rstrip(b"\r")
            del self._in[:i + 1]
        if any(b < 0x20 or b > 0x7E for b in raw) or len(raw) > 4100:
            return ""
        return raw.decode("ascii")

    def _run(self) -> None:
        while not self._closed:
            line = self._take_line(0.1)
            if self._unplugged:
                return
            if line is None:
                self._monitor_tick()
                continue
            self._dispatch(line.strip())
            self._mon_next = time.monotonic() + 0.25

    def _monitor_tick(self) -> None:
        now = time.monotonic()
        if now < self._mon_next:
            return
        self._mon_next = now + 0.9
        if self.trng == "OK":
            g = self._rng.gauss
            self.monitor = [max(0, min(4095, int(round(g(2048, 210))))) for _ in range(MONITOR_SAMPLES)]
            self.monitor_b = [max(0, min(4095, int(round(g(1860, 190))))) for _ in range(MONITOR_SAMPLES)]
        else:
            self.monitor = [2048 + self._rng.choice((-1, 0, 0, 1)) for _ in range(MONITOR_SAMPLES)]
            self.monitor_b = [1860 + self._rng.choice((-1, 0, 0, 1)) for _ in range(MONITOR_SAMPLES)]
        self.corr_x1000 = self._corr(self.monitor, self.monitor_b)
        self.mon_seq += 1

    @staticmethod
    def _corr(a: List[int], b: List[int]) -> int:
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        if va <= 0 or vb <= 0:
            return 0
        return int(1000 * sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5)

    def _wait_button(self, title: str, args: str) -> str:
        """Same rules as the firmware: a byte from the host cancels."""
        self._button.clear()
        self._put(f"EVT CONFIRM {args} timeout_ms={int(CONFIRM_TIMEOUT_S * 1000)}")
        end = time.monotonic() + CONFIRM_TIMEOUT_S
        while time.monotonic() < end:
            if self._button.wait(0.05):
                self._button.clear()
                return "OK"
            if self._input_pending():
                self._take_line(0.2)
                return "CANCELLED by host"
            if self._closed or self._unplugged:
                return "CANCELLED"
        return "TIMEOUT no button press"

    def _stats(self, s: Optional[List[int]] = None):
        s = self.monitor if s is None else s
        n = len(s)
        mean = sum(s) / n
        sd = (sum((x - mean) ** 2 for x in s) / n) ** 0.5
        clipped = sum(1 for x in s if x in (0, 4095))
        return int(mean), int(sd * 10), min(s), max(s), clipped

    def _dispatch(self, line: str) -> None:
        if not line:
            self._err("BAD_LINE empty, too long or non-printable line")
            return
        parts = line.split()
        cmd, args = parts[0].upper(), parts[1:]
        handler = getattr(self, "_cmd_" + cmd.lower(), None)
        if handler is None or not cmd.isalpha():
            self._err("UNKNOWN unknown command (try HELP)")
            return
        handler(args)

    def _cmd_ping(self, args):
        self._put("PONG")
        self._ok()

    def _cmd_info(self, args):
        p = self._put
        p("FW 1.0.0-sim")
        p("PROTO 1")
        p("SIMULATED 1")
        p("ALG ML-DSA-65 (FIPS 204), simulated in software on this PC")
        p("UID " + self.uid.lower())
        p("KAT ok scope=shake256,ml-dsa-65")
        p("HARDENING mpu=0 canary=0 stack=0")
        p("VDD_MV 0")
        p("VBIAS_MV 0")
        p("TRNG " + self.trng)
        p("H_ASSUMED simulated")
        p("HEALTH symbols=0 rct_cut=0 apt_cut=0 fails=0 fails_total=0")
        p(f"MONITOR seq={self.mon_seq} valid={1 if self.trng == 'OK' else 0} std_x10=0")
        p(f"SOURCE_B present std_x10={self._stats(self.monitor_b)[1]} corr_x1000={self.corr_x1000} "
          "symbols=0 fails=0")
        p("KEY " + ("present" if self.pk else "none"))
        if self.pk:
            p("PK_FP " + fingerprint(self.pk).lower())
        if self.ecc_key:
            p("ATECC ready rev=00000000 config_locked=1 data_locked=1")
            p("ECC_FP " + fingerprint(self._ecc_pub()).lower())
        else:
            p("ATECC absent")
        p("OLED present")
        p("USB configured")
        p("CONFIRM " + ("on" if self.confirm else "off"))
        p("SIGNING " + ("locked" if self.locked else "enabled"))
        p(f"PROVE_LEFT {self.prove_left}")
        p(f"SIGS {self.sigs}")
        self._ok()

    def _cmd_peek(self, args):
        mean, sd10, lo, hi, clip = self._stats()
        valid = 1 if self.trng == "OK" else 0
        bmean, bsd10, _, _, bclip = self._stats(self.monitor_b)
        self._put(f"PEEK seq={self.mon_seq} n={MONITOR_SAMPLES if self.mon_seq else 0} rate=20000 "
                  f"valid={valid} mean={mean} std_x10={sd10} min={lo} max={hi} clipped={clip} trng={self.trng} "
                  f"b=1 b_mean={bmean} b_std_x10={bsd10} b_clipped={bclip} corr_x1000={self.corr_x1000}")
        if self.mon_seq:
            self._put("SAMPLES " + "".join(f"{x:03x}" for x in self.monitor))
        self._ok()

    def _cmd_pk(self, args):
        if self.locked:
            self._err("LOCKED a signature self-check failed; power-cycle the device")
            return
        if not self.pk:
            self._err("NO_KEY run KEYGEN")
            return
        self._put("PK " + self.pk.hex())
        self._put("PK_FP " + fingerprint(self.pk).lower())
        self._ok()

    def _cmd_eccpk(self, args):
        pub = self._ecc_pub()
        if not pub:
            self._err("NO_ECC ATECC608 not provisioned (see ATECC PROVISION)")
            return
        self._put("ECCPK " + pub.hex())
        self._put("ECC_FP " + fingerprint(pub).lower())
        self._ok()

    def _cmd_health(self, args):
        time.sleep(0.12)
        self._put("TRNG " + self.trng)
        self._put("HEALTH symbols=1024 rct_cut=0 rct_max=0 apt_cut=0 apt_max=0")
        self._put("VBIAS_MV 0")
        if self.trng == "OK":
            self._ok()
        else:
            self._err("ENTROPY_FAULT entropy source unhealthy (see docs/GUIDE.md)")

    def _cmd_bench(self, args):
        # Timings of a PC simulation would be meaningless next to the board's.
        self._err("SIMULATED benchmarks need the real board; the simulator does not time anything")

    def _cmd_vbias(self, args):
        self._put("VBIAS_MV 0 VDD_MV 0")
        self._ok()

    def _cmd_i2cscan(self, args):
        self._put("I2C 0x3C")
        if self.ecc_key:
            self._put("I2C 0x60 atecc-awake")
        self._ok()

    def _cmd_analyze(self, args):
        mean, sd10, lo, hi, clip = self._stats()
        self._put(f"N {MONITOR_SAMPLES} RATE 20000")
        self._put(f"MIN {lo} MAX {hi} MEAN {mean} STD {sd10 // 10}.{sd10 % 10}")
        self._put(f"CLIPPED {clip} DISTINCT12 0 DISTINCT8 0")
        self._put("MCV_H 0.00 bits/symbol (quick estimate only)")
        self._put("AUTOCORR1 0.000")
        self._ok()

    def _cmd_confirm(self, args):
        a = (args[0].upper() if args else "")
        if a == "ON":
            self.confirm = True
            self._ok()
        elif a == "OFF":
            r = self._wait_button("NO BUTTON?", "purpose=confirm-off")
            if r != "OK":
                self._err(r)
                return
            self.confirm = False
            self._ok()
        else:
            self._err("BAD_ARG usage CONFIRM ON|OFF")

    def _cmd_keygen(self, args):
        force = bool(args) and args[0].upper() == "FORCE"
        if self.locked:
            self._err("LOCKED a signature self-check failed; power-cycle the device")
            return
        if self.pk and not force:
            self._err("KEY_EXISTS use KEYGEN FORCE to destroy the old key and make a new one")
            return
        if self.pk and force:
            r = self._wait_button("REPLACE?", "purpose=keygen-force")
            if r != "OK":
                self._err(r)
                return
        if self.trng != "OK":
            self._err("ENTROPY_FAULT entropy source unhealthy, refusing to generate a key")
            return
        for stage in ("ENTROPY", "REFRESH", "SIGN", "VERIFY"):
            self._put("EVT " + stage)
            if stage == "ENTROPY":
                self._new_key()
        self._put("PK " + self.pk.hex())
        self._put("PK_FP " + fingerprint(self.pk).lower())
        self._put("PCT ok")
        self._ok()

    def _cmd_sign(self, args):
        from dilithium_py.ml_dsa import ML_DSA_65
        if len(args) != 1 or len(args[0]) != 64:
            # Word for word the firmware 1.0.0 reply, so the simulator answers like the board.
            self._err("BAD_ARG usage SIGN <64 hex chars = SHA-256 of the document>")
            return
        try:
            digest = bytes.fromhex(args[0])
        except ValueError:
            self._err("BAD_ARG usage SIGN <64 hex chars = SHA-256 of the document>")
            return
        if self.locked:
            self._err("LOCKED a signature self-check failed; power-cycle the device")
            return
        if not self.pk:
            self._err("NO_KEY run KEYGEN")
            return
        if self.confirm:
            r = self._wait_button("SIGN?", "doc=" + digest[:4].hex().upper())
            if r != "OK":
                self._err(r)
                return
        self._put("EVT ENTROPY")
        if self.trng != "OK":
            self._err("ENTROPY_FAULT entropy source failed its startup test, refusing to sign")
            return
        time.sleep(0.25)
        self._put("EVT REFRESH")
        time.sleep(0.2)
        self._put("EVT SIGN")
        sig = ML_DSA_65.sign(self.sk, digest, ctx=CTX_DOC)
        self._put("EVT VERIFY")
        if not ML_DSA_65.verify(self.pk, digest, sig, ctx=CTX_DOC):
            self.locked = True
            self._err("SELF_CHECK signature failed on-device verification; withheld, signing locked")
            return
        ecdsa = None
        if self.ecc_key:
            self._put("EVT ECDSA")
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec, utils
            der = self.ecc_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
            r_, s_ = utils.decode_dss_signature(der)
            ecdsa = r_.to_bytes(32, "big") + s_.to_bytes(32, "big")
        self._put("MLDSA " + sig.hex())
        if ecdsa:
            self._put("ECDSA " + ecdsa.hex())
        else:
            self._put("ECDSA NONE ATECC608 not provisioned")
        self._put("STATS simulated=1")
        self.sigs += 1
        self._ok()

    def _cmd_prove(self, args):
        from dilithium_py.ml_dsa import ML_DSA_65
        if len(args) != 1 or len(args[0]) != 64:
            self._err("BAD_ARG usage PROVE <64 hex chars = host nonce>")
            return
        if self.prove_left <= 0:
            self._err("RATE_LIMIT identity proof budget used up for this boot")
            return
        if not self.pk:
            self._err("NO_KEY run KEYGEN")
            return
        self.prove_left -= 1
        msg = PROVE_PREFIX + bytes.fromhex(self.uid) + bytes.fromhex(args[0])
        self._put("EVT ENTROPY")
        self._put("EVT REFRESH")
        self._put("EVT SIGN")
        sig = ML_DSA_65.sign(self.sk, msg, ctx=CTX_PROVE)
        self._put("EVT VERIFY")
        self._put("PROOF " + sig.hex())
        self._ok()

    def _cmd_atecc(self, args):
        a = (args[0].upper() if args else "")
        if a == "INFO" and self.ecc_key:
            self._put("REVISION 00000000")
            self._put("SERIAL 000000000000000000")
            self._put("CONFIG_LOCKED yes")
            self._put("DATA_LOCKED yes")
            self._put("SLOT0_LOCKED no")
            self._put("SLOT0_CONFIG 0x0083 KEYCONFIG 0x0033")
            self._put("ZIRAH_READY yes")
            self._ok()
        elif a == "INFO":
            self._err("ATECC no wake response (check wiring, pull-ups, address)")
        else:
            self._err("BAD_ARG the simulation supports ATECC INFO only")

    def _cmd_help(self, args):
        self._put("Zirah simulation: PING INFO PEEK PK ECCPK HEALTH SIGN PROVE KEYGEN CONFIRM")
        self._ok()

    def _cmd_reboot(self, args):
        self._ok()


def digest_of(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


if __name__ == "__main__":  # quick manual check
    from zirah_core import Device, Link
    d = Device(Link(SimTransport()))
    print(d.handshake())
    os._exit(0)
