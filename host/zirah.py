#!/usr/bin/env python3
"""Zirah command line tool (Windows, macOS, Linux).

Talks to the Zirah board over its USB serial port (micro-USB on the
STM32F4DISCOVERY's USB OTG connector) or a USB-TTL adapter on PA2/PA3.
zirah.bat in the Zirah folder runs this file for you:

    zirah ports                 list serial ports
    zirah status                readiness checks
    zirah trust                 trust this device's keys (compare with the OLED)
    zirah sign  C:\\docs\\contract.pdf
    zirah verify C:\\docs\\contract.pdf
    zirah bench                 time the signing path on the board
    zirah term                  interactive terminal

Global options: --port COM5 picks the port, --sim uses the simulated device
(no hardware; everything it produces is marked as simulated).

All rules (pinned context, trusted keys, verify-before-write, no overwrite)
live in zirah_core.py and are shared with the desktop app.
"""
import argparse
import datetime
import json
import re
import sys
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zirah_core as core  # noqa: E402
from zirah_core import (HOST_VERSION, MLDSA_PK_BYTES, Cancelled, Device, Link, LinkError,  # noqa: E402
                        TrustStore, ZirahError, check_identity, clean_text, data_dir,
                        embedded_signature, export_public_keys, find_signature_for, fingerprint,
                        hash_file, list_ports, load_keys_file, readiness, safe_file_path,
                        sign_file, signing_container, trusted_keys_path, verify_file, write_new_file)


def fail(msg: str, detail: str = "", code: int = 1):
    print(f"Error: {clean_text(msg)}", file=sys.stderr)
    if detail:
        print(f"  {clean_text(detail)}", file=sys.stderr)
    sys.exit(code)


_SIM = None


def connect(args) -> Device:
    global _SIM
    log = (lambda d, t: print(f"{d} {t}")) if args.verbose else None
    try:
        if args.sim:
            from zirah_sim import SimTransport
            _SIM = SimTransport()
            dev = Device(Link(_SIM, log=log))
            dev.handshake()
            print("SIMULATED DEVICE: nothing below comes from Zirah hardware.\n")
            return dev
        return Device.open(args.port, log=log)
    except ZirahError as e:
        fail(e.message, e.detail)


def trust_store(args) -> TrustStore:
    if args.sim:
        return TrustStore.memory()
    try:
        return TrustStore()
    except ZirahError as e:
        fail(e.message, e.detail)


def show_reply(r) -> None:
    for ln in r.lines:
        print(clean_text(ln, 4000))
    if not r.ok:
        fail(f"{r.err_code} {r.err_text}")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_ports(args):
    ports = list_ports()
    if not ports:
        print("No serial ports found. Plug the micro-USB cable into the board's USB OTG "
              "connector (CN5), or your USB-TTL adapter.")
    tags = {"zirah": "<- Zirah USB serial", "usb-ttl": "(USB-TTL adapter)",
            "st-link": "(ST-LINK debugger, not the Zirah data port)", "other": ""}
    for p in ports:
        print(f"{p.device:12} {p.description}  {tags[p.kind]}")


def cmd_info(args):
    dev = connect(args)
    show_reply(dev.link.command("INFO", timeout=5))
    dev.close()


def print_checks(state, checks):
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        print(f"  [{mark}] {c.title:34} {clean_text(c.detail, 90)}")
    print(f"\nState: {state}")


def cmd_status(args):
    dev = connect(args)
    try:
        info = dev.refresh_info()
        trust = None
        if info.key == "present":
            trust = trust_store(args).evaluate(info.uid, dev.keys())
        state, checks = readiness(info, trust)
        print(f"Zirah {info.firmware} (protocol {info.protocol}) on {dev.link.t.name}, "
              f"device {info.uid or 'unknown'}")
        print_checks(state, checks)
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()
    sys.exit(1 if state == "ERROR" else 0)


def cmd_pk(args):
    dev = connect(args)
    try:
        keys = dev.keys()
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()
    print(f"ML-DSA-65 public key  {len(keys.mldsa_pk)} bytes, fingerprint {keys.mldsa_fp}  (OLED line PQ)")
    if keys.ecc_pk:
        print(f"ECDSA P-256 public key 64 bytes, fingerprint {keys.ecc_fp}  (OLED line EC)")
    else:
        print("ECDSA P-256: not available (ATECC608 not provisioned)")


