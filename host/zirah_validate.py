"""One run of every hardware check, written up as a results file.

`zirah validate` drives this against the real board: the readiness checks,
a fresh start-up health test, the noise statistics of each source, repeated
A-B correlation measurements when the second source is fitted, an identity
proof, and the benchmark. Every number in the report comes from the device
in that run; nothing is estimated. The report is written as Markdown for a
write-up and as JSON for later comparison, and never over an existing file.

The simulator is refused unless a test asks for it explicitly, and then the
report says SIMULATED on every page.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import zirah_core as core

_NUM = r"-?\d+(?:\.\d+)?"


def parse_analyze(r: core.Reply) -> Dict[str, Optional[float]]:
    """The numbers of an ANALYZE reply; None for anything the reply lacks."""
    def find(key: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, f"{key} " + (r.get(key) or "")) if r.get(key) is not None else None
        return float(m.group(1)) if m else None
    return {
        "n": find("N", rf"^N ({_NUM})"),
        "min": find("MIN", rf"MIN ({_NUM})"),
        "max": find("MIN", rf"MAX ({_NUM})"),
        "mean": find("MIN", rf"MEAN ({_NUM})"),
        "std": find("MIN", rf"STD ({_NUM})"),
        "clipped": find("CLIPPED", rf"CLIPPED ({_NUM})"),
        "distinct8": find("CLIPPED", rf"DISTINCT8 ({_NUM})"),
        "mcv_h": find("MCV_H", rf"MCV_H ({_NUM})"),
        "autocorr1": find("AUTOCORR1", rf"AUTOCORR1 ({_NUM})"),
        "corr_ab": find("CORR_AB", rf"CORR_AB ({_NUM})"),
    }


def run_validation(dev: core.Device, trust: Optional[core.TrustStore], bench_count: int = 20,
                   corr_runs: int = 10, progress: Callable[[str], None] = print,
                   allow_simulated: bool = False) -> dict:
    if not 0 <= bench_count <= 100:
        raise ValueError("bench_count must be 0..100")
    if not 0 <= corr_runs <= 100:
        raise ValueError("corr_runs must be 0..100")
    started = core.now_utc()
    info = dev.refresh_info()
    if info.simulated and not allow_simulated:
        raise core.ZirahError("SIMULATED", "Validation needs the real board",
                              "Simulated numbers say nothing about the hardware")
    rec: dict = {"tool": "zirah validate", "host_version": core.HOST_VERSION, "started_utc": started,
                 "simulated": info.simulated, "device": {}, "readiness": {}, "health": {},
                 "sources": {}, "correlation": None, "identity": None, "bench": None, "notes": []}

    progress("Reading the device status")
    keys = dev.keys() if info.key == "present" else None
    t = trust.evaluate(info.uid, keys) if (trust is not None and keys is not None) else None
    state, checks = core.readiness(info, t)
    rec["device"] = {"uid": info.uid, "firmware": info.firmware, "protocol": info.protocol,
                     "fw_hash": info.fw_hash, "pc_zirah_bin_hash": core.local_firmware_hash(board=info.board),
                     "board": info.board,
                     "rdp": info.rdp, "mpu": info.mpu, "canary": info.canary, "stack_bytes": info.stack_bytes,
                     "atecc": info.atecc, "stm32_rng": info.stm32_rng,
                     "jitter": info.jitter, "accel": info.accel, "accel_chip": info.accel_chip, "oled": info.oled,
                     "key": info.key, "pq_fp": keys.mldsa_fp if keys else "",
                     "ec_fp": keys.ecc_fp if keys else "", "h_assumed": info.h_assumed,
                     "vdd_mv": info.vdd_mv, "temp_c10_start": info.temp_c10}
    rec["readiness"] = {"state": state, "checks": [{"key": c.key, "ok": c.ok, "required": c.required,
                                                     "title": c.title, "detail": c.detail} for c in checks]}

    progress("Running a fresh start-up health test")
    h = dev.health()
    rec["health"] = {"ok": h.ok, "trng": (h.get("TRNG") or "").strip(),
                     "health": core.kv_fields(h.get("HEALTH") or ""),
                     "vbias_mv": core._int(h.get("VBIAS_MV")),
                     "source_b": (h.get("SOURCE_B") or "").strip(),
                     "error": "" if h.ok else f"{h.err_code} {h.err_text}".strip()}
    info = dev.refresh_info()
    rec["device"]["source_b"] = info.source_b or "not reported"

    progress("Measuring source A")
    rec["sources"]["a"] = parse_analyze(dev.analyze(4096, "a").raise_for_error())
    if info.source_b == "present":
        progress("Measuring source B")
        rec["sources"]["b"] = parse_analyze(dev.analyze(4096, "b").raise_for_error())
        values: List[float] = []
        for i in range(corr_runs):
            progress(f"A-B correlation, run {i + 1} of {corr_runs}")
            v = parse_analyze(dev.analyze(4096, "a").raise_for_error())["corr_ab"]
            if v is not None:
                values.append(v)
        if values:
            rec["correlation"] = {"runs": len(values), "values": values,
                                  "mean": statistics.fmean(values),
                                  "largest_abs": max(abs(v) for v in values),
                                  "startup_test_x1000": info.corr_x1000, "fault_limit": 0.300}

    if keys is not None:
        progress("Identity proof")
        ok, msg = core.check_identity(dev, keys)
        rec["identity"] = {"ok": ok, "detail": msg}

    if bench_count:
        progress(f"Benchmark, {bench_count} signatures with a throwaway key")
        try:
            def on_line(ln: str) -> None:
                if ln.startswith("EVT BENCH"):
                    f = core.kv_fields(ln[len("EVT BENCH"):])
                    progress(f"  {f.get('done', '?')} of {f.get('of', '?')}")
            rec["bench"] = dev.bench(bench_count, on_line=on_line)
        except core.ZirahError as e:
            rec["bench"] = {"error": f"{e.code} {e.message}"}

    end = dev.refresh_info()
    rec["device"]["temp_c10_end"] = end.temp_c10
    rec["device"]["trng_end"] = end.trng
    rec["finished_utc"] = core.now_utc()
    return rec


def _fmt(v, digits=1) -> str:
    if v is None:
        return "Not Reported"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.{digits}f}"
    return f"{int(v)}" if isinstance(v, (int, float)) else str(v)


def _temp(c10) -> str:
    return "not reported" if c10 is None else f"{c10 / 10:.1f} °C"


def render_markdown(rec: dict) -> str:
    d = rec["device"]
    sim = rec["simulated"]
    out = ["# Zirah Hardware Validation", ""]
    if sim:
        out += ["**SIMULATED RUN. No number below comes from Zirah hardware.**", ""]
    else:
        out += ["Every number below was reported by the board during this run of `zirah validate`. "
                "Nothing was estimated or copied from documentation.", ""]
    out += [f"Run from {rec['started_utc']} to {rec.get('finished_utc', '')} (UTC), host software "
            f"{rec['host_version']}.", "", "## Device", "",
            "| Item | Value |", "|---|---|",
            f"| Device ID | {d['uid'] or 'Not Reported'} |",
            f"| Firmware | {d['firmware']}, protocol {d['protocol']} |",
            f"| Firmware hash | {d['fw_hash'] or 'Not Reported'} |",
            f"| Board | {'DevEBox STM32F4XX_M (STM32F407VE)' if d.get('board') == 'devebox' else 'STM32F4DISCOVERY (STM32F407VG)'} |",
            f"| Same build as this PC's {core.BOARDS.get(d.get('board', 'discovery'), 'zirah.bin')} | "
            f"{'Yes' if d['fw_hash'] and d['fw_hash'] == d['pc_zirah_bin_hash'] else 'No' if d['pc_zirah_bin_hash'] else 'No firmware file on this PC'} |",
            f"| Readout protection | level {d['rdp'] if d['rdp'] >= 0 else 'not reported'} |",
            f"| MPU and stack canary | {'on' if d['mpu'] else 'off'}, {'on' if d['canary'] else 'off'} |",
            f"| ATECC608 | {d['atecc']} |", f"| STM32 generator | {d['stm32_rng'] or 'not reported'} |",
            f"| LSI clock jitter | {d.get('jitter') or 'not reported'} |",
            f"| Board accelerometer | {d.get('accel') or 'not reported'}"
            f"{' (' + d['accel_chip'] + ')' if d.get('accel_chip') not in (None, '', 'none') else ''} |",
            f"| Second noise source | {d.get('source_b', '')} |",
            f"| Key fingerprints | PQ {d['pq_fp'] or 'none'}, EC {d['ec_fp'] or 'none'} |",
            f"| Assumed entropy | {d['h_assumed']} |",
            f"| Die temperature | {_temp(d['temp_c10_start'])} at the start, {_temp(d.get('temp_c10_end'))} at the end |",
            "", "## Readiness Checks", "", f"Overall state: **{rec['readiness']['state']}**", "",
            "| Check | Result | Detail |", "|---|---|---|"]
    for c in rec["readiness"]["checks"]:
        res = "pass" if c["ok"] else ("FAIL" if c["required"] else "warning")
        out.append(f"| {c['title']} | {res} | {c['detail']} |")
    h = rec["health"]
    out += ["", "## Start-Up Health Test", "",
            f"Result: **{h['trng'] or 'no result'}**" + (f" ({h['error']})" if h["error"] else "") + ". "
            f"Boost voltage {_fmt(h['vbias_mv'] / 1000, 2)} V.", ""]
    if h["health"]:
        out += ["Counters: " + ", ".join(f"{k} {v}" for k, v in h["health"].items()) + ".", ""]
    out += ["## Noise Sources", "", "4096 samples per source at the default rate.", "",
            "| Source | Mean | Std Dev (LSB) | Min | Max | Clipped | Distinct 8-Bit | MCV Estimate (bits/symbol) | Lag-1 Autocorrelation |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name, s in rec["sources"].items():
        out.append(f"| {name.upper()} | {_fmt(s['mean'])} | {_fmt(s['std'])} | {_fmt(s['min'])} | {_fmt(s['max'])} | "
                   f"{_fmt(s['clipped'])} | {_fmt(s['distinct8'])} | {_fmt(s['mcv_h'], 2)} | {_fmt(s['autocorr1'], 3)} |")
    out += ["", "The MCV figure is the firmware's quick most-common-value estimate over one block, not an "
            "SP 800-90B assessment. That needs a capture of at least 1,000,000 samples (`zirah capture`) "
            "run through NIST's tool, as described in `data/entropy/README.md`.", ""]
    c = rec["correlation"]
    if c:
        out += ["## A-B Correlation", "",
                f"{c['runs']} measurements of 4096 sample pairs: mean {c['mean']:+.3f}, largest magnitude "
                f"{c['largest_abs']:.3f}. The last start-up test measured "
                f"{_fmt(c['startup_test_x1000'] / 1000 if c['startup_test_x1000'] is not None else None, 3)}. "
                f"The firmware faults above {c['fault_limit']:.3f}.", "",
                "Values: " + ", ".join(f"{v:+.3f}" for v in c["values"]) + ".", ""]
    elif d.get("source_b") == "present":
        out += ["## A-B Correlation", "", "The device did not report a correlation value.", ""]
    else:
        out += ["## A-B Correlation", "", "Not measured: no second noise source was found at power-up.", ""]
    if rec["identity"]:
        out += ["## Identity Proof", "", f"{'Passed' if rec['identity']['ok'] else 'FAILED'}: "
                f"{rec['identity']['detail']}.", ""]
    b = rec["bench"]
    out += ["## Benchmark", ""]
    if not b:
        out += ["Not run.", ""]
    elif "error" in b:
        out += [f"Did not complete: {b['error']}.", ""]
    else:
        bd = b.get("build", {})
        out += [f"Throwaway key, never the device key. NTT {bd.get('ntt', '?')}, {bd.get('cpu_mhz', '?')} MHz, "
                f"{bd.get('n', '?')} signatures, {bd.get('sources', '?')} noise source(s), ATECC608 random "
                f"{'mixed' if bd.get('ecc_rng') == '1' else 'not mixed'}, STM32 random "
                f"{'mixed' if bd.get('stm32_rng') == '1' else 'not mixed'}, clock jitter "
                f"{'mixed' if bd.get('jitter') == '1' else 'not mixed'}, accelerometer "
                f"{'mixed' if bd.get('accel') == '1' else 'not mixed'}.", "",
                "| Stage | Min | Median | Max | Mean |", "|---|---|---|---|---|"]
        names = {"trng32_us": "Entropy for 32 bytes (ms)", "refresh_us": "Mask refresh (ms)",
                 "sign_us": "Masked signature (ms)", "verify_us": "On-device verification (ms)",
                 "attempts": "Rejection attempts"}
        for key in core.BENCH_STAGES:
            st = b["stages"][key]
            div = 1 if key == "attempts" else 1000
            cells = []
            for k in ("min", "median", "max", "mean"):
                x = st.get(k)
                cells.append("Not Reported" if x is None else f"{x}" if div == 1 else f"{x / div:.1f}")
            out.append(f"| {names[key]} | " + " | ".join(cells) + " |")
        v = b.get("values", {})
        extra = []
        if v.get("keygen_us", {}).get("value"):
            extra.append(f"key generation {v['keygen_us']['value'] / 1000:.1f} ms")
        for key, label in (("ntt_cycles", "forward NTT"), ("invntt_cycles", "inverse NTT")):
            if v.get(key, {}).get("signer") and v.get(key, {}).get("c"):
                extra.append(f"{label} {v[key]['signer']} cycles against {v[key]['c']} for the C code")
        if v.get("shake256_1kb_cycles", {}).get("value"):
            extra.append(f"SHAKE256 of 1 KB {v['shake256_1kb_cycles']['value']} cycles")
        if extra:
            out += ["", "Also measured: " + ", ".join(extra) + ".", ""]
    return "\n".join(out).rstrip() + "\n"


def save(rec: dict, folder: Path) -> List[Path]:
    """Writes validation-<time>.md and .json into folder; never overwrites."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = "validation-" + time.strftime("%Y%m%d-%H%M%S") + ("-SIMULATED" if rec["simulated"] else "")
    md, js = folder / f"{stem}.md", folder / f"{stem}.json"
    core.write_new_file(js, (json.dumps(rec, indent=2) + "\n").encode("utf-8"))
    core.write_new_file(md, render_markdown(rec).encode("utf-8"))
    return [md, js]
