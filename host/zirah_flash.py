"""Flash Zirah onto a board without an ST-LINK, through the STM32's own
serial bootloader and a USB-TTL adapter (CP2102, CH340 or FTDI).

Every STM32F4 carries a bootloader in system memory (ST AN2606). It starts
instead of the firmware when BOOT0 is high at reset, and on the STM32F407
it listens on USART1, PA9 and PA10, the same pins the DevEBox build of
Zirah uses for its serial link. The protocol is ST AN3155: 8 data bits,
even parity, a 0x7F byte to start, then commands that are each confirmed
with ACK (0x79) or refused with NACK (0x1F).

Only the flash sectors the firmware occupies are erased, never the key
sector, so the ML-DSA key survives a reflash just as it does with
STM32CubeProgrammer over the ST-LINK. After writing, every byte is read back
and compared, and the board is started with the bootloader's Go command.

Nothing here touches the key: the key sector is outside every erase and
write, and the bootloader refuses all reads and writes once readout
protection is on, which this tool reports instead of trying to remove it
(removing level 1 mass-erases the chip, key included).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Protocol

ACK, NACK, SYNC = 0x79, 0x1F, 0x7F
CMD_GET, CMD_GET_ID, CMD_READ, CMD_GO, CMD_WRITE, CMD_EXT_ERASE = 0x00, 0x02, 0x11, 0x21, 0x31, 0x44
CMD_PROTECT, CMD_UNPROTECT = 0x82, 0x92
FLASH_BASE = 0x08000000
STM32F40X_PID = 0x0413

# STM32F407 sector sizes (RM0090 3.3): four of 16 KB, one of 64 KB, then 128 KB.
SECTORS = [16 * 1024] * 4 + [64 * 1024] + [128 * 1024] * 7
# The key sector, which is never erased or written here (src/board_pins.h).
KEY_SECTOR = {"devebox": 7, "discovery": 11}
FLASH_SIZE = {"devebox": 512 * 1024, "discovery": 1024 * 1024}


class FlashError(Exception):
    """A step failed; `message` says what to do in plain words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Transport(Protocol):
    def write(self, data: bytes) -> int: ...

    def read(self, n: int) -> bytes: ...

    def reset_input_buffer(self) -> None: ...


def sectors_for(length: int, board: str) -> List[int]:
    """The sector numbers covering [0, length) from the start of flash."""
    out, start = [], 0
    for i, size in enumerate(SECTORS):
        if start >= length:
            break
        out.append(i)
        start += size
    if length > FLASH_SIZE[board]:
        raise FlashError("The firmware file is larger than this board's flash.")
    if KEY_SECTOR[board] in out:
        raise FlashError("The firmware would reach the key sector; refusing to write it.")
    return out