def cmd_trust(args):
    dev = connect(args)
    store = trust_store(args)
    try:
        info = dev.refresh_info()
        keys = dev.keys()
        state = store.evaluate(info.uid, keys)
        print(f"Device {info.uid}")
        if state.status == "trusted":
            print(f"  PQ fingerprint  {keys.mldsa_fp}")
            print(f"  EC fingerprint  {keys.ecc_fp or 'none'}")
            print("\nThese keys are already trusted on this PC.")
            return
        if state.status == "changed" and not args.replace:
            fail("This device's keys changed since you trusted it.",
                 "If you generated a new key on purpose, run: zirah trust --replace")
        # The fingerprint is not printed first: what you type must come from
        # the OLED, which is what makes this check independent of the USB link.
        typed = input("Read the PQ line on the OLED and type its eight characters: ").strip().upper()
        if typed.replace(" ", "").replace("-", "") != keys.mldsa_fp:
            fail("That does not match the key this PC received over USB. Nothing was trusted.",
                 "Check the OLED again. If it still differs, do not use this device until you know why.")
        print(f"  PQ fingerprint  {keys.mldsa_fp}  (matches the OLED)")
        print(f"  EC fingerprint  {keys.ecc_fp or 'none'}  (compare it with the EC line on the OLED)")
        entry = store.pin(info.uid, keys, label=args.label or "", replace=args.replace)
        where = "memory only (simulation)" if args.sim else str(store.path)
        print(f"Trusted. Saved to {where} at {entry.pinned_at}.")
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()


def cmd_untrust(args):
    store = trust_store(args)
    uid = args.uid.upper()
    if not store.find(uid):
        fail(f"No trusted device {uid}")
    store.forget(uid)
    print(f"Removed {uid} from the trusted keys.")


def cmd_export_keys(args):
    store = trust_store(args)
    if not store.devices:
        fail("No trusted devices yet. Run: zirah trust")
    try:
        export_public_keys(Path(args.file).resolve(), store.devices)
    except ZirahError as e:
        fail(e.message, e.detail)
    print(f"Wrote the trusted public keys (no secrets) to {args.file}.")
    print("Send this file to anyone who should verify your signatures.")


def cmd_keygen(args):
    dev = connect(args)
    try:
        if args.force:
            print(f"Replacing the key destroys the old one. Press {core.button_name(dev.info.board)} "
                  "on the board within 30 s to confirm.")
        if args.sim and args.force:
            threading.Timer(1.0, _SIM.press_button).start()
        r = dev.keygen(args.force)
        if not r.ok:
            fail(f"{r.err_code} {r.err_text}")
        pk = bytes.fromhex(r.get("PK") or "")
        if len(pk) != MLDSA_PK_BYTES:
            fail("The device returned a malformed public key")
        print(f"New ML-DSA-65 key, fingerprint {fingerprint(pk)}. Pairwise consistency test passed.")
        print("Next: run 'zirah trust' and compare the fingerprint with the OLED.")
    finally:
        dev.close()


def cmd_sign(args):
    try:
        doc = safe_file_path(Path(args.file).resolve())
    except ZirahError as e:
        fail(e.message, e.detail)
    dev = connect(args)
    store = trust_store(args)
    if args.sim:
        # The simulated device's key is pinned for this run only.
        store.pin(dev.info.uid, dev.keys(), "simulation")
    try:
        digest = hash_file(doc)
        dest = Path(args.out).resolve() if args.out else None
        where = signing_container(doc, dest, args.detached)
        print(f"File      {doc.name} ({digest.size:,} bytes)")
        print(f"SHA-256   {digest.sha256.hex()}")
        print("Output    " + ("a separate .zirah.json signature file" if where == "detached"
                              else f"a signed copy with the signature inside ({where.upper()})"))
        oled = {}

        def on_event(stage, fields):
            if stage == "MANIFEST":
                oled["digits"] = fields.get("oled", "")
            elif stage == "CONFIRM":
                button = ("K1 on the board" if dev.info.board == "devebox" else "the blue USER button")
                print(f"\nThe OLED shows {oled.get('digits', '?')}. If it matches, press {button} "
                      "(30 s). Ctrl+C cancels.")
                if args.sim:
                    threading.Timer(1.0, _SIM.press_button).start()
            elif stage == "PC_VERIFY":
                print("  checking the signature on this PC")
            else:
                print(f"  {stage.lower()}")

        cancel = threading.Event()
        try:
            path, rec = sign_file(dev, store, doc, dest, digest=digest, on_event=on_event,
                                      cancel=cancel, detached=args.detached)
        except KeyboardInterrupt:
            cancel.set()
            fail("Cancelled")
        st = rec.get("device_stats", {})
        print(f"\nSigned. ML-DSA-65 {len(bytes.fromhex(rec['mldsa65']['signature']))} bytes"
              + (", ECDSA P-256 64 bytes" if rec["ecdsa_p256"] else ", post-quantum only"))
        if st.get("mldsa_us"):
            print(f"Device timings: health test {st.get('health_us', 0) / 1000:.1f} ms, "
                  f"masks {st.get('trng_us', 0) / 1000:.1f} ms, ML-DSA {st['mldsa_us'] / 1000:.1f} ms "
                  f"({st.get('attempts', '?')} attempts), self-check {st.get('verify_us', 0) / 1000:.1f} ms, "
                  f"ECDSA {st.get('ecdsa_us', 0) / 1000:.1f} ms")
        extra = [name for key, name in (("ecc_rng", "the ATECC608's generator"),
                                        ("stm32_rng", "the STM32's generator"),
                                        ("jitter", "the LSI clock jitter"), ("accel", "the board accelerometer"))
                 if st.get(key)]
        if st.get("mldsa_us"):
            noise = "two avalanche noise sources" if st.get("sources") == 2 else "avalanche noise"
            print(f"Randomness: {noise}" + (f", with {', '.join(extra[:-1]) + ' and ' if len(extra) > 1 else ''}"
                                             f"{extra[-1]} mixed in (credited zero)" if extra else ""))
        inside = rec["manifest"]["container"] != "detached"
        print(f"{'Signed copy' if inside else 'Signature file'}: {path}")
        if inside:
            print(f"{doc.name} itself is unchanged. Send the signed copy; it opens like the original.")
        if dev.simulated:
            print("This file is SIMULATED and will never verify as a Zirah hardware signature.")
    except Cancelled as e:
        fail(e.message)
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()


