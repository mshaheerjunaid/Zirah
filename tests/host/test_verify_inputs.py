"""Tests of what the Verify page and `zirah verify` rely on: sorting dropped
files by their content and naming the trusted signer that matched, here with
a version 1 signature file, which must keep verifying."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))

import zirah_core as core  # noqa: E402
from dilithium_py.ml_dsa import ML_DSA_65  # noqa: E402


class VerifyInputTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.pk, self.sk = ML_DSA_65.keygen()
        self.uid = os.urandom(12).hex().upper()
        self.doc = self.dir / "CV.pdf"
        self.doc.write_bytes(os.urandom(4000))
        self.digest = core.hash_file(self.doc)
        sig = {"format": core.SIG_FORMAT_V1, "context": core.CTX_DOC.decode(),
               "document": {"name": "CV.pdf", "size": self.digest.size, "sha256": self.digest.sha256.hex()},
               "signer": {"device_uid": self.uid, "firmware": "1.0.0"},
               "mldsa65": {"public_key": self.pk.hex(),
                           "signature": ML_DSA_65.sign(self.sk, self.digest.sha256, ctx=core.CTX_DOC).hex()},
               "ecdsa_p256": None, "simulated": False}
        self.sig = self.dir / "CV.pdf.zirah.json"
        self.sig.write_text(json.dumps(sig))
        self.keys = self.dir / "my-keys.json"
        core.export_public_keys(self.keys, [core.TrustedDevice(self.uid, self.pk, None, "Prometheus")])

    def test_dropped_files_are_sorted_by_content(self):
        other_json = self.dir / "data.json"
        other_json.write_text('{"format": "something else"}')
        broken = self.dir / "broken.json"
        broken.write_text("{ not json")
        renamed_sig = self.dir / "signature.txt.json"
        renamed_sig.write_text(self.sig.read_text())
        self.assertEqual(core.classify_file(self.sig), "signature")
        self.assertEqual(core.classify_file(renamed_sig), "signature")
        self.assertEqual(core.classify_file(self.keys), "keys")
        for p in (self.doc, other_json, broken, self.dir / "missing.json"):
            self.assertEqual(core.classify_file(p), "file", p.name)

    def test_report_names_the_matching_signer(self):
        r = core.verify_detached(self.digest, self.sig, core.load_keys_file(self.keys))
        self.assertEqual((r.verdict, r.signer), ("VALID_PQ_ONLY", "Prometheus"))
        self.assertEqual(r.to_dict()["signer"], "Prometheus")
        r = core.verify_detached(self.digest, self.sig, [])
        self.assertEqual((r.verdict, r.signer), ("UNTRUSTED", ""))

    def test_an_edited_file_fails_even_with_the_right_key(self):
        self.doc.write_bytes(self.doc.read_bytes()[:-1] + b"x")
        r = core.verify_detached(core.hash_file(self.doc), self.sig, core.load_keys_file(self.keys))
        self.assertEqual(r.verdict, "INVALID")
        self.assertFalse(next(c for c in r.checks if c.key == "hash").ok)


if __name__ == "__main__":
    unittest.main()
