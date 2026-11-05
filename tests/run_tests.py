#!/usr/bin/env python3
"""Run every Zirah test and print one line per suite.

    python tests/run_tests.py            (zirah-tests.bat on Windows)
    python tests/run_tests.py --keep     keep build outputs in tests/out

Suites:
  oled       firmware OLED code built for the PC; every screen rendered, any
             clipped text fails (needs gcc or clang)
  mldsa      firmware ML-DSA-65 code against the reference implementation,
             with AddressSanitizer and UBSan where the compiler has them
  entropy    the entropy source's health tests, statistics, A-B correlation
             and second-source detection, driven with synthetic noise
  timing     dudect-style test: the secret-handling kernels show no timing
             difference between a fixed and a random secret on this PC, while
             a deliberately leaky compare is caught (needs gcc or clang on x86)
  kat        boot self-test vectors recomputed with dilithium-py
  style      Title Case rule for the OLED and desktop text
  host       host core: protocol, trust, signing and verification rules
  app        the desktop app driven through sign and verify (simulation),
             started and quit like zirah-app.bat, and timed while it animates
  qemu       the firmware's Cortex-M4 assembly NTT on an emulated Cortex-M4
             (QEMU mps2-an386): equal to the C NTT modulo q for thousands of
             inputs, and the whole mldsa suite rerun with it, whose transcript
             of every key and signature must equal the PC build's (needs the
             ARM toolchain and qemu-system-arm)
  firmware   arm-none-eabi build of the firmware for both boards with zero warnings
             (needs the ARM toolchain and firmware/libopencm3)

A suite whose tools are missing is reported as SKIP with the reason, never
as a pass. Exit status is 1 if any suite fails.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware"
PY = sys.executable


def cc() -> str | None:
    for c in ("gcc", "clang", "cc"):
        if shutil.which(c):
            return c
    return None


def run(cmd, timeout=600, env=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def suite_oled(out: Path):
    c = cc()
    if not c:
        return "SKIP", "no C compiler (install MSYS2 gcc or use WSL)"
    exe = out / "oled_preview"
    rc, log = run([c, "-std=gnu11", "-O1", "-DZIRAH_NATIVE_TEST", f"-I{FW / 'src'}",
                   str(ROOT / "tests/native/oled_preview.c"), str(FW / "src/oled.c"), str(FW / "src/oled_ui.c"),
                   str(ROOT / "tests/native/i2c_stub.c"), "-lm", "-o", str(exe)])
    if rc:
        return "FAIL", "build: " + last_line(log)
    frames = out / "oled_frames"
    frames.mkdir(exist_ok=True)
    rc, log = run([str(exe), str(frames)])
    return ("PASS" if rc == 0 else "FAIL"), last_line(log)


def suite_mldsa(out: Path):
    c = cc()
    if not c:
        return "SKIP", "no C compiler (install MSYS2 gcc or use WSL)"
    m = FW / "mldsa"
    srcs = [str(ROOT / "tests/native/test_mldsa.c"), str(FW / "src/zirah_mldsa.c"), str(FW / "src/secure.c")] + \
        [str(m / f) for f in ("fips202.c", "ntt.c", "packing.c", "poly.c", "polyvec.c", "reduce.c",
                              "rounding.c", "sign.c", "symmetric-shake.c")]
    exe = out / "test_mldsa"
    base = [c, "-std=gnu11", "-O1", "-g", "-DDILITHIUM_MODE=3", "-DZIRAH_NATIVE_TEST", f"-I{FW / 'src'}",
            f"-I{m}"]
    note = "with ASan and UBSan"
    rc, log = run(base + ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"] + srcs +
                  ["-o", str(exe)])
    if rc:
        note = "without sanitizers (not available)"
        rc, log = run(base + srcs + ["-o", str(exe)])
        if rc:
            return "FAIL", "build: " + last_line(log)
    rc, log = run([str(exe)])
    global NATIVE_TRANSCRIPT
    NATIVE_TRANSCRIPT = transcript(log) if rc == 0 else None
    return ("PASS" if rc == 0 else "FAIL"), f"{last_line(log)} ({note})"


NATIVE_TRANSCRIPT = None
M4 = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"]
PQC_NO_NTT = ("fips202.c", "packing.c", "poly.c", "polyvec.c", "reduce.c", "rounding.c", "sign.c",
              "symmetric-shake.c")


def transcript(log: str) -> str | None:
    return next((ln.split()[1] for ln in log.splitlines() if ln.startswith("transcript ")), None)


def qemu_run(elf: Path, icount: bool = False):
    cmd = ["qemu-system-arm", "-M", "mps2-an386", "-nographic", "-monitor", "none",
           "-semihosting-config", "enable=on,target=native"]
    if icount:
        cmd += ["-icount", "shift=0"]
    return run(cmd + ["-kernel", str(elf)], timeout=900)


def suite_qemu(out: Path):
    gcc = "arm-none-eabi-gcc"
    if not shutil.which(gcc) or not shutil.which("arm-none-eabi-objcopy"):
        return "SKIP", "arm-none-eabi-gcc not found"
    if not shutil.which("qemu-system-arm"):
        return "SKIP", "qemu-system-arm not found (Linux or WSL2: apt install qemu-system-arm)"
    if NATIVE_TRANSCRIPT is None:
        return "SKIP", "needs a passing mldsa suite to compare against"
    m, q = FW / "mldsa", ROOT / "tests" / "qemu"
    cf = M4 + ["-O2", "-g", "-std=gnu11", "-DDILITHIUM_MODE=3", f"-I{FW / 'src'}", f"-I{m}"]
    ld = ["-T", str(q / "mps2_an386.ld"), "-nostartfiles", "--specs=rdimon.specs", "-Wl,--gc-sections",
          "-lc", "-lrdimon", "-lm"]
    asm = out / "ntt_asm.o"
    rc, log = run([gcc] + M4 + ["-g", f"-I{FW / 'mldsa-m4'}", "-c", str(FW / "mldsa-m4/ntt.S"), "-o", str(asm)])
    if rc:
        return "FAIL", "assembly: " + last_line(log)
    # The C transform a second time, renamed, to compare against inside one program.
    cref = out / "cref_ntt.o"
    rc, log = run([gcc] + cf + ["-c", str(m / "ntt.c"), "-o", str(cref)])
    if rc == 0:
        rc, log = run(["arm-none-eabi-objcopy",
                       "--redefine-sym", "pqcrystals_dilithium3_ref_ntt=cref_ntt",
                       "--redefine-sym", "pqcrystals_dilithium3_ref_invntt_tomont=cref_invntt_tomont",
                       str(cref)])
    if rc:
        return "FAIL", "reference NTT: " + last_line(log)
    check = out / "ntt_check.elf"
    rc, log = run([gcc] + cf + [str(q / "ntt_check.c"), str(m / "reduce.c"), str(q / "startup.c"), str(cref),
                                str(asm)] + ld + ["-o", str(check)])
    if rc:
        return "FAIL", "ntt_check build: " + last_line(log)
    rc, log = qemu_run(check, icount=True)
    if rc:
        return "FAIL", "ntt_check: " + last_line(log)
    counts = next((ln for ln in log.splitlines() if ln.startswith("instructions per transform")), "")
    ntt_line = next((ln for ln in log.splitlines() if ln.startswith("ntt:")), last_line(log))
    full = out / "test_mldsa_m4.elf"
    rc, log = run([gcc] + cf + ["-DZIRAH_NATIVE_TEST", str(ROOT / "tests/native/test_mldsa.c"),
                                str(FW / "src/zirah_mldsa.c"), str(FW / "src/secure.c"),
                                str(FW / "mldsa-m4/ntt_m4.c"), str(asm), str(q / "startup.c")] +
                  [str(m / f) for f in PQC_NO_NTT] + ld + ["-o", str(full)])
    if rc:
        return "FAIL", "test_mldsa build: " + last_line(log)
    rc, log = qemu_run(full)
    t = transcript(log)
    if rc or t != NATIVE_TRANSCRIPT:
        why = last_line(log) if rc else f"transcript {t} differs from the PC build's {NATIVE_TRANSCRIPT}"
        return "FAIL", "test_mldsa on the Cortex-M4: " + why
    return "PASS", (f"{ntt_line.split(';')[0][5:]}; the mldsa suite on the Cortex-M4 with the assembly NTT: "
                    f"{last_line(log).split(': ', 1)[1]}, transcript equal to the PC build; {counts}")


def suite_entropy(out: Path):
    c = cc()
    if not c:
        return "SKIP", "no C compiler (install MSYS2 gcc or use WSL)"
    exe = out / "test_trng"
    base = [c, "-std=gnu11", "-O1", "-g", f"-I{FW / 'src'}", str(ROOT / "tests/native/test_trng.c"),
            str(FW / "src/trng_logic.c"), "-lm", "-o", str(exe)]
    note = "with ASan and UBSan"
    rc, log = run(base[:1] + ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"] + base[1:])
    if rc:
        note = "without sanitizers (not available)"
        rc, log = run(base)
        if rc:
            return "FAIL", "build: " + last_line(log)
    rc, log = run([str(exe)])
    return ("PASS" if rc == 0 else "FAIL"), f"{last_line(log)} ({note})"


def suite_timing(out: Path):
    c = cc()
    if not c:
        return "SKIP", "no C compiler (install MSYS2 gcc or use WSL)"
    m = FW / "mldsa"
    exe = out / "dudect"
    rc, log = run([c, "-std=gnu11", "-O2", "-DDILITHIUM_MODE=3", f"-I{FW / 'src'}", f"-I{m}",
                   str(ROOT / "tests/native/dudect.c"), str(FW / "src/zirah_mldsa.c"), str(FW / "src/secure.c")] +
                  [str(m / f) for f in ("ntt.c",) + PQC_NO_NTT] + ["-lm", "-o", str(exe)])
    if rc:
        return "FAIL", "build: " + last_line(log)
    rc, log = run([str(exe)], timeout=600)
    if rc == 77:
        return "SKIP", last_line(log)
    return ("PASS" if rc == 0 else "FAIL"), last_line(log)


def suite_kat(out: Path):
    try:
        import dilithium_py  # noqa: F401
    except ImportError:
        return "SKIP", "dilithium-py not installed (run setup.bat)"
    rc, log = run([PY, str(ROOT / "tests/native/kat_reference.py")])
    return ("PASS" if rc == 0 else "FAIL"), "4 vectors match" if rc == 0 else last_line(log)


def suite_style(out: Path):
    rc, log = run([PY, str(ROOT / "tests/style/title_case.py")])
    return ("PASS" if rc == 0 else "FAIL"), last_line(log)


def suite_host(out: Path):
    rc, log = run([PY, "-m", "unittest", "discover", "-s", str(ROOT / "tests/host")], timeout=900)
    summary = next((ln for ln in reversed(log.splitlines()) if ln.startswith("Ran ")), last_line(log))
    return ("PASS" if rc == 0 else "FAIL"), summary + ("" if rc == 0 else ", " + last_line(log))


def suite_app(out: Path):
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return "SKIP", "PySide6 not installed (run setup.bat)"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_LOGGING_RULES="qt.qpa.*=false")
    worst, lines = 0, []
    # The whole sign and verify flow, then starting and quitting the program
    # exactly as zirah-app.bat does, then the signal window's drawing shortcut
    # and the drawing-speed budget.
    for script, args in (("render_screens.py", [str(out / "screens")]), ("lifecycle.py", []),
                         ("scope_check.py", []), ("perf.py", [])):
        rc, log = run([PY, str(ROOT / "tests/ui" / script)] + args, timeout=600, env=env)
        worst = worst or rc
        picked = [ln for ln in log.splitlines()
                  if ln.startswith(("FAIL", "screens written", "lifecycle:", "scope:", "perf:"))]
        lines += picked or [f"{script}: " + last_line(log)]
    summary = "; ".join(lines).replace(str(out / "screens"), "the test folder")
    return ("PASS" if worst == 0 else "FAIL"), summary


def suite_firmware(out: Path):
    if not shutil.which("arm-none-eabi-gcc") or not shutil.which("make"):
        return "SKIP", "arm-none-eabi-gcc or make not found (firmware/zirah.bin is prebuilt)"
    if not (FW / "libopencm3" / "lib" / "libopencm3_stm32f4.a").exists():
        return "SKIP", "firmware/libopencm3 not built (bash firmware/get_libopencm3.sh)"
    # Both boards: the Discovery (zirah.bin) and the DevEBox (zirah-devebox.bin).
    boards = (("discovery", "zirah.bin", 0x080E0000), ("devebox", "zirah-devebox.bin", 0x08060000))
    warnings, nobj = 0, 0
    for board, name, key_addr in boards:
        build = out / f"fwbuild-{board}"
        rc, log = run(["make", "-C", str(FW), f"BOARD={board}", f"BUILD={build}", "-j4"], timeout=900)
        warnings += sum(1 for ln in log.splitlines() if "warning:" in ln)
        if rc:
            return "FAIL", f"{board}: " + last_line(log)
        # The Cortex-M4 divider takes a data-dependent number of cycles, so no
        # code that handles key material may contain a division instruction.
        objs = [build / "src/zirah_mldsa.o", build / "src/secure.o"] + sorted((build / "mldsa").glob("*.o")) + \
            sorted((build / "mldsa-m4").glob("*.o"))
        nobj = len(objs)
        dividing = []
        for o in objs:
            rc, dis = run(["arm-none-eabi-objdump", "-d", str(o)])
            if rc:
                return "FAIL", f"objdump {o.name}: " + last_line(dis)
            if any(ln.split("\t")[2:3] and ln.split("\t")[2].strip().split(".")[0] in ("udiv", "sdiv")
                   for ln in dis.splitlines()):
                dividing.append(o.name)
        if dividing:
            return "FAIL", f"{board}: division instructions in key-handling code: " + ", ".join(dividing)
        # FW_HASH covers flash from 0x08000000 to the end of the load copy of
        # .data, which has to be exactly the image the PC flashes, and the
        # image has to end below the key sector.
        rc, nm = run(["arm-none-eabi-nm", str(build / "zirah.elf")])
        syms = {ln.split()[-1]: int(ln.split()[0], 16) for ln in nm.splitlines() if len(ln.split()) == 3}
        if rc or not {"_data_loadaddr", "_data", "_edata"} <= syms.keys():
            return "FAIL", f"{board}: could not read the image symbols from zirah.elf"
        extent = syms["_data_loadaddr"] - 0x08000000 + syms["_edata"] - syms["_data"]
        built = (build / "zirah.bin").read_bytes()
        if extent != len(built):
            return "FAIL", f"{board}: FW_HASH would cover {extent} bytes but the image has {len(built)}"
        if 0x08000000 + extent > key_addr:
            return "FAIL", f"{board}: the image reaches the key sector at 0x{key_addr:08X}"
        # The shipped image must be this source: a stale file would make
        # every board report a build the PC does not have.
        shipped = FW / name
        if not shipped.exists() or shipped.read_bytes() != built:
            return "FAIL", f"firmware/{name} differs from a fresh build (run make BOARD={board} release in firmware)"
        sha = FW / (name + ".sha256")
        listed = sha.read_text().split()[0] if sha.exists() else ""
        if listed != hashlib.sha256(built).hexdigest():
            return "FAIL", f"firmware/{name}.sha256 does not match {name}"
    return ("PASS" if warnings == 0 else "FAIL"), \
        f"both boards built, {warnings} warnings, no division instruction in the {nobj} signing objects, " \
        "each below its key sector and identical to the shipped zirah.bin and zirah-devebox.bin"


SUITES = [("oled", suite_oled), ("mldsa", suite_mldsa), ("entropy", suite_entropy), ("timing", suite_timing),
          ("kat", suite_kat),
          ("style", suite_style), ("host", suite_host), ("app", suite_app), ("qemu", suite_qemu),
          ("firmware", suite_firmware)]


def main() -> int:
    keep = "--keep" in sys.argv
    out = ROOT / "tests" / "out" if keep else Path(tempfile.mkdtemp(prefix="zirah-tests-"))
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    print(f"Zirah tests ({sys.platform}, Python {sys.version.split()[0]})\n")
    for name, fn in SUITES:
        t0 = time.monotonic()
        try:
            status, detail = fn(out)
        except subprocess.TimeoutExpired:
            status, detail = "FAIL", "timed out"
        except Exception as e:  # a crashed runner is a failure, not a skip
            status, detail = "FAIL", repr(e)
        failed += status == "FAIL"
        print(f"  {status:4}  {name:9} {detail}  [{time.monotonic() - t0:.1f} s]", flush=True)
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    print(f"\n{'All suites passed or were skipped.' if not failed else f'{failed} suite(s) failed.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