def cmd_verify(args):
    try:
        doc = safe_file_path(Path(args.file).resolve())
        if args.sig:
            sig = Path(args.sig).resolve()
        elif embedded_signature(doc):
            sig = None                       # a signed copy carries its own signature
        else:
            sig = find_signature_for(doc)
            if sig is None:
                fail(f"{doc.name} carries no Zirah signature and has no signature file next to it",
                     "Pass the signature file with --sig")
        if args.trusted:
            trusted = load_keys_file(Path(args.trusted))
        elif trusted_keys_path().exists():
            trusted = load_keys_file(trusted_keys_path())
        else:
            trusted = []
        report, digest = verify_file(doc, sig, trusted)
    except ZirahError as e:
        fail(e.message, e.detail)
    t = report.times
    print(f"File       {doc.name}")
    print(f"Signature  {sig.name if sig else 'inside the file (' + report.found_in.upper() + ')'}")
    for c in report.checks:
        mark = "PASS" if c.ok else ("FAIL" if c.required else "n/a ")
        print(f"  [{mark}] {c.title:32} {clean_text(c.detail, 90)}")
    if report.sig:
        s = report.sig
        signed, mine = s.content_sha256.hex().upper(), digest.sha256.hex().upper()
        print(f"\nSigned content      {signed}")
        print(f"This file's content {mine}" + ("" if signed == mine else "   DIFFERS: the file was changed"))
        ec = fingerprint(s.ecc_pk) if s.ecc_pk else "none"
        print(f"Signer fingerprints  PQ {fingerprint(s.mldsa_pk)}   EC {ec}")
        if report.signer:
            print(f"These keys match {report.signer} in the trusted keys.")
        else:
            print("Compare these with the fingerprints the signer published (website or profile).")
        state = t.get("times_state") or ("signed" if t.get("signed_at_signed") == "yes" else "not_signed")
        vouched = {"signed": "signed, as the signing PC's clock gave it",
                   "unverified": "in the signed data, but the signatures did not verify, so it proves nothing",
                   "not_signed": "not covered by this older signature format"}[state]
        then = "signed" if state == "signed" else "in the signed data, not verified"
        print(f"\nSigned at              {t.get('signed_at') or 'unknown'} ({vouched})")
        if s.version >= 2:
            print(f"File created, signer   {t.get('created_at_signing') or 'not reported'}  ({then})")
            print(f"File modified, signer  {t.get('modified_at_signing') or 'not reported'}  ({then})")
        print(f"File created, this PC  {t.get('created_now') or 'not reported'}  (file system, not signed)")
        print(f"File modified, this PC {t.get('modified_now') or 'not reported'}  (file system, not signed)")
        print(f"Verified at            {t.get('verified_at')}")
        if t.get("changed_after_signing") == "yes":
            print("This PC's file system dates the last change after the signing time.")
    print(f"\nResult: {report.title}. {report.detail}.")
    sys.exit(0 if report.verdict in ("VALID", "VALID_PQ_ONLY") else 1)


def cmd_prove(args):
    dev = connect(args)
    try:
        keys = dev.keys()
        ok, msg = check_identity(dev, keys)
        print(f"Identity proof: {msg}")
        t = trust_store(args).evaluate(dev.info.uid, keys)
        print(f"Trust: {t.reason}")
        sys.exit(0 if ok else 1)
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()


