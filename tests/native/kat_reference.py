#!/usr/bin/env python3
"""Independent known-answer values for the firmware boot self-test.

Computes the four vectors in firmware/src/kat_vectors.h with dilithium-py
(a separate pure-Python ML-DSA implementation) and hashlib, then compares
them with the header. Exit status 0 means the header matches.

    python tests/native/kat_reference.py            check the header
    python tests/native/kat_reference.py --print    print C arrays

Needs: pip install dilithium-py
"""
import hashlib
import re
import sys
from pathlib import Path

from dilithium_py.ml_dsa import ML_DSA_65

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "firmware" / "src" / "kat_vectors.h"

SHAKE_MSG = b"ZIRAH-KAT-v1"
KAT_MSG = b"ZIRAH-KAT-MSG-v1"
KAT_CTX = b"zirah-kat-v1"


def shake(data: bytes, n: int) -> bytes:
    return hashlib.shake_256(data).digest(n)


def compute() -> dict:
    xi = read_header()["k_kat_xi"]
    pk, sk = ML_DSA_65.key_derive(xi)
    sig = ML_DSA_65.sign(sk, KAT_MSG, ctx=KAT_CTX, deterministic=True)
    assert ML_DSA_65.verify(pk, KAT_MSG, sig, ctx=KAT_CTX)
    return {
        "k_kat_xi": xi,
        "k_kat_shake": shake(SHAKE_MSG, 32),
        "k_kat_pk_fp": shake(pk, 16),
        "k_kat_sig_fp": shake(sig, 16),
    }


def read_header() -> dict:
    text = HEADER.read_text()
    out = {}
    for name, body in re.findall(r"static const uint8_t (\w+)\[\d+\] = \{([^}]*)\}", text):
        out[name] = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body))
    return out


def main() -> int:
    want = compute()
    if "--print" in sys.argv:
        for k, v in want.items():
            print(f"{k} = {v.hex()}")
        return 0
    have = read_header()
    bad = [k for k in want if have.get(k) != want[k]]
    for k in want:
        print(f"{'ok  ' if k not in bad else 'FAIL'} {k} {want[k].hex()}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
