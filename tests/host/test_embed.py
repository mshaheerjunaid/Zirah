"""Tests of signed copies (host/zirah_embed.py and the signing and
verification paths in zirah_core that use them).

For every format the signed copy must give back the original bytes exactly,
still be a well-formed file of its kind, verify with the right keys, and
fail when its content is edited, when data is appended, or when the block
is moved somewhere else."""
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))

import zirah_core as core  # noqa: E402
import zirah_embed as embed  # noqa: E402
from zirah_sim import SimTransport  # noqa: E402


def make_pdf() -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def make_zip(office: bool, comment: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if office:
            z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            z.writestr("word/document.xml", "<w:document>Prometheus CV</w:document>" * 50)
        else:
            z.writestr("notes.txt", "hello " * 200)
        z.comment = comment
    return buf.getvalue()


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def make_png() -> bytes:
    raw = b"".join(b"\x00" + bytes([x * 8 % 256, 120, 200]) * 16 for x in range(12))
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 12, 8, 2, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


def make_jpeg() -> bytes:
    try:
        from PIL import Image
    except ImportError:
        return b""
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (20, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


SAMPLES = {"pdf": ("cv.pdf", make_pdf), "ooxml": ("cv.docx", lambda: make_zip(True)),
           "zip": ("files.zip", lambda: make_zip(False, b"kept comment")), "png": ("pic.png", make_png),
           "jpeg": ("pic.jpg", make_jpeg)}


class EmbedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zirah-embed-"))
        os.environ["ZIRAH_HOME"] = str(self.tmp / "home")
        from cryptography.hazmat.primitives.asymmetric import ec
        from dilithium_py.ml_dsa import ML_DSA_65
        self.ML = ML_DSA_65
        self.pk, self.sk = ML_DSA_65.key_derive(b"\x09" * 32)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        n = self.ec.public_key().public_numbers()
        self.ecpk = n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")
        self.trusted = [core.TrustedDevice("B" * 24, self.pk, self.ecpk, "Prometheus")]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sample(self, kind):
        name, make = SAMPLES[kind]
        data = make()
        if not data:
            self.skipTest("Pillow is not installed")
        p = self.tmp / name
        p.write_bytes(data)
        return p, data

    def record(self, doc: Path, container: str) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        manifest = core.make_manifest(core.hash_file(doc), container)
        signed = core.manifest_digest(manifest)
        r, s = utils.decode_dss_signature(self.ec.sign(signed, ec.ECDSA(utils.Prehashed(hashes.SHA256()))))
        res = core.SignResult(self.ML.sign(self.sk, signed, ctx=core.CTX_DOC),
                              r.to_bytes(32, "big") + s.to_bytes(32, "big"), "", {})
        info = core.DeviceInfo(firmware="1.0.0", protocol=1, uid="B" * 24)
        return core.build_signature(manifest, info, core.DeviceKeys(self.pk, self.ecpk), res, simulated=False)

    def signed_copy(self, kind):
        doc, data = self.sample(kind)
        plan = embed.plan(doc)
        self.assertIsNotNone(plan, kind)
        self.assertEqual(plan.kind, kind)
        line = json.dumps(self.record(doc, kind), separators=(",", ":")).encode("ascii")
        out = self.tmp / embed.signed_name(doc, False)
        with open(out, "wb") as f:
            embed.write_copy(doc, f, plan, line)
        return doc, data, out, line

    def test_every_format_gives_back_the_original_and_verifies(self):
        for kind in SAMPLES:
            with self.subTest(kind=kind):
                doc, data, out, line = self.signed_copy(kind)
                self.assertEqual(doc.read_bytes(), data)                 # the original is untouched
                e = embed.find(out)
                self.assertEqual((e.kind, e.record, e.clean, e.size), (kind, line, True, len(data)))
                self.assertEqual(embed.hash_original(out, e), hashlib.sha256(data).digest())
                rep, dg = core.verify_file(out, None, self.trusted)
                self.assertEqual((rep.verdict, rep.signer, rep.found_in), ("VALID", "Prometheus", kind))
                self.assertIsNone(embed.find(doc))

    def test_signed_copies_stay_well_formed(self):
        _, _, out, _ = self.signed_copy("pdf")
        self.assertTrue(out.read_bytes().endswith(b"\nstartxref\n%d\n%%%%EOF\n" % make_pdf().index(b"xref\n")))
        for kind in ("ooxml", "zip"):
            _, data, out, _ = self.signed_copy(kind)
            with zipfile.ZipFile(out) as z, zipfile.ZipFile(io.BytesIO(data)) as orig:
                self.assertIsNone(z.testzip())
                for name in orig.namelist():
                    self.assertEqual(z.read(name), orig.read(name))
                extra = set(z.namelist()) - set(orig.namelist())
                self.assertEqual(extra, {"zirah/signature.xml"} if kind == "ooxml" else set())
                if kind == "zip":
                    self.assertTrue(z.comment.startswith(b"kept comment"))
        _, _, out, _ = self.signed_copy("png")
        b, i, kinds = out.read_bytes(), 8, []
        while i < len(b):
            n = struct.unpack(">I", b[i:i + 4])[0]
            kind, data = b[i + 4:i + 8], b[i + 8:i + 8 + n]
            self.assertEqual(b[i + 8 + n:i + 12 + n], struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
            kinds.append(kind)
            i += 12 + n
        self.assertEqual(kinds, [b"IHDR", b"IDAT", b"zrAH", b"IEND"])
        try:
            from PIL import Image
        except ImportError:
            return
        for kind in ("png", "jpeg"):
            _, _, out, _ = self.signed_copy(kind)
            with Image.open(out) as im:
                im.load()
                self.assertEqual(im.size, (16, 12))

    def test_edits_appends_and_moved_blocks_fail(self):
        for kind in SAMPLES:
            with self.subTest(kind=kind):
                doc, data, out, line = self.signed_copy(kind)
                b = bytearray(out.read_bytes())
                b[embed.find(out).prefix // 2] ^= 0x01                   # one bit inside the original part
                edited = self.tmp / f"edited-{doc.name}"
                edited.write_bytes(bytes(b))
                rep, _ = core.verify_file(edited, None, self.trusted)
                self.assertEqual(rep.verdict, "INVALID")
                self.assertFalse(next(c for c in rep.checks if c.key == "hash").ok)
                # A well-formed copy whose block claims another signing time fails:
                # the time is part of what was signed.
                forged = self.tmp / f"forged-{doc.name}"
                with open(forged, "wb") as f:
                    embed.write_copy(doc, f, embed.plan(doc),
                                     line.replace(b'"signed_at_utc":"2', b'"signed_at_utc":"1'))
                rep, _ = core.verify_file(forged, None, self.trusted)
                self.assertEqual(rep.verdict, "INVALID")
                self.assertTrue(next(c for c in rep.checks if c.key == "hash").ok)
                self.assertFalse(next(c for c in rep.checks if c.key == "mldsa").ok)
                appended = self.tmp / f"appended-{doc.name}"
                appended.write_bytes(out.read_bytes() + b"more data")
                rep, _ = core.verify_file(appended, None, self.trusted)
                self.assertEqual(rep.verdict, "INVALID")
                self.assertFalse(next(c for c in rep.checks if c.key == "intact").ok)
                # The same record in a separate file beside the original is refused:
                # it was made for the signed copy, not for a detached signature.
                moved = self.tmp / f"{doc.name}.zirah.json"
                moved.write_bytes(line)
                rep, _ = core.verify_file(doc, moved, self.trusted)
                self.assertEqual(rep.verdict, "INVALID")
                moved.unlink()

    def test_formats_without_a_hidden_place_stay_detached(self):
        for name, data in (("notes.txt", b"plain text"), ("book.epub", make_zip(False)),
                           ("pdf-notes.txt", b"About PDFs: %PDF-1.4 ... startxref\n9\n%%EOF\n"),
                           ("app.msix", make_zip(True)), ("package.nupkg", make_zip(True)),
                           ("broken.png", make_png()[:-12]), ("trailing.pdf", make_pdf() + b"x" * 2_000_000)):
            p = self.tmp / name
            p.write_bytes(data)
            self.assertEqual(core.signing_container(p), "detached", name)

    def test_names_and_destinations(self):
        doc, _ = self.sample("pdf")
        self.assertEqual(core.default_signature_path(doc, False, "pdf").name, "cv.signed.pdf")
        self.assertEqual(core.default_signature_path(doc, True, "pdf").name, "cv.signed-sim.pdf")
        (self.tmp / "cv.signed.pdf").write_bytes(b"x")
        self.assertEqual(core.default_signature_path(doc, False, "pdf").name, "cv.signed (2).pdf")
        with self.assertRaises(core.ZirahError):
            core.check_destination(self.tmp / "cv.signed.docx", False, "pdf", doc)      # wrong ending
        with self.assertRaises(core.ZirahError):
            core.check_destination(self.tmp / "cv.copy.pdf", True, "pdf", doc)         # simulation needs its tag
        with self.assertRaises(core.ZirahError):
            core.check_destination(self.tmp / "cv.signed.pdf", False, "pdf", doc)      # exists: never overwrite
        core.check_destination(self.tmp / "cv.final.pdf", False, "pdf", doc)
        bare = self.tmp / "scan"                      # a PDF without an ending still gets a signed copy
        bare.write_bytes(make_pdf())
        dest = core.default_signature_path(bare, False, core.signing_container(bare))
        self.assertEqual(dest.name, "scan.signed")
        core.check_destination(dest, False, "pdf", bare)


class SignedCopyFlow(unittest.TestCase):
    """The whole signing flow with the simulated device."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zirah-flow-"))
        os.environ["ZIRAH_HOME"] = str(self.tmp / "home")
        self.t = SimTransport()
        self.dev = core.Device(core.Link(self.t))
        self.dev.handshake()
        self.store = core.TrustStore.memory()
        self.store.pin(self.dev.refresh_info().uid, self.dev.keys(), "simulation")
        self.oled = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sign(self, doc, dest=None, detached=False):
        def ev(stage, fields):
            if stage == "MANIFEST":
                self.oled.append(fields["oled"])
            if stage == "CONFIRM":
                self.oled.append(fields.get("doc"))
                threading.Timer(0.05, self.t.press_button).start()
        return core.sign_file(self.dev, self.store, doc, dest, on_event=ev, detached=detached)

    def test_pdf_becomes_a_signed_copy_and_cannot_be_signed_twice(self):
        doc = self.tmp / "cv.pdf"
        doc.write_bytes(make_pdf())
        before = doc.read_bytes()
        out, rec = self.sign(doc)
        self.assertEqual(out.name, "cv.signed-sim.pdf")
        self.assertEqual(doc.read_bytes(), before)
        self.assertEqual(rec["manifest"]["container"], "pdf")
        self.assertEqual(rec["manifest"]["content_sha256"], hashlib.sha256(before).hexdigest())
        # What the PC showed, what the device echoed and what was signed agree.
        digits = core.short_hex(core.manifest_digest(rec["manifest"]))
        self.assertEqual(self.oled, [digits, digits.replace(" ", "")])
        rep, _ = core.verify_file(out, None, None)
        self.assertEqual(rep.verdict, "SIMULATED")
        self.assertTrue(all(c.ok for c in rep.checks if c.key not in ("trusted", "hardware")))
        with self.assertRaises(core.ZirahError) as cm:
            self.sign(out)
        self.assertEqual(cm.exception.code, "ALREADY_SIGNED")

    def test_detached_when_asked_or_when_the_format_has_no_place(self):
        doc = self.tmp / "cv.pdf"
        doc.write_bytes(make_pdf())
        out, rec = self.sign(doc, detached=True)
        self.assertEqual((out.name, rec["manifest"]["container"]), ("cv.pdf.zirah-sim.json", "detached"))
        txt = self.tmp / "notes.txt"
        txt.write_text("plain")
        out, rec = self.sign(txt)
        self.assertEqual((out.name, rec["manifest"]["container"]), ("notes.txt.zirah-sim.json", "detached"))
        rep, _ = core.verify_file(txt, out, None)
        self.assertEqual(rep.verdict, "SIMULATED")


if __name__ == "__main__":
    unittest.main()