def simple(cmd, timeout=15):
    def run(args):
        dev = connect(args)
        try:
            show_reply(dev.link.command(cmd, timeout=timeout))
            print("OK")
        except ZirahError as e:
            fail(e.message, e.detail)
        finally:
            dev.close()
    return run


def cmd_confirm(args):
    dev = connect(args)
    try:
        if args.state == "off":
            print(f"Turning confirmation off needs a press of {core.button_name(dev.info.board)} "
                  "on the board within 30 s.")
            if args.sim:
                threading.Timer(1.0, _SIM.press_button).start()
        show_reply(dev.set_confirm(args.state == "on"))
        print(f"Button confirmation {args.state} (it is on again after every power-up).")
    finally:
        dev.close()


def cmd_vbias(args):
    dev = connect(args)
    try:
        if not args.watch:
            show_reply(dev.link.command("VBIAS"))
            return
        print("Live boost voltage. Turn the MT3608 screw slowly. Press Enter to stop.")
        stop = threading.Event()
        threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
        dev.link.send("VBIAS WATCH")
        while not stop.is_set():
            try:
                ln = dev.link.readline(1.0)
            except LinkError:
                continue
            if ln.startswith("VBIAS_MV"):
                mv = int(ln.split()[1])
                print(f"\r  boost = {mv / 1000:6.2f} V   ", end="", flush=True)
        dev.link.send_raw(b"\n")  # any byte stops the watch loop
        try:
            while dev.link.readline(5) != "OK":
                pass
        except LinkError:
            pass
        print()
    finally:
        dev.close()


def cmd_analyze(args):
    dev = connect(args)
    try:
        r = dev.analyze(args.n, args.source)
        show_reply(r)
        m = re.search(r"\bSTD (\d+(?:\.\d+)?)", r.get("MIN") or "")
        if m:
            std = float(m.group(1))
            if std < 8:
                print("\nHint: std below 8 LSB means almost no noise reaches the ADC. Check the boost "
                      "voltage, the transistor orientation, the coupling capacitor and the op-amp wiring.")
            elif std > 900:
                print("\nHint: very large swing, probably clipping. Lower the gain.")
    finally:
        dev.close()


CAPTURE_PIECE = 50_000   # samples per RAW request. One long request over a USB-TTL link can
                         # lose bytes and then wait for ever; pieces are each CRC-checked.


def capture_pieces(n: int, piece: int = CAPTURE_PIECE) -> list:
    """Sizes of the RAW requests that make up an n-sample capture."""
    full, rest = divmod(n, piece)
    return [piece] * full + ([rest] if rest else [])


