"""The serial flasher against a simulated STM32F407 bootloader (ST AN3155).

The simulator keeps a flash array with the real sector layout, answers the
commands the way the ROM bootloader does, checks every checksum and refuses
writes to flash that was not erased, so a wrong erase list, a bad checksum
or a write past the image shows up here.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host"))
import zirah_flash as zf  # noqa: E402

ACK, NACK = bytes([0x79]), bytes([0x1F])


class FakeBootloader:
    def __init__(self, size=512 * 1024, pid=0x0413, protected=False):
        self.flash = bytearray(os.urandom(size))      # old contents, not erased
        self.erased = [False] * len(zf.SECTORS)
        self.pid, self.protected = pid, protected
        self.out = bytearray()
        self.inbuf = bytearray()
        self.synced = False
        self.started_at = None
        self.erased_sectors = []

    # Transport
    def reset_input_buffer(self):
        self.out.clear()

    def read(self, n):
        got, self.out[:] = bytes(self.out[:n]), self.out[n:]
        return got

    def write(self, data):
        self.inbuf += data
        self._run()
        return len(data)

    # The bootloader
    def _take(self, n):
        if len(self.inbuf) < n:
            return None
        got, self.inbuf[:] = bytes(self.inbuf[:n]), self.inbuf[n:]
        return got

    def _run(self):
        while True:
            if not self.synced:
                b = self._take(1)
                if b is None:
                    return
                if b == b"\x7f":
                    self.synced = True
                    self.out += ACK
                continue
            if getattr(self, "pending", None):
                if not self.pending():
                    return
                continue
            cmd = self._take(2)
            if cmd is None:
                return
            if cmd[0] ^ cmd[1] != 0xFF:
                self.out += NACK
                continue
            handler = {0x02: self._get_id, 0x44: self._erase, 0x31: self._write, 0x11: self._read,
                       0x21: self._go, 0x82: self._protect, 0x92: self._unprotect}.get(cmd[0])
            if handler is None or (self.protected and cmd[0] in (0x31, 0x11)):
                self.out += NACK
                continue
            self.out += ACK
            self.pending = handler()

    def _get_id(self):
        self.out += bytes([1]) + self.pid.to_bytes(2, "big") + ACK
        return None

    def _addr_step(self, then):
        def step():
            a = self._take(5)
            if a is None:
                return False
            if a[4] != a[0] ^ a[1] ^ a[2] ^ a[3]:
                self.out += NACK
                self.pending = None
                return True
            self.out += ACK
            self.pending = then(int.from_bytes(a[:4], "big"))
            return True
        return step

    def _erase(self):
        def step():
            head = bytes(self.inbuf[:2])
            if len(head) < 2:
                return False
            n = int.from_bytes(head, "big") + 1
            body = self._take(2 + 2 * n + 1)
            if body is None:
                return False
            x = 0
            for b in body[:-1]:
                x ^= b
            if x != body[-1]:
                self.out += NACK
            else:
                for i in range(n):
                    s = int.from_bytes(body[2 + 2 * i:4 + 2 * i], "big")
                    start = sum(zf.SECTORS[:s])
                    self.flash[start:start + zf.SECTORS[s]] = b"\xff" * zf.SECTORS[s]
                    self.erased[s] = True
                    self.erased_sectors.append(s)
                self.out += ACK
            self.pending = None
            return True
        return step

    def _write(self):
        def data(addr):
            def step():
                if not self.inbuf:
                    return False
                n = self.inbuf[0] + 1
                body = self._take(1 + n + 1)
                if body is None:
                    return False
                x = 0
                for b in body[:-1]:
                    x ^= b
                off = addr - zf.FLASH_BASE
                ok = x == body[-1] and n % 4 == 0 and all(v == 0xFF for v in self.flash[off:off + n])
                if ok:
                    self.flash[off:off + n] = body[1:-1]
                self.out += ACK if ok else NACK
                self.pending = None
                return True
            return step
        return self._addr_step(data)

    def _read(self):
        def length(addr):
            def step():
                ln = self._take(2)
                if ln is None:
                    return False
                if ln[0] ^ ln[1] != 0xFF:
                    self.out += NACK
                else:
                    off = addr - zf.FLASH_BASE
                    self.out += ACK + self.flash[off:off + ln[0] + 1]
                self.pending = None
                return True
            return step
        return self._addr_step(length)

    def _protect(self):
        self.protected = True
        self.out += ACK
        return None

    def _unprotect(self):
        self.flash[:] = b"\xff" * len(self.flash)
        self.protected = False
        self.out += ACK
        return None

    def _go(self):
        def done(addr):
            self.started_at = addr
            return None
        return self._addr_step(done)


class FlashTests(unittest.TestCase):
    def test_flashes_verifies_and_keeps_the_key(self):
        bl = FakeBootloader()
        key_start = sum(zf.SECTORS[:7])
        key = bytes(bl.flash[key_start:key_start + 4096])
        image = os.urandom(84_804)
        digest = zf.flash(bl, image, "devebox")
        self.assertEqual(bytes(bl.flash[:len(image)]), image)
        self.assertEqual(bl.erased_sectors, [0, 1, 2, 3, 4])         # 84,804 bytes reach sector 4
        self.assertEqual(bytes(bl.flash[key_start:key_start + 4096]), key)
        self.assertEqual(bl.started_at, zf.FLASH_BASE)
        import hashlib
        self.assertEqual(digest, hashlib.sha256(image).hexdigest())

    def test_sector_lists(self):
        self.assertEqual(zf.sectors_for(1, "devebox"), [0])
        self.assertEqual(zf.sectors_for(64 * 1024, "devebox"), [0, 1, 2, 3])
        self.assertEqual(zf.sectors_for(64 * 1024 + 1, "devebox"), [0, 1, 2, 3, 4])
        self.assertEqual(zf.sectors_for(256 * 1024, "devebox"), [0, 1, 2, 3, 4, 5])
        with self.assertRaises(zf.FlashError):                        # would erase the key sector
            zf.sectors_for(384 * 1024 + 1, "devebox")
        with self.assertRaises(zf.FlashError):
            zf.sectors_for(600 * 1024, "devebox")
        self.assertEqual(zf.sectors_for(896 * 1024, "discovery")[-1], 10)

    def test_odd_length_is_padded(self):
        bl = FakeBootloader()
        image = os.urandom(1001)
        zf.flash(bl, image, "devebox")
        self.assertEqual(bytes(bl.flash[:1001]), image)
        self.assertEqual(bytes(bl.flash[1001:1004]), b"\xff" * 3)

    def test_refusals(self):
        with self.assertRaises(zf.FlashError) as e:
            zf.flash(FakeBootloader(pid=0x0410), os.urandom(4096), "devebox")
        self.assertIn("not an STM32F405/407", e.exception.message)
        with self.assertRaises(zf.FlashError) as e:
            zf.flash(FakeBootloader(protected=True), os.urandom(4096), "devebox")
        self.assertIn("readout protection", e.exception.message)

    def test_protection(self):
        bl = FakeBootloader()
        image = os.urandom(8192)
        zf.flash(bl, image, "devebox")
        bl.synced = False
        zf.set_protection(bl, 1)
        self.assertTrue(bl.protected)
        self.assertEqual(bytes(bl.flash[:8192]), image)               # level 1 keeps the firmware
        bl.synced = False
        with self.assertRaises(zf.FlashError):                        # and refuses a new write
            zf.flash(bl, image, "devebox")
        bl.synced = False
        zf.set_protection(bl, 0)
        self.assertFalse(bl.protected)
        self.assertEqual(set(bl.flash[:8192]), {0xFF})                # level 0 erases everything
        with self.assertRaises(zf.FlashError):
            zf.set_protection(bl, 2)

    def test_silent_board(self):
        class Silent:
            def write(self, d): return len(d)
            def read(self, n): return b""
            def reset_input_buffer(self): pass
        bl = zf.Bootloader(Silent())
        with self.assertRaises(zf.FlashError) as e:
            bl.sync(tries=2)
        self.assertIn("BOOT0", e.exception.message)


if __name__ == "__main__":
    unittest.main()
