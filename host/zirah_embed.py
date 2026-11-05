"""Zirah signatures carried inside a signed copy of the file.

The signed copy is the original file with one block added where the file's
own format lets readers skip it, so the copy still opens normally:

  PDF   a comment line after the original %%EOF, followed by the original
        startxref offset and %%EOF again, so readers find the same
        cross-reference data as before
  DOCX, XLSX, PPTX and the other Office Open XML formats
        one extra part, zirah/signature.xml, that no relationship points
        to, so Office applications never load it
  ZIP   the archive comment (.zip files only; Office readers such as
        LibreOffice refuse long archive comments)
  PNG   a private ancillary chunk, zrAH, just before IEND
  JPEG  data after the end-of-image marker, between two markers

The block is the signature record as compact ASCII JSON. Removing it gives
back the original bytes exactly, which is what the signature covers. Other
formats have no place a reader would skip, so they keep a separate
.zirah.json signature file instead.

This module only reads and writes bytes; zirah_core decides what is signed
and how it is checked.
"""
from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import re
import struct
import zlib
from pathlib import Path
from typing import BinaryIO, Callable, Optional

KINDS = ("pdf", "ooxml", "zip", "png", "jpeg")
TAIL = 1024 * 1024              # the block always sits in the last part of the file
CHUNK = 1024 * 1024
PDF_MARK = b"\n%ZIRAH-SIG-1 "    # a PDF comment line; the record holds no raw newline
BIN_MARK = b"\x00ZIRAH-SIG-1\x00"  # ZIP and JPEG; ASCII JSON never holds a NUL byte
PNG_TYPE = b"zrAH"              # ancillary, private, not safe to copy after edits
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
EOCD = b"PK\x05\x06"
OOXML_PART = b"zirah/signature.xml"
OOXML_OPEN = b'<?xml version="1.0" encoding="US-ASCII"?>\n<zirah xmlns="urn:zirah:signature:1">'
OOXML_CLOSE = b"</zirah>\n"
_STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF")
# Office Open XML files only: other OPC packages (MSIX, APPX, VSIX, NuGet)
# sign or check their own parts and must not gain one.
OFFICE = {".docx", ".docm", ".dotx", ".dotm", ".xlsx", ".xlsm", ".xltx", ".xltm",
          ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm"}


@dataclasses.dataclass
class Plan:
    """Where the block goes in a file that has none yet."""
    kind: str
    cut: int                    # bytes of the original copied unchanged
    patch: Callable[[bytes], bytes]   # record -> the bytes that follow them


@dataclasses.dataclass
class Embedded:
    """A block found in a file, and how to rebuild the original from it."""
    kind: str
    record: bytes
    prefix: int                 # the original starts with this many bytes of the file
    suffix: bytes               # and ends with these
    clean: bool                 # nothing but the expected structure follows the block

    @property
    def size(self) -> int:
        return self.prefix + len(self.suffix)


def _tail(path: Path):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - TAIL)
        f.seek(0)
        head = f.read(min(size, 1024))
        f.seek(start)
        return head, f.read(), start, size