def cmd_capture(args):
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.samples < 1 or args.samples > 20_000_000:
        fail("--samples must be 1..20000000")
    targets = [Path(f"{out}_{part}") for part in ("raw16.bin", "sym8.bin", "bit0.bin", "meta.json")]
    taken = [t.name for t in targets if t.exists()]
    if taken:
        fail(f"{', '.join(taken)} already exist; choose another --out name or move the old capture away. "
             "Zirah never overwrites a file.")
    dev = connect(args)
    if dev.simulated:
        fail("RAW capture needs real hardware; simulated samples are not noise data.")
    try:
        info = dev.refresh_info()
        if args.source == "b" and info.source_b != "present":
            fail("No second noise source was found on PB1 when the board started.")
        n = args.samples
        pieces = capture_pieces(n)
        print(f"Capturing {n:,} raw samples of source {args.source.upper()} at {args.rate} Hz"
              + (f" in {len(pieces)} pieces of up to {CAPTURE_PIECE:,}" if len(pieces) > 1 else "")
              + f" (about {n / args.rate:.0f} s of sampling plus transfer time)...")
        data = bytearray()
        failures = 0
        t0 = time.time()
        for idx, k in enumerate(pieces, 1):
            dev.link.send(f"RAW {k} {args.rate}" + (" B" if args.source == "b" else ""))
            header = dev.link.readline(10)
            if not header.startswith("RAW BEGIN"):
                fail(header)
            try:
                chunk = dev.link.read_exact(2 * k, timeout=60 + 4 * k / 1000, idle=15)
            except LinkError as e:
                fail(f"The board stopped sending during piece {idx} of {len(pieces)}. Nothing was saved.",
                     f"{e.message}. Press RST on the board, then run the capture again.")
            tail = []
            while True:
                ln = dev.link.readline(30)
                if ln == "OK":
                    break
                if ln:
                    tail.append(ln)
            end = next((t[len("RAW END "):] for t in tail if t.startswith("RAW END")), "")
            fields = dict(p.split("=", 1) for p in end.split() if "=" in p)
            crc_piece = "%08x" % (zlib.crc32(chunk) & 0xFFFFFFFF)
            if fields.get("crc32") != crc_piece:
                fail(f"CRC mismatch in piece {idx} of {len(pieces)} (device {fields.get('crc32')}, host "
                     f"{crc_piece}): the transfer was corrupted. Nothing was saved; run the capture again.")
            hf = fields.get("health_failures", "")
            failures = failures + int(hf) if (failures is not None and hf.isdigit()) else None
            data += chunk
            if len(pieces) > 1:
                print(f"  piece {idx} of {len(pieces)}: {len(data) // 2:,} samples, CRC OK", flush=True)
        elapsed = time.time() - t0
        after = dev.refresh_info()
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()
    crc_host = "%08x" % (zlib.crc32(data) & 0xFFFFFFFF)
    try:
        write_new_file(targets[0], bytes(data))
        write_new_file(targets[1], bytes(data[0::2]))
        write_new_file(targets[2], bytes(b & 1 for b in data[0::2]))
    except ZirahError as e:
        fail(e.message, e.detail)
    meta = {"samples": n, "rate_hz": args.rate, "source": args.source.upper(), "firmware": info.firmware,
            "firmware_hash": info.fw_hash, "vbias_mv": info.vbias_mv,
            "die_temp_c_before": None if info.temp_c10 is None else info.temp_c10 / 10,
            "die_temp_c_after": None if after.temp_c10 is None else after.temp_c10 / 10,
            "second_source": info.source_b or "not reported",
            "health_failures_during_capture": -1 if failures is None else failures,
            "crc32": crc_host, "pieces": len(pieces), "piece_samples": min(n, CAPTURE_PIECE),
            "contiguity": "The firmware samples 2,048 values at a time and pauses while it sends each block, "
                          "so samples are consecutive within each 2,048-sample block.",
            "transfer_seconds": round(elapsed, 1), "notes": args.notes,
            "files": {"raw16": "uint16 little-endian, 12-bit ADC values",
                      "sym8": "8-bit symbols, low byte of each sample (ea_non_iid file 8)",
                      "bit0": "1-bit symbols, LSB of each sample (ea_non_iid file 1)"}}
    try:
        write_new_file(targets[3], json.dumps(meta, indent=2).encode("utf-8"))
    except ZirahError as e:
        fail(e.message, e.detail)
    print(f"Done in {elapsed:.0f} s. CRC OK. Health-test failures during capture: "
          f"{meta['health_failures_during_capture']}")


BENCH_ROWS = (("trng32_us", "Entropy, 32 bytes", 1000), ("refresh_us", "Mask refresh", 1000),
              ("sign_us", "Masked ML-DSA-65 signature", 1000), ("verify_us", "On-device verification", 1000),
              ("attempts", "Rejection attempts", 1))


def cmd_bench(args):
    if args.count < 1 or args.count > 100:
        fail("--count must be 1..100")
    dev = connect(args)
    if dev.simulated:
        fail("Benchmarks need the real board; the simulator does not time anything.")
    try:
        info = dev.refresh_info()
        print(f"Timing {args.count} masked signatures on the board with a throwaway key "
              "(the device key is not used)...")

        def progress(ln):
            if ln.startswith("EVT BENCH"):
                f = dict(p.split("=", 1) for p in ln.split()[2:] if "=" in p)
                print(f"  {f.get('done', '?')} of {f.get('of', '?')}", flush=True)
        res = dev.bench(args.count, on_line=progress)
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()
    b, v = res["build"], res["values"]
    print(f"\nZirah {info.firmware}, NTT {b.get('ntt', '?')}, {b.get('cpu_mhz', '?')} MHz, "
          f"{b.get('sources', '?')} noise source(s), die {info.temp_c10 / 10:.1f} °C" if info.temp_c10 is not None
          else f"\nZirah {info.firmware}, NTT {b.get('ntt', '?')}, {b.get('cpu_mhz', '?')} MHz")
    print(f"  {'Key generation (throwaway key)':30} {v.get('keygen_us', {}).get('value', 0) / 1000:9.1f} ms")
    print(f"  {'Masking the key':30} {v.get('mask_load_us', {}).get('value', 0) / 1000:9.1f} ms\n")
    print(f"  {'':30} {'Min':>9} {'Median':>9} {'Max':>9} {'Mean':>9}")
    for key, name, div in BENCH_ROWS:
        st = res["stages"][key]
        if div == 1:
            cells = [f"{st[k]:>9d}" for k in ("min", "median", "max")] + [f"{st['mean']:>9d}"]
        else:
            cells = [f"{st[k] / div:>9.1f}" for k in ("min", "median", "max", "mean")]
        print(f"  {name:30} " + " ".join(cells) + ("" if div == 1 else "  ms"))
    for key, name in (("ntt_cycles", "Forward NTT"), ("invntt_cycles", "Inverse NTT")):
        c = v.get(key, {})
        if c.get("signer") and c.get("c"):
            print(f"  {name:30} {c['signer']:>9d} cycles ({b.get('ntt', '?')}), {c['c']} in C, "
                  f"{c['c'] / c['signer']:.1f} x")
    shake = v.get("shake256_1kb_cycles", {}).get("value")
    if shake:
        print(f"  {'SHAKE256 of 1 KB':30} {shake:>9d} cycles")
    out = Path(args.out) if args.out else data_dir() / "benchmarks" / (
        f"bench-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    record = {"tool": "zirah bench", "count": args.count, "firmware": info.firmware,
              "firmware_hash": info.fw_hash, "die_temp_c": None if info.temp_c10 is None else info.temp_c10 / 10,
              "second_source": info.source_b or "not reported", "result": res}
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_new_file(out, json.dumps(record, indent=2).encode("utf-8"))
    except (OSError, ZirahError) as e:
        fail(getattr(e, "message", str(e)), getattr(e, "detail", ""))
    print(f"\nSaved to {out}")


