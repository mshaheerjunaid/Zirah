"""Tests for host/zirah_core.py, the rules shared by the CLI and the app.

    python -m unittest discover -s tests/host -v

Uses the simulated device (host/zirah_sim.py) and small scripted transports,
so no hardware is needed. Each test runs with its own ZIRAH_HOME folder.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))

import zirah_core as core  # noqa: E402
from zirah_core import Device, Link, LinkError, ProtocolError, TrustStore, ZirahError  # noqa: E402
from zirah_sim import SimTransport  # noqa: E402


class Scripted(core.Transport):
    """Replies to each command line with canned output."""

    def __init__(self, replies):
        self.replies = replies
        self.sent = []
        self._out = bytearray()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        self.sent.append(data)
        line = data.decode("ascii", "replace").strip()
        if not line:
            return
        cmd = line.split()[0]
        reply = self.replies.get(cmd)
        if reply is None:
            return
        with self._lock:
            self._out += (reply(line) if callable(reply) else reply).encode("latin-1")

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._out:
                    d = bytes(self._out)
                    self._out.clear()
                    return d
            time.sleep(0.01)
        return b""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zirah-test-"))
        os.environ["ZIRAH_HOME"] = str(self.tmp / "home")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sim_device(self, **kw):
        t = SimTransport(**kw)
        d = Device(Link(t))
        d.handshake()
        return t, d

    def doc(self, name="contract.pdf", size=50_000) -> Path:
        p = self.tmp / name
        p.write_bytes(os.urandom(size))
        return p

    def sign(self, t, d, store, doc, dest=None, **kw):
        def on_event(stage, fields):
            if stage == "CONFIRM":
                threading.Timer(0.05, t.press_button).start()
        dest = dest or core.default_signature_path(doc, d.simulated)
        return core.sign_file(d, store, doc, dest, on_event=on_event, **kw)


class Helpers(Base):
    def test_parse_hex_exact_length(self):
        self.assertEqual(core.parse_hex("00ff", 2, "x"), b"\x00\xff")
        for bad in ("0", "00f", "zz", "00ff00", 5):
            with self.assertRaises(ProtocolError):
                core.parse_hex(bad, 2, "x")

    def test_clean_text_strips_control_characters(self):
        self.assertEqual(core.clean_text("ok\x1b[2Jbad\n"), "ok?[2Jbad?")
        self.assertEqual(len(core.clean_text("a" * 1000, 50)), 50)

    def test_write_new_file_never_overwrites(self):
        p = self.tmp / "a.zirah.json"
        core.write_new_file(p, b"one")
        with self.assertRaises(ZirahError) as cm:
            core.write_new_file(p, b"two")
        self.assertEqual(cm.exception.code, "EXISTS")
        self.assertEqual(p.read_bytes(), b"one")
        self.assertEqual([x.name for x in self.tmp.iterdir() if x.name.endswith(".tmp")], [])

    def test_unwritable_destination_is_a_clear_error(self):
        blocker = self.tmp / "not-a-folder"
        blocker.write_text("x")
        with self.assertRaises(ZirahError) as cm:
            core.write_new_file(blocker / "a.zirah.json", b"one")
        self.assertEqual(cm.exception.code, "WRITE_FAILED")

    def test_unreadable_file_is_a_clear_error(self):
        with self.assertRaises(ZirahError) as cm:
            core.hash_file(self.tmp)            # a folder cannot be read as a file
        self.assertEqual(cm.exception.code, "READ_FAILED")

    def test_default_signature_path_picks_a_free_name(self):
        doc = self.doc()
        first = core.default_signature_path(doc)
        first.write_text("x")
        second = core.default_signature_path(doc)
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.endswith(core.SIG_SUFFIX))

    def test_destination_rules(self):
        doc = self.doc()
        with self.assertRaises(ZirahError):
            core.check_destination(self.tmp / "x.txt", False)            # wrong suffix
        with self.assertRaises(ZirahError):
            core.check_destination(self.tmp / "x.zirah.json", True)      # simulation needs its suffix
        with self.assertRaises(ZirahError):
            core.check_destination(Path("relative.zirah.json"), False)
        core.check_destination(doc.with_name("ok.zirah.json"), False)


class LinkTests(Base):
    def test_rejects_control_characters(self):
        t = Scripted({"PING": "PO\x07NG\nOK\n"})
        with self.assertRaises(ProtocolError):
            Link(t).command("PING", timeout=2)

    def test_rejects_oversized_lines(self):
        t = Scripted({"PING": "A" * (core.MAX_LINE + 10) + "\nOK\n"})
        with self.assertRaises(ProtocolError):
            Link(t).command("PING", timeout=2)

    def test_err_codes(self):
        t = Scripted({"SIGN": "ERR LOCKED a signature self-check failed\n"})
        r = Link(t).command("SIGN " + "00" * 32, timeout=2)
        self.assertFalse(r.ok)
        self.assertEqual(r.err_code, "LOCKED")

    def test_refuses_to_send_unprintable_commands(self):
        with self.assertRaises(ProtocolError):
            Link(Scripted({})).send("PING\nREBOOT")

    def test_timeout(self):
        with self.assertRaises(LinkError):
            Link(Scripted({})).command("PING", timeout=0.3)


class DeviceTests(Base):
    def test_handshake_and_info(self):
        t, d = self.sim_device()
        self.assertTrue(d.simulated)
        self.assertEqual(d.info.protocol, core.PROTO_VERSION)
        self.assertRegex(d.info.uid, r"^[0-9A-F]{24}$")
        self.assertEqual(d.info.key, "present")

    def test_peek_returns_512_samples(self):
        t, d = self.sim_device()
        time.sleep(1.1)
        b = d.peek()
        self.assertIsNotNone(b)
        self.assertEqual(len(b.samples), core.MONITOR_SAMPLES)
        self.assertTrue(all(0 <= s <= 4095 for s in b.samples))

    def test_readiness_is_never_ready_in_simulation(self):
        t, d = self.sim_device()
        keys = d.keys()
        store = TrustStore.memory()
        store.pin(d.info.uid, keys)
        state, checks = core.readiness(d.info, store.evaluate(d.info.uid, keys))
        self.assertEqual(state, "SIMULATED")

    def test_readiness_error_on_failed_required_check(self):
        info = core.DeviceInfo(firmware="1.0.0", protocol=1, kat_ok=True, trng="FAULT_BIAS", key="present",
                               signing="enabled", oled=True, confirm=True, atecc="ready", mpu=True,
                               canary=True, rdp=1, fw_hash=core.local_firmware_hash())
        trust = core.TrustState("trusted", "ok")
        state, _ = core.readiness(info, trust)
        self.assertEqual(state, "ERROR")
        info.trng = "OK"
        self.assertEqual(core.readiness(info, trust)[0], "READY")
        info.protocol = 2
        self.assertEqual(core.readiness(info, trust)[0], "ERROR")

    def test_readout_protection_off_is_a_warning(self):
        r = core.Reply(True, ["FW 1.0.0", "PROTO 1", "KAT ok", "HARDENING mpu=1 canary=1 stack=60160 rdp=0",
                              "TRNG OK", "KEY present", "SIGNING enabled", "OLED present", "CONFIRM on",
                              "ATECC ready rev=00006002 config_locked=1 data_locked=1",
                              "FW_HASH " + core.local_firmware_hash().lower()])
        info = core.parse_info(r, simulated=False)
        self.assertEqual(info.rdp, 0)
        trust = core.TrustState("trusted", "ok")
        state, checks = core.readiness(info, trust)
        self.assertEqual(state, "DEGRADED")
        rdp = [c for c in checks if c.key == "rdp"][0]
        self.assertFalse(rdp.ok)
        self.assertFalse(rdp.required)
        self.assertIn("Level 0", rdp.detail)
        r.lines[3] = "HARDENING mpu=1 canary=1 stack=60160 rdp=1"
        self.assertEqual(core.readiness(core.parse_info(r, simulated=False), trust)[0], "READY")
        # A line without the field, or with a value outside 0 to 2, is "not reported".
        for line in ("HARDENING mpu=1 canary=1 stack=60160", "HARDENING mpu=1 canary=1 rdp=7"):
            r.lines[3] = line
            info = core.parse_info(r, simulated=False)
            self.assertEqual(info.rdp, -1)
            self.assertEqual(core.readiness(info, trust)[0], "DEGRADED")

    def test_firmware_build_is_compared_with_this_pcs_zirah_bin(self):
        info = core.DeviceInfo(firmware="1.0.0", protocol=1, kat_ok=True, trng="OK", key="present",
                               signing="enabled", oled=True, confirm=True, atecc="ready", mpu=True,
                               canary=True, rdp=1)
        trust = core.TrustState("trusted", "ok")
        local = core.local_firmware_hash()
        self.assertRegex(local, r"^[0-9A-F]{32}$")
        for fw_hash, ok in ((local, True), ("00" * 16, False), ("", False)):
            info.fw_hash = fw_hash
            state, checks = core.readiness(info, trust)
            match = [c for c in checks if c.key == "fwmatch"][0]
            self.assertEqual(match.ok, ok)
            self.assertFalse(match.required)
            self.assertEqual(state, "READY" if ok else "DEGRADED")
        # A PC without the firmware file (a verifier-only copy) does not judge the build.
        self.assertEqual(core.local_firmware_hash(Path(self.tmp) / "missing.bin"), "")

    def test_new_info_and_peek_fields(self):
        r = core.Reply(True, ["FW 1.0.0", "PROTO 1", "TEMP_C10 -45", "STM32_RNG fault",
                              "SOURCE_B present std_x10=1903 corr_x1000=-12 symbols=1024 fails=0"])
        i = core.parse_info(r, simulated=False)
        self.assertEqual((i.temp_c10, i.stm32_rng, i.source_b, i.b_std_x10, i.corr_x1000),
                         (-45, "fault", "present", 1903, -12))
        state, checks = core.readiness(i, None)
        self.assertIn("mcurng", [c.key for c in checks if not c.ok])
        self.assertNotIn("jitter", [c.key for c in checks])         # not reported: no warning
        r = core.Reply(True, ["SOURCE_B absent", "TEMP_C10 hot", "STM32_RNG maybe"])
        i = core.parse_info(r, simulated=False)
        self.assertEqual((i.source_b, i.corr_x1000, i.temp_c10, i.stm32_rng),
                         ("absent", None, None, ""))
        i = core.parse_info(core.Reply(True, ["JITTER live mean_ticks=2610 distinct=11 spread=12 mode=40 of=256",
                                              "ACCEL live chip=lis3dsh least_distinct=5 of=16"]), simulated=False)
        self.assertEqual((i.jitter, i.accel, i.accel_chip), ("live", "live", "lis3dsh"))
        self.assertNotIn("jitter", [c.key for c in core.readiness(i, None)[1]])
        i = core.parse_info(core.Reply(True, ["JITTER fault", "ACCEL absent chip=none"]), simulated=False)
        self.assertEqual((i.jitter, i.accel, i.accel_chip), ("fault", "absent", "none"))
        failed = [c.key for c in core.readiness(i, None)[1] if not c.ok]
        self.assertIn("jitter", failed)
        self.assertNotIn("accel", failed)                           # absent is not a failure
        i = core.parse_info(core.Reply(True, ["BOARD devebox noise=PA4 serial=PA9/PA10 hse_mhz=8"]), simulated=False)
        self.assertEqual((i.board, i.hse_mhz, i.noise_pin), ("devebox", 8, "PA4"))
        self.assertEqual(core.firmware_bin(i.board).name, "zirah-devebox.bin")
        i = core.parse_info(core.Reply(True, ["BOARD devebox noise=PA4;rm serial=PA9/PA10 hse_mhz=8"]),
                            simulated=False)
        self.assertEqual(i.noise_pin, "")                           # anything odd is left out, not shown
        i = core.parse_info(core.Reply(True, ["BOARD devebox noise=PA4 serial=PA9/PA10 hse_mhz=0"]), simulated=False)
        self.assertEqual(i.hse_mhz, 0)                              # running from the internal oscillator
        for line in ([], ["BOARD nucleo"]):                         # older firmware, or a board we do not know
            self.assertEqual(core.parse_info(core.Reply(True, line), simulated=False).board, "discovery")
        i = core.parse_info(core.Reply(True, ["JITTER wobbly", "ACCEL live chip=bmi160"]), simulated=False)
        self.assertEqual((i.jitter, i.accel, i.accel_chip), ("", "live", ""))
        samples = "".join(f"{(2048 + k) % 4096:03x}" for k in range(core.MONITOR_SAMPLES))
        r = core.Reply(True, ["PEEK seq=3 n=512 rate=20000 valid=1 mean=2048 std_x10=2100 min=1 max=4000 "
                              "clipped=0 trng=OK b=1 b_mean=1860 b_std_x10=1900 b_clipped=2 corr_x1000=-7",
                              "SAMPLES " + samples])
        b = core.parse_peek(r, simulated=False)
        self.assertEqual((b.b, b.b_mean, b.b_std_x10, b.b_clipped, b.corr_x1000), (True, 1860, 1900, 2, -7))
        r.lines[0] = r.lines[0].split(" b=1")[0]
        b = core.parse_peek(r, simulated=False)
        self.assertEqual((b.b, b.corr_x1000), (False, None))

    def test_benchmark_reply(self):
        lines = ["EVT BENCH done=5 of=5", "BENCH build ntt=asm n=5 cpu_mhz=168 sources=1",
                 "BENCH keygen_us value=12000", "BENCH mask_load_us value=900",
                 "BENCH ntt_cycles signer=8000 c=30000"]
        lines += [f"BENCH {k} n=5 min=1 median=2 max=3 mean=2" for k in core.BENCH_STAGES]
        res = core.parse_bench(core.Reply(True, lines))
        self.assertEqual(res["build"]["ntt"], "asm")
        self.assertEqual(res["stages"]["sign_us"]["median"], 2)
        self.assertEqual(res["values"]["ntt_cycles"], {"signer": 8000, "c": 30000})
        with self.assertRaises(ProtocolError):
            core.parse_bench(core.Reply(True, lines[:3]))           # incomplete
        with self.assertRaises(ProtocolError):
            core.parse_bench(core.Reply(True, lines + ["BENCH ../etc n=1"]))
        # The simulator refuses: simulated timings must never look like the board's.
        t, d = self.sim_device()
        with self.assertRaises(ZirahError) as cm:
            d.bench(3)
        self.assertEqual(cm.exception.code, "SIMULATED")

    def test_identity_proof(self):
        t, d = self.sim_device()
        ok, _ = core.check_identity(d, d.keys())
        self.assertTrue(ok)


class SigningTests(Base):
    def setUp(self):
        super().setUp()
        self.t, self.d = self.sim_device()
        self.store = TrustStore.memory()

    def pin(self):
        self.store.pin(self.d.info.uid, self.d.keys())

    def test_refuses_untrusted_key(self):
        with self.assertRaises(ZirahError) as cm:
            self.sign(self.t, self.d, self.store, self.doc())
        self.assertEqual(cm.exception.code, "UNTRUSTED_KEY")

    def test_sign_and_verify(self):
        self.pin()
        doc = self.doc()
        path, rec = self.sign(self.t, self.d, self.store, doc)
        self.assertTrue(path.name.endswith(core.SIM_SIG_SUFFIX))
        self.assertTrue(rec["simulated"])
        self.assertEqual(rec["context"], "zirah-doc-v1")
        rep = core.verify_detached(core.hash_file(doc), path, self.store.devices)
        # A simulated signature never passes as a hardware signature.
        self.assertEqual(rep.verdict, "SIMULATED")
        checks = {c.key: c.ok for c in rep.checks}
        self.assertTrue(checks["hash"] and checks["mldsa"] and checks["ecdsa"])

    def test_existing_destination_is_not_overwritten(self):
        self.pin()
        doc = self.doc()
        dest = core.default_signature_path(doc, True)
        dest.write_text("keep me")
        with self.assertRaises(ZirahError) as cm:
            self.sign(self.t, self.d, self.store, doc, dest=dest)
        self.assertEqual(cm.exception.code, "EXISTS")
        self.assertEqual(dest.read_text(), "keep me")

    def test_file_changed_during_signing_saves_nothing(self):
        self.pin()
        doc = self.doc()
        dg = core.hash_file(doc)
        dest = core.default_signature_path(doc, True)

        def on_event(stage, fields):
            if stage == "CONFIRM":
                doc.write_bytes(os.urandom(10))       # someone edits the file
                threading.Timer(0.05, self.t.press_button).start()
        with self.assertRaises(ZirahError) as cm:
            core.sign_file(self.d, self.store, None, dest, digest=dg, on_event=on_event)
        self.assertEqual(cm.exception.code, "FILE_CHANGED")
        self.assertFalse(dest.exists())

    def test_cancel_before_button(self):
        self.pin()
        doc = self.doc()
        cancel = threading.Event()

        def on_event(stage, fields):
            if stage == "CONFIRM":
                cancel.set()
        dest = core.default_signature_path(doc, True)
        with self.assertRaises(core.Cancelled):
            core.sign_file(self.d, self.store, doc, dest, on_event=on_event, cancel=cancel)
        self.assertFalse(dest.exists())
        # The link is still usable afterwards.
        self.assertTrue(self.d.link.command("PING", timeout=3).ok)

    def test_changed_key_is_reported_not_replaced(self):
        self.pin()
        old = self.store.devices[0]
        self.t.press_button()
        threading.Timer(0.2, self.t.press_button).start()
        r = self.d.keygen(force=True)
        self.assertTrue(r.ok)
        keys = self.d.keys()
        self.assertEqual(self.store.evaluate(self.d.info.uid, keys).status, "changed")
        with self.assertRaises(ZirahError):
            self.store.pin(self.d.info.uid, keys)
        self.assertEqual(self.store.devices[0].mldsa_pk, old.mldsa_pk)
        self.store.pin(self.d.info.uid, keys, replace=True)
        self.assertEqual(self.store.evaluate(self.d.info.uid, keys).status, "trusted")


class DigestMismatch(Base):
    def test_device_echoing_another_digest_is_cancelled(self):
        def sign_reply(line):
            return "EVT CONFIRM doc=00000000 timeout_ms=30000\n"
        t = Scripted({"SIGN": sign_reply})
        d = Device(Link(t))
        with self.assertRaises(ProtocolError) as cm:
            d.sign_digest(b"\x11" * 32)
        self.assertEqual(cm.exception.code, "DIGEST_MISMATCH")
        self.assertIn(b"\n", t.sent[-1])        # the cancel byte went out


class VerifyTests(Base):
    """Verification against a hardware-style record made with a test key."""

    def setUp(self):
        super().setUp()
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from dilithium_py.ml_dsa import ML_DSA_65
        self.pk, self.sk = ML_DSA_65.key_derive(b"\x07" * 32)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        n = self.ec.public_key().public_numbers()
        self.ecpk = n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")
        self.docp = self.doc()
        self.dg = core.hash_file(self.docp)
        self.manifest = core.make_manifest(self.dg, "detached")
        signed = core.manifest_digest(self.manifest)
        sig = ML_DSA_65.sign(self.sk, signed, ctx=core.CTX_DOC)
        der = self.ec.sign(signed, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        r, s = utils.decode_dss_signature(der)
        info = core.DeviceInfo(firmware="1.0.0", protocol=1, uid="A" * 24)
        keys = core.DeviceKeys(self.pk, self.ecpk)
        res = core.SignResult(sig, r.to_bytes(32, "big") + s.to_bytes(32, "big"), "", {})
        self.rec = core.build_signature(self.manifest, info, keys, res, simulated=False)
        self.trusted = [core.TrustedDevice("A" * 24, self.pk, self.ecpk)]

    def write(self, rec, name="doc.zirah.json") -> Path:
        p = self.tmp / name
        p.write_text(json.dumps(rec))
        return p

    def verdict(self, rec=None, dg=None, trusted="default"):
        trusted = self.trusted if trusted == "default" else trusted
        return core.verify_detached(dg or self.dg, self.write(rec or self.rec), trusted).verdict

    def test_valid_hybrid(self):
        self.assertEqual(self.verdict(), "VALID")

    def test_unknown_signer(self):
        self.assertEqual(self.verdict(trusted=[]), "UNTRUSTED")

    def test_tampered_file(self):
        self.docp.write_bytes(b"changed")
        self.assertEqual(self.verdict(dg=core.hash_file(self.docp)), "INVALID")

    def test_tampered_signature(self):
        rec = json.loads(json.dumps(self.rec))
        h = rec["mldsa65"]["signature"]
        rec["mldsa65"]["signature"] = ("0" if h[0] != "0" else "1") + h[1:]
        self.assertEqual(self.verdict(rec), "INVALID")

    def test_context_cannot_be_chosen_by_the_file(self):
        rec = dict(self.rec, context="zirah-attest-v1")
        self.assertEqual(self.verdict(rec), "INVALID")

    def test_missing_simulated_field_counts_as_simulated(self):
        rec = dict(self.rec)
        rec.pop("simulated")
        self.assertEqual(self.verdict(rec), "SIMULATED")

    def test_hybrid_required_when_trusted_key_has_ecdsa(self):
        rec = dict(self.rec, ecdsa_p256=None)
        self.assertEqual(self.verdict(rec), "INVALID")
        trusted_pq_only = [core.TrustedDevice("A" * 24, self.pk, None)]
        self.assertEqual(self.verdict(rec, trusted=trusted_pq_only), "VALID_PQ_ONLY")

    def test_signed_name_and_times_cannot_be_edited(self):
        for field, value in (("name", "other.pdf"), ("signed_at_utc", "2020-01-01T00:00:00Z"),
                             ("file_modified_utc", "2020-01-01T00:00:00Z"), ("size", self.dg.size + 1)):
            rec = json.loads(json.dumps(self.rec))
            rec["manifest"][field] = value
            self.assertEqual(self.verdict(rec), "INVALID", field)
        rec = json.loads(json.dumps(self.rec))
        rec["manifest"]["extra"] = 1                    # unknown fields are refused, not ignored
        with self.assertRaises(ZirahError):
            self.verdict(rec)

    def test_report_separates_signed_and_file_system_times(self):
        rep = core.verify_detached(self.dg, self.write(self.rec), self.trusted)
        t = rep.times
        self.assertEqual(t["signed_at"], self.manifest["signed_at_utc"])
        self.assertEqual(t["signed_at_signed"], "yes")
        self.assertEqual(t["times_state"], "signed")
        self.assertEqual(t["modified_at_signing"], self.manifest["file_modified_utc"])
        self.assertEqual(t["modified_now"], core.file_times(self.docp)[1])
        self.assertTrue(t["verified_at"])
        self.assertEqual(t["changed_after_signing"], "no")
        self.docp.write_bytes(b"edited later")
        os.utime(self.docp, (time.time() + 120, time.time() + 120))   # a change dated after the signing
        rep = core.verify_detached(core.hash_file(self.docp), self.write(self.rec), self.trusted)
        self.assertEqual((rep.verdict, rep.times["changed_after_signing"]), ("INVALID", "yes"))
        # The times are still in the signed data, but a failed signature vouches for nothing.
        self.assertEqual((rep.times["signed_at_signed"], rep.times["times_state"]), ("no", "unverified"))

    def test_rejects_malformed_files(self):
        for bad in ("not json", json.dumps({"format": "other"}), json.dumps({"format": core.SIG_FORMAT})):
            p = self.tmp / "bad.zirah.json"
            p.write_text(bad)
            with self.assertRaises(ZirahError):
                core.load_signature(p)

    def test_rejects_huge_files(self):
        p = self.tmp / "big.zirah.json"
        p.write_bytes(b" " * (core.MAX_SIGNATURE_FILE + 1))
        with self.assertRaises(ZirahError):
            core.load_signature(p)


class TrustStoreFile(Base):
    def test_round_trip_and_export(self):
        t, d = self.sim_device()
        store = TrustStore(self.tmp / "keys.json")
        store.pin(d.info.uid, d.keys(), "bench")
        again = TrustStore(self.tmp / "keys.json")
        self.assertEqual(again.devices[0].mldsa_pk, d.keys().mldsa_pk)
        out = self.tmp / "export.json"
        core.export_public_keys(out, again.devices)
        self.assertEqual(core.load_keys_file(out)[0].uid, d.info.uid)
        text = out.read_text()
        self.assertNotIn("secret", text.lower())

    def test_memory_store_never_writes(self):
        s = TrustStore.memory()
        t, d = self.sim_device()
        s.pin(d.info.uid, d.keys())
        self.assertFalse(core.trusted_keys_path().exists())


class CaptureTransferTests(unittest.TestCase):
    def test_read_exact_gives_up_when_the_stream_goes_quiet(self):
        t = Scripted({})
        link = Link(t)
        t._out += b"\x01\x02"                                       # two bytes, then silence
        start = time.monotonic()
        with self.assertRaises(LinkError) as e:
            link.read_exact(10, timeout=60, idle=0.5)
        self.assertLess(time.monotonic() - start, 5)                # not the 60 s overall timeout
        self.assertIn("2 of 10", e.exception.message)

    def test_read_exact_returns_a_complete_payload(self):
        t = Scripted({})
        link = Link(t)
        t._out += bytes(range(8))
        self.assertEqual(link.read_exact(8, timeout=5, idle=1), bytes(range(8)))

    def test_capture_pieces(self):
        sys.path.insert(0, str(ROOT / "host"))
        import zirah as cli
        self.assertEqual(cli.capture_pieces(10_000), [10_000])
        self.assertEqual(cli.capture_pieces(1_000_000), [50_000] * 20)
        self.assertEqual(cli.capture_pieces(120_001), [50_000, 50_000, 20_001])
        self.assertEqual(sum(cli.capture_pieces(20_000_000)), 20_000_000)


if __name__ == "__main__":
    unittest.main()