def kind_of(head: bytes, suffix: str = "") -> Optional[str]:
    """The format from the first bytes. PDF last: the others have fixed magic
    numbers, while a PDF header may sit anywhere in the first kilobyte, so
    only a .pdf file may have anything in front of it (a text file that
    merely mentions %PDF- is not a PDF)."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] in (b"PK\x03\x04", EOCD):
        return "zip"
    if head.startswith(b"%PDF-") or (b"%PDF-" in head and suffix.lower() == ".pdf"):
        return "pdf"
    return None


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _eocd(tail: bytes, exact: bool):
    """Offset of the end-of-central-directory record in tail and its comment
    length. exact: the comment must end the file."""
    p = len(tail)
    while True:
        p = tail.rfind(EOCD, 0, p)
        if p < 0 or len(tail) - p > 22 + 65535:
            return None
        if p + 22 <= len(tail):
            n = struct.unpack("<H", tail[p + 20:p + 22])[0]
            if p + 22 + n == len(tail) or (not exact and p + 22 + n <= len(tail)):
                return p, n


def _zip_layout(path: Path, tail: bytes, off: int, exact: bool):
    """The end record, central directory and member names of a single-disk
    archive without ZIP64 fields, or None."""
    e = _eocd(tail, exact)
    if e is None:
        return None
    p, n = e
    disk, cd_disk, ent_disk, ent, cd_size, cd_off = struct.unpack("<HHHHII", tail[p + 4:p + 20])
    if disk or cd_disk or ent_disk != ent or 0xFFFF == ent or 0xFFFFFFFF in (cd_size, cd_off) \
            or cd_off + cd_size != off + p:
        return None
    with open(path, "rb") as f:
        f.seek(cd_off)
        cd = f.read(cd_size)
    entries, i = [], 0
    while i < len(cd):
        if cd[i:i + 4] != b"PK\x01\x02" or i + 46 > len(cd):
            return None
        nl, xl, cl = struct.unpack("<HHH", cd[i + 28:i + 34])
        end = i + 46 + nl + xl + cl
        entries.append((i, end, cd[i + 46:i + 46 + nl]))
        i = end
    if len(entries) != ent or i != len(cd):
        return None
    return {"p": p, "n": n, "cd": cd, "cd_off": cd_off, "entries": entries, "comment": tail[p + 22:p + 22 + n],
            "exact": p + 22 + n == len(tail)}


def _end_record(entries: int, cd_size: int, cd_off: int, comment: bytes) -> bytes:
    return EOCD + struct.pack("<HHHHIIH", 0, 0, entries, entries, cd_size, cd_off, len(comment)) + comment


def _ooxml_member(record: bytes, offset: int):
    data = OOXML_OPEN + base64.b64encode(record) + OOXML_CLOSE
    crc = zlib.crc32(data) & 0xFFFFFFFF
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x21, crc, len(data), len(data),
                        len(OOXML_PART), 0) + OOXML_PART + data
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0x21, crc, len(data), len(data),
                          len(OOXML_PART), 0, 0, 0, 0, 0, offset) + OOXML_PART
    return local, central


def _ooxml_find(path: Path, z: dict) -> Optional[Embedded]:
    start, end, name = z["entries"][-1] if z["entries"] else (0, 0, b"")
    if name != OOXML_PART:
        return None
    cd = z["cd"]
    offset = struct.unpack("<I", cd[start + 42:start + 46])[0]
    with open(path, "rb") as f:
        f.seek(offset)
        head = f.read(30 + len(OOXML_PART))
        if len(head) < 30 + len(OOXML_PART) or head[:4] != b"PK\x03\x04" or head[30:] != OOXML_PART:
            return None
        size, nl, xl = struct.unpack("<I", head[18:22])[0], *struct.unpack("<HH", head[26:30])
        f.seek(offset + 30 + nl + xl)
        data = f.read(size)
    if offset + 30 + nl + xl + size != z["cd_off"] or not (data.startswith(OOXML_OPEN) and data.endswith(OOXML_CLOSE)):
        return None
    try:
        record = base64.b64decode(data[len(OOXML_OPEN):-len(OOXML_CLOSE)], validate=True)
    except (binascii.Error, ValueError):
        return None
    rest = cd[:start] + cd[end:]
    suffix = rest + _end_record(len(z["entries"]) - 1, len(rest), offset, z["comment"])
    return Embedded("ooxml", record, offset, suffix, z["exact"])


def find(path: Path) -> Optional[Embedded]:
    """The Zirah block in a file, or None if it has none."""
    head, tail, off, size = _tail(path)
    kind = kind_of(head, path.suffix)
    if kind == "pdf":
        i = tail.rfind(PDF_MARK)
        if i >= 0:
            j = tail.find(b"\n", i + len(PDF_MARK))
            if j > 0:
                m = list(_STARTXREF.finditer(tail[:i]))
                expect = (b"startxref\n" + m[-1].group(1) + b"\n%%EOF\n") if m else None
                return Embedded("pdf", tail[i + len(PDF_MARK):j], off + i, b"",
                                expect is not None and tail[j + 1:] == expect)
    if kind == "zip":
        z = _zip_layout(path, tail, off, exact=False)
        if z is not None:
            found = _ooxml_find(path, z)
            if found is not None:
                return found
            k = z["comment"].rfind(BIN_MARK)
            if k >= 0:
                orig = z["comment"][:k]
                return Embedded("zip", z["comment"][k + len(BIN_MARK):], off + z["p"] + 20,
                                struct.pack("<H", len(orig)) + orig, z["exact"])
    if kind == "png":
        p = len(tail)
        while True:
            p = tail.rfind(PNG_TYPE, 0, p)
            if p < 4:
                break
            start = p - 4
            n = struct.unpack(">I", tail[start:p])[0]
            end = p + 4 + n + 4
            if end <= len(tail) and _png_chunk(PNG_TYPE, tail[p + 4:p + 4 + n]) == tail[start:end]:
                rest = tail[end:]
                return Embedded("png", tail[p + 4:p + 4 + n], off + start, rest, rest == PNG_IEND)
    if kind == "jpeg":
        close = tail.rfind(BIN_MARK)
        start = tail.rfind(BIN_MARK, 0, close) if close > 0 else -1
        if start >= 0 and tail[:start].endswith(b"\xff\xd9"):
            return Embedded("jpeg", tail[start + len(BIN_MARK):close], off + start, b"",
                            close + len(BIN_MARK) == len(tail))
    return None


def plan(path: Path) -> Optional[Plan]:
    """How to embed into this file, or None when its format offers no place
    a reader skips (or it is damaged in a way that would make one unsafe)."""
    head, tail, off, size = _tail(path)
    kind = kind_of(head, path.suffix)
    if kind == "pdf":
        m = list(_STARTXREF.finditer(tail))
        if not m:
            return None
        n = m[-1].group(1)
        return Plan("pdf", size, lambda rec: PDF_MARK + rec + b"\nstartxref\n" + n + b"\n%%EOF\n")
    if kind == "zip":
        z = _zip_layout(path, tail, off, exact=True)
        if z is None:
            return None
        names = {e[2] for e in z["entries"]}
        if path.suffix.lower() in OFFICE:
            if b"[Content_Types].xml" not in names or OOXML_PART in names:
                return None
            cd, cd_off, count, comment = z["cd"], z["cd_off"], len(z["entries"]), z["comment"]

            def patch_ooxml(rec: bytes) -> bytes:
                local, central = _ooxml_member(rec, cd_off)
                return local + cd + central + _end_record(count + 1, len(cd) + len(central),
                                                          cd_off + len(local), comment)
            return Plan("ooxml", cd_off, patch_ooxml)
        if path.suffix.lower() != ".zip":
            return None      # ODF, EPUB, JAR, APK...: their readers check the archive more strictly
        comment, cut = z["comment"], off + z["p"] + 20

        def patch_zip(rec: bytes) -> bytes:
            body = comment + BIN_MARK + rec
            if len(body) > 65535:
                raise ValueError("the signature does not fit in the archive comment")
            return struct.pack("<H", len(body)) + body
        return Plan("zip", cut, patch_zip)
    if kind == "png":
        if not tail.endswith(PNG_IEND):
            return None
        return Plan("png", size - len(PNG_IEND), lambda rec: _png_chunk(PNG_TYPE, rec) + PNG_IEND)
    if kind == "jpeg":
        if not tail.endswith(b"\xff\xd9"):
            return None
        return Plan("jpeg", size, lambda rec: BIN_MARK + rec + BIN_MARK)
    return None


def write_copy(src: Path, out: BinaryIO, p: Plan, record: bytes) -> None:
    """Writes the signed copy: the first p.cut bytes of src, then the block."""
    if b"\n" in record or b"\x00" in record or not record.isascii():
        raise ValueError("the record must be one line of ASCII")
    patch = p.patch(record)
    left = p.cut
    with open(src, "rb") as f:
        while left:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                raise ValueError("the file became shorter while it was copied")
            out.write(chunk)
            left -= len(chunk)
    out.write(patch)


def hash_original(path: Path, e: Embedded, cancel=None) -> bytes:
    """SHA-256 of the original file inside a signed copy."""
    h = hashlib.sha256()
    left = e.prefix
    with open(path, "rb") as f:
        while left:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                raise ValueError("the file is shorter than its signature block says")
            h.update(chunk)
            left -= len(chunk)
    h.update(e.suffix)
    return h.digest()


def signed_name(doc: Path, simulated: bool) -> str:
    """CV.pdf -> CV.signed.pdf (CV.signed-sim.pdf in Simulation Mode)."""
    tag = ".signed-sim" if simulated else ".signed"
    return f"{doc.stem}{tag}{doc.suffix}" if doc.suffix else f"{doc.name}{tag}"