def cmd_validate(args):
    import zirah_validate
    if not 0 <= args.bench_count <= 100:
        fail("--bench-count must be 0..100")
    if not 0 <= args.corr_runs <= 100:
        fail("--corr-runs must be 0..100")
    dev = connect(args)
    if dev.simulated:
        fail("Validation needs the real board; simulated numbers say nothing about the hardware.")
    try:
        rec = zirah_validate.run_validation(dev, trust_store(args), bench_count=args.bench_count,
                                            corr_runs=args.corr_runs, progress=lambda m: print(m, flush=True))
    except ZirahError as e:
        fail(e.message, e.detail)
    finally:
        dev.close()
    folder = Path(args.out) if args.out else data_dir() / "validation"
    try:
        paths = zirah_validate.save(rec, folder)
    except (OSError, ZirahError) as e:
        fail("Could not write the report", str(e))
    print(f"\nReadiness: {rec['readiness']['state']}, start-up test: {rec['health']['trng'] or 'no result'}")
    for name, st in rec["sources"].items():
        print(f"Source {name.upper()}: std {st['std']} LSB, clipped {st['clipped']}, MCV {st['mcv_h']} bits/symbol")
    if rec["correlation"]:
        c = rec["correlation"]
        print(f"A-B correlation over {c['runs']} runs: mean {c['mean']:+.3f}, largest {c['largest_abs']:.3f} "
              f"(fault limit {c['fault_limit']:.3f})")
    print("Report written to:")
    for p in paths:
        print(f"  {p}")


def cmd_atecc(args):
    dev = connect(args)
    try:
        if args.action != "provision":
            show_reply(dev.link.command("ATECC " + args.action.upper(), timeout=10))
            return
        show_reply(dev.link.command("ATECC INFO"))
        r = dev.link.command("ATECC PROVISION")
        show_reply(r)
        code = r.get("CONFIRM_CODE")
        print("\nThis PERMANENTLY configures and locks the ATECC608. It cannot be undone.")
        if input("Type  I UNDERSTAND  to continue: ").strip() != "I UNDERSTAND":
            fail("Cancelled. Nothing was written.")
        print(f"Now press {core.button_name(dev.info.board)} on the board.")
        r = dev.link.command(f"ATECC PROVISION {code}", timeout=60, on_line=print)
        print("Provisioned. Run 'zirah trust --replace' to trust the new ECDSA key." if r.ok
              else f"Provisioning failed: {r.err_code} {r.err_text}")
    finally:
        dev.close()