def _xor(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


def _addr(a: int) -> bytes:
    b = a.to_bytes(4, "big")
    return b + bytes([_xor(b)])


@dataclass
class Bootloader:
    link: Transport
    timeout: float = 1.0

    def _byte(self, wait: float) -> int:
        end = time.monotonic() + wait
        while True:
            got = self.link.read(1)
            if got:
                return got[0]
            if time.monotonic() > end:
                raise FlashError("The board stopped answering. Check the TX, RX and GND wires, then try again.")

    def _ack(self, what: str, wait: Optional[float] = None) -> None:
        b = self._byte(self.timeout if wait is None else wait)
        if b == NACK:
            raise FlashError(f"The bootloader refused {what}. If readout protection is on, it cannot be "
                             "flashed this way without erasing the whole chip, key included.")
        if b != ACK:
            raise FlashError(f"Unexpected answer 0x{b:02X} to {what}. Check the wiring and the baud rate.")

    def _cmd(self, code: int, what: str) -> None:
        self.link.write(bytes([code, code ^ 0xFF]))
        self._ack(what)

    def sync(self, tries: int = 10) -> None:
        """Start the protocol. 0x7F lets the bootloader find the baud rate; a
        bootloader that is already running answers it with NACK."""
        for _ in range(tries):
            self.link.reset_input_buffer()
            self.link.write(bytes([SYNC]))
            try:
                b = self._byte(0.5)
            except FlashError:
                continue
            if b in (ACK, NACK):
                return
        raise FlashError("No answer from the bootloader. Fit the BOOT0 wire (BT0 to 3V3), press RST, "
                         "check that the adapter's TX goes to PA10 and its RX to PA9, then try again.")

    def chip_id(self) -> int:
        self._cmd(CMD_GET_ID, "the ID request")
        n = self._byte(self.timeout) + 1
        pid = int.from_bytes(bytes(self._byte(self.timeout) for _ in range(n)), "big")
        self._ack("the ID request")
        return pid

    def erase(self, sectors: List[int]) -> None:
        self._cmd(CMD_EXT_ERASE, "the erase command")
        body = (len(sectors) - 1).to_bytes(2, "big") + b"".join(s.to_bytes(2, "big") for s in sectors)
        self.link.write(body + bytes([_xor(body)]))
        # A 128 KB sector takes up to 2 s to erase (STM32F407 datasheet, tERASE128KB).
        self._ack("the erase", wait=2.5 * len(sectors) + 2)

    def write(self, address: int, data: bytes) -> None:
        assert 0 < len(data) <= 256 and len(data) % 4 == 0
        self._cmd(CMD_WRITE, "the write command")
        self.link.write(_addr(address))
        self._ack("the write address")
        body = bytes([len(data) - 1]) + data
        self.link.write(body + bytes([_xor(body)]))
        self._ack("the data", wait=2.0)

    def read(self, address: int, n: int) -> bytes:
        assert 0 < n <= 256
        self._cmd(CMD_READ, "the read command")
        self.link.write(_addr(address))
        self._ack("the read address")
        self.link.write(bytes([n - 1, (n - 1) ^ 0xFF]))
        self._ack("the read length")
        out = bytearray()
        end = time.monotonic() + 2.0
        while len(out) < n:
            chunk = self.link.read(n - len(out))
            if chunk:
                out += chunk
            elif time.monotonic() > end:
                raise FlashError("The board stopped sending while reading back.")
        return bytes(out)

    def protect(self) -> None:
        """Readout protection level 1 (AN3155 Readout Protect). The chip then
        resets by itself; afterwards the bootloader refuses reads and writes."""
        self._cmd(CMD_PROTECT, "readout protection")
        self._ack("readout protection", wait=5.0)

    def unprotect(self) -> None:
        """Back to level 0. The chip mass-erases its whole flash first, key
        included, and resets."""
        self._cmd(CMD_UNPROTECT, "removing readout protection")
        self._ack("removing readout protection", wait=30.0)

    def go(self, address: int = FLASH_BASE) -> None:
        self._cmd(CMD_GO, "the start command")
        self.link.write(_addr(address))
        self._ack("the start address")


def flash(link: Transport, image: bytes, board: str = "devebox",
          progress: Optional[Callable[[str, int, int], None]] = None) -> str:
    """Erase the sectors the image needs, write it, read it back, start it.
    Returns the SHA-256 of what was verified on the board."""
    if not image:
        raise FlashError("The firmware file is empty.")
    image = image + b"\xff" * (-len(image) % 4)     # writes are whole 32-bit words
    say = progress or (lambda stage, done, total: None)
    bl = Bootloader(link)
    bl.sync()
    pid = bl.chip_id()
    if pid != STM32F40X_PID:
        raise FlashError(f"This is not an STM32F405/407 (chip ID 0x{pid:03X}). Check which board is connected.")
    sectors = sectors_for(len(image), board)
    say("erase", 0, len(sectors))
    bl.erase(sectors)
    say("erase", len(sectors), len(sectors))
    for off in range(0, len(image), 256):
        bl.write(FLASH_BASE + off, image[off:off + 256])
        say("write", min(off + 256, len(image)), len(image))
    back = bytearray()
    for off in range(0, len(image), 256):
        back += bl.read(FLASH_BASE + off, min(256, len(image) - off))
        say("verify", len(back), len(image))
    if bytes(back) != image:
        raise FlashError("The read-back did not match the file. Flash again.")
    bl.go()
    return hashlib.sha256(bytes(back)).hexdigest()


def flash_file(port: str, path: Path, board: str = "devebox",
               progress: Optional[Callable[[str, int, int], None]] = None) -> str:
    import serial  # pyserial, already needed by the rest of Zirah

    image = path.read_bytes()
    try:
        link = serial.Serial(port, 115200, bytesize=8, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.1)
    except serial.SerialException as e:
        raise FlashError(f"Cannot open {port}: {e}. Close the Zirah app and any terminal using it.") from e
    with link:
        return flash(link, image, board, progress)


def set_protection(link: Transport, level: int) -> None:
    """Level 1 keeps the firmware and key; level 0 erases the whole chip."""
    bl = Bootloader(link)
    bl.sync()
    pid = bl.chip_id()
    if pid != STM32F40X_PID:
        raise FlashError(f"This is not an STM32F405/407 (chip ID 0x{pid:03X}). Check which board is connected.")
    if level == 1:
        bl.protect()
    elif level == 0:
        bl.unprotect()
    else:
        raise FlashError("Only levels 0 and 1 are offered; level 2 is permanent.")


def set_protection_port(port: str, level: int) -> None:
    import serial

    try:
        link = serial.Serial(port, 115200, bytesize=8, parity=serial.PARITY_EVEN, stopbits=1, timeout=0.1)
    except serial.SerialException as e:
        raise FlashError(f"Cannot open {port}: {e}. Close the Zirah app and any terminal using it.") from e
    with link:
        set_protection(link, level)
