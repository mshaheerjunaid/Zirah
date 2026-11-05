"""Tests of host/zirah_validate.py: parsing the firmware's ANALYZE reply,
refusing the simulator, the written report and the no-overwrite rule."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))

import zirah_core as core  # noqa: E402
import zirah_validate as zv  # noqa: E402
from zirah_sim import SimTransport  # noqa: E402

ANALYZE = ["N 4096 RATE 20000 SOURCE A", "MIN 1702 MAX 2391 MEAN 2047 STD 118.4",
           "CLIPPED 0 DISTINCT12 512 DISTINCT8 248", "MCV_H 6.81 bits/symbol (quick estimate only)",
           "AUTOCORR1 -0.031", "HIST 1702    12 #", "OTHER B MIN 1650 MAX 2402 MEAN 2050 STD 121.0",
           "CORR_AB -0.014 (limit 0.300)"]


def sim_device():
    dev = core.Device(core.Link(SimTransport()))
    dev.handshake()
    return dev


class ValidateTests(unittest.TestCase):
    def test_analyze_reply_is_parsed_as_the_firmware_prints_it(self):
        s = zv.parse_analyze(core.Reply(True, ANALYZE))
        self.assertEqual((s["n"], s["min"], s["max"], s["mean"], s["std"]), (4096, 1702, 2391, 2047, 118.4))
        self.assertEqual((s["clipped"], s["distinct8"], s["mcv_h"]), (0, 248, 6.81))
        self.assertEqual((s["autocorr1"], s["corr_ab"]), (-0.031, -0.014))

    def test_simulator_is_refused_unless_a_test_asks(self):
        with self.assertRaises(core.ZirahError) as cm:
            zv.run_validation(sim_device(), core.TrustStore.memory(), progress=lambda m: None)
        self.assertEqual(cm.exception.code, "SIMULATED")

    def test_simulated_report_says_so_and_never_overwrites(self):
        rec = zv.run_validation(sim_device(), core.TrustStore.memory(), bench_count=1, corr_runs=1,
                                progress=lambda m: None, allow_simulated=True)
        self.assertTrue(rec["simulated"])
        self.assertIn("error", rec["bench"])            # the simulator times nothing
        with tempfile.TemporaryDirectory() as d:
            md, js = zv.save(rec, Path(d))
            self.assertIn("SIMULATED", md.name)
            self.assertIn("No number below comes from Zirah hardware", md.read_text(encoding="utf-8"))
            with self.assertRaises(core.ZirahError):
                core.write_new_file(md, b"x")
            self.assertNotEqual(md.read_bytes(), b"x")

    def test_report_from_hardware_numbers(self):
        rec = {"simulated": False, "started_utc": "t0", "finished_utc": "t1", "host_version": "1.0.0",
               "device": {"uid": "A" * 24, "firmware": "1.0.0", "protocol": 1, "fw_hash": "AB" * 16,
                          "pc_zirah_bin_hash": "AB" * 16, "rdp": 0, "mpu": True, "canary": True,
                          "stack_bytes": 1, "atecc": "ready", "stm32_rng": "ok", "oled": True, "key": "present",
                          "pq_fp": "11223344", "ec_fp": "55667788", "h_assumed": "1.00 bits/symbol",
                          "vdd_mv": 2950, "temp_c10_start": 312, "temp_c10_end": 318, "source_b": "present"},
               "readiness": {"state": "DEGRADED", "checks": [{"key": "rdp", "ok": False, "required": False,
                                                              "title": "Readout Protection On", "detail": "Level 0"}]},
               "health": {"ok": True, "trng": "OK", "health": {"symbols": "2048"}, "vbias_mv": 12040,
                          "source_b": "present", "error": ""},
               "sources": {"a": zv.parse_analyze(core.Reply(True, ANALYZE))},
               "correlation": {"runs": 2, "values": [0.01, -0.02], "mean": -0.005, "largest_abs": 0.02,
                               "startup_test_x1000": 12, "fault_limit": 0.3},
               "identity": {"ok": True, "detail": "Proof Verified on This PC"},
               "bench": {"build": {"ntt": "asm", "cpu_mhz": "168", "n": "20", "sources": "2",
                                   "ecc_rng": "1", "stm32_rng": "1"},
                         "stages": {k: {"min": 1000, "median": 2000, "max": 3000, "mean": 2100, "n": 20}
                                    for k in core.BENCH_STAGES},
                         "values": {"keygen_us": {"value": 45000}, "ntt_cycles": {"signer": 8000, "c": 29000}}}}
        md = zv.render_markdown(rec)
        for text in ("Every number below was reported by the board", "| Same build as this PC's zirah.bin | Yes |",
                     "31.2 °C at the start", "largest magnitude 0.020", "| A | 2047 | 118.4 |",
                     "| Masked signature (ms) | 1.0 | 2.0 | 3.0 | 2.1 |", "8000 cycles against 29000"):
            self.assertIn(text, md)
        self.assertNotIn("SIMULATED", md)


if __name__ == "__main__":
    unittest.main()