def cmd_flash(args):
    """Flash the DevEBox (or any STM32F407 board without an ST-LINK) through
    its serial bootloader and the USB-TTL adapter on PA9/PA10."""
    import hashlib
    import zirah_flash as zf
    board = args.board
    path = Path(args.bin) if args.bin else core.firmware_bin(board)
    if not path.is_file():
        fail(f"Firmware file not found: {path}")
    sha = path.with_name(path.name + ".sha256")
    if sha.is_file():
        want = sha.read_text().split()[0].lower()
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            fail(f"{path.name} does not match {sha.name}. The file is damaged; copy the folder again.")
        print(f"Firmware file checked: {path.name}, SHA-256 OK.")
    port = args.port
    if not port:
        ttl = [p.device for p in list_ports() if p.kind == "usb-ttl"]
        if len(ttl) != 1:
            fail("Could not pick the USB-TTL adapter by itself" + (f" (found {', '.join(ttl)})" if ttl else "")
                 + ". Run again with --port, for example: zirah flash --port COM5")
        port = ttl[0]
    print(f"Using {port}.")
    print("1. Wire the adapter: its TXD to PA10, its RXD to PA9, its GND to a GND pin. Leave its 5V and 3V3 pins free.")
    print("2. Put a wire from BT0 to 3V3 on the small SWD header at the top of the board.")
    print("3. Press RST on the board.")
    if not args.yes:
        input("Press Enter when that is done... ")
    last = [""]

    def progress(stage, done, total):
        label = {"erase": "Erasing", "write": "Writing", "verify": "Checking"}[stage]
        pct = 100 * done // total if total else 100
        line = f"{label} {pct:3d} %"
        if line != last[0]:
            print("\r" + line, end="", flush=True)
            last[0] = line
    try:
        digest = zf.flash_file(port, path, board, progress)
    except zf.FlashError as e:
        print()
        fail(e.message)
    print()
    print(f"Written and read back: SHA-256 {digest[:16]}..., the key sector was not touched.")
    print("4. Take the BT0 wire out and press RST. Zirah then starts normally; check with: zirah info")


def cmd_rdp_serial(args):
    """Readout protection on a board without an ST-LINK, through the serial
    bootloader (the same wiring and BT0 wire as zirah flash)."""
    import zirah_flash as zf
    port = args.port
    if not port:
        ttl = [p.device for p in list_ports() if p.kind == "usb-ttl"]
        if len(ttl) != 1:
            fail("Could not pick the USB-TTL adapter by itself. Run again with --port, for example: "
                 "zirah rdp-serial --level 1 --port COM5")
        port = ttl[0]
    if args.level == 0:
        print("Level 0 ERASES THE WHOLE CHIP, the ML-DSA key included. Afterwards you flash again, make a new")
        print("key and trust it again. Export your public keys first if you still need to verify old signatures.")
        if input("Type ERASE to go on: ").strip() != "ERASE":
            fail("Nothing was changed.")
    else:
        print("Level 1 keeps the firmware and the key, and stops anyone reading the flash. You cannot")
        print("reflash the board until you go back to level 0, which erases the key.")
    print("Put the wire from BT0 to 3V3, press RST, and keep the adapter on PA9/PA10.")
    if not args.yes:
        input("Press Enter when that is done... ")
    try:
        zf.set_protection_port(port, args.level)
    except zf.FlashError as e:
        fail(e.message)
    print(f"Readout protection set to level {args.level}. The chip has restarted.")
    print("Take the BT0 wire out and press RST, then check with: zirah info  (HARDENING ... rdp=)")


def cmd_term(args):
    dev = connect(args)
    print(f"Connected to {dev.link.t.name}. Type commands (HELP lists them), 'exit' to quit.")
    try:
        while True:
            try:
                line = input("zirah> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if line.lower() in ("exit", "quit"):
                break
            if not line:
                continue
            if line.upper().startswith("RAW"):
                print("Use 'zirah capture' for RAW (binary output).")
                continue
            try:
                r = dev.link.command(line, timeout=60, on_line=lambda ln: print(clean_text(ln, 4000)))
                print("OK" if r.ok else f"ERR {r.err_code} {r.err_text}")
            except ZirahError as e:
                print(e.message)
    finally:
        dev.close()


def main():
    ap = argparse.ArgumentParser(prog="zirah", description=f"Zirah command line tool {HOST_VERSION}")
    ap.add_argument("--port", help="serial port, e.g. COM5 (default: auto-detect)")
    ap.add_argument("--sim", action="store_true", help="use the simulated device (no hardware)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show the serial traffic")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list serial ports").set_defaults(fn=cmd_ports)
    sub.add_parser("info", help="raw device status").set_defaults(fn=cmd_info)
    sub.add_parser("status", help="readiness checks").set_defaults(fn=cmd_status)
    sub.add_parser("pk", help="show the device public keys").set_defaults(fn=cmd_pk)
    sp = sub.add_parser("trust", help="trust this device's keys after comparing with the OLED")
    sp.add_argument("--label", help="a name for this device")
    sp.add_argument("--replace", action="store_true", help="replace keys that changed (new key)")
    sp.set_defaults(fn=cmd_trust)
    sp = sub.add_parser("untrust", help="remove a trusted device")
    sp.add_argument("uid")
    sp.set_defaults(fn=cmd_untrust)
    sp = sub.add_parser("export-keys", help="write the trusted public keys for other verifiers")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_export_keys)
    sp = sub.add_parser("keygen", help="generate the ML-DSA-65 key")
    sp.add_argument("--force", action="store_true", help="replace an existing key (press the board's button)")
    sp.set_defaults(fn=cmd_keygen)
    sp = sub.add_parser("sign", help="sign a file")
    sp.add_argument("file")
    sp.add_argument("--out", help="output path (default: a signed copy, or a .zirah.json file, next to the file)")
    sp.add_argument("--detached", action="store_true",
                    help="write a separate .zirah.json file even when the format can carry the signature")
    sp.set_defaults(fn=cmd_sign)
    sp = sub.add_parser("verify", help="verify a signed copy, or a file against its signature file")
    sp.add_argument("file")
    sp.add_argument("--sig", help="signature file (default: the signature inside the file, or the one next to it)")
    sp.add_argument("--trusted", help="public-key file from the signer (zirah export-keys)")
    sp.set_defaults(fn=cmd_verify)
    sub.add_parser("prove", help="challenge the device to prove it holds its key").set_defaults(fn=cmd_prove)
    sub.add_parser("i2cscan", help="list I2C devices").set_defaults(fn=simple("I2CSCAN"))
    sub.add_parser("health", help="rerun the entropy startup test").set_defaults(fn=simple("HEALTH"))
    sub.add_parser("jitter", help="measure the LSI clock jitter source").set_defaults(fn=simple("JITTER"))
    sub.add_parser("accel", help="read the board accelerometer source").set_defaults(fn=simple("ACCEL"))
    sp = sub.add_parser("vbias", help="boost voltage")
    sp.add_argument("--watch", action="store_true", help="live readout while adjusting")
    sp.set_defaults(fn=cmd_vbias)
    sp = sub.add_parser("analyze", help="noise statistics")
    sp.add_argument("--n", type=int, default=4096)
    sp.add_argument("--source", choices=["a", "b"], default="a", help="b: the second noise source")
    sp.set_defaults(fn=cmd_analyze)
    sp = sub.add_parser("capture", help="raw entropy capture for SP 800-90B")
    sp.add_argument("--samples", type=int, default=1000000)
    sp.add_argument("--rate", type=int, default=20000)
    sp.add_argument("--out", default="data/entropy/run1")
    sp.add_argument("--notes", default="")
    sp.add_argument("--source", choices=["a", "b"], default="a", help="b: the second noise source")
    sp.set_defaults(fn=cmd_capture)
    sp = sub.add_parser("bench", help="time the masked signing path on the board (throwaway key)")
    sp.add_argument("--count", type=int, default=20, help="signatures to time, 1..100")
    sp.add_argument("--out", help="JSON file for the results (default: the Zirah data folder)")
    sp.set_defaults(fn=cmd_bench)
    sp = sub.add_parser("validate", help="run every hardware check and write a results report")
    sp.add_argument("--bench-count", type=int, default=20, help="benchmark signatures, 0 skips the benchmark")
    sp.add_argument("--corr-runs", type=int, default=10, help="A-B correlation measurements with source B")
    sp.add_argument("--out", help="folder for the report (default: the Zirah data folder)")
    sp.set_defaults(fn=cmd_validate)
    sp = sub.add_parser("atecc", help="secure element")
    sp.add_argument("action", choices=["info", "config", "random", "provision"])
    sp.set_defaults(fn=cmd_atecc)
    sp = sub.add_parser("confirm", help="button confirmation on or off (K1 on the DevEBox, USER on the Discovery)")
    sp.add_argument("state", choices=["on", "off"])
    sp.set_defaults(fn=cmd_confirm)
    sp = sub.add_parser("flash", help="flash a board without an ST-LINK (DevEBox) over the USB-TTL adapter")
    sp.add_argument("--port", default=argparse.SUPPRESS, help="the USB-TTL adapter's port, e.g. COM5")
    sp.add_argument("--board", choices=sorted(core.BOARDS), default="devebox")
    sp.add_argument("--bin", help="firmware file (default: the one for --board in the firmware folder)")
    sp.add_argument("--yes", action="store_true", help="do not wait for Enter before flashing")
    sp.set_defaults(fn=cmd_flash)
    sp = sub.add_parser("rdp-serial", help="readout protection on a board without an ST-LINK (DevEBox)")
    sp.add_argument("--level", type=int, choices=(0, 1), required=True)
    sp.add_argument("--port", default=argparse.SUPPRESS, help="the USB-TTL adapter's port, e.g. COM5")
    sp.add_argument("--yes", action="store_true", help="do not wait for Enter (level 0 still asks for ERASE)")
    sp.set_defaults(fn=cmd_rdp_serial)
    sub.add_parser("term", help="interactive terminal").set_defaults(fn=cmd_term)

    args = ap.parse_args()
    data_dir()
    args.fn(args)


if __name__ == "__main__":
    main()
