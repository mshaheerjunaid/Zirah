"""Every connection of the measured Zirah build on the DevEBox (5 November 2026).
Pins from firmware/src/board_pins.h and the as-built layout in
hardware/layout/zirah_layout.py (BOARD_A_DEV); Board B as in hardware/WIRING.md."""
import sys
from pathlib import Path
from style import new, box, wire, save, save_svg, INK, SUB, WIRE

fig, ax = new(4.15)
P = 6.0      # pin labels
S = 5.6      # small notes


def t(x, y, s, ha="center", va="center", fs=P, color=INK, **kw):
    ax.text(x, y, s, ha=ha, va=va, fontsize=fs, color=color, zorder=6, **kw)


# --- the PC and the adapter
box(ax, 2, 71, 20, 15, "gray", "Windows PC", "Zirah App and Verifier", fs=7.4, sfs=6.2)
box(ax, 2, 42, 20, 19, "sky")
t(12, 58.0, "CP2102 Adapter", fs=7.4)
t(12, 55.2, "USB to UART", fs=6.2, color=SUB)
t(12, 38.6, "Its 5V and 3V3 Pins\nAre Left Unconnected", fs=S, color=SUB, linespacing=1.2)
wire(ax, [(12, 71), (12, 61)], SUB, lw=1.1)
t(13.2, 66, "USB", ha="left", fs=P, color=SUB)

# --- the DevEBox
box(ax, 34, 35, 32, 51, "blue")
t(50, 83.0, "DevEBox STM32F4XX_M", fs=7.6)
t(50, 80.0, "STM32F407VE at 168 MHz", fs=6.2, color=SUB)
for i, s in enumerate(["K1 Button on PA0", "LED D2 on PA1", "Key in Flash Sector 7",
                       "Readout Protection 1", "BT0 to 3V3 for Flashing"]):
    t(56.0, 56.5 - i * 3.6, s, fs=S, color=SUB)
wire(ax, [(22, 78.5), (34, 78.5)], SUB, lw=1.1)
t(28, 81.4, "USB-C", fs=P, color=SUB)
t(28, 75.6, "5 V Only", fs=P, color=SUB)

# serial link: adapter TXD to PA10, RXD to PA9, GND to GND
for y, a, b, c in [(51.0, "TXD", "PA10 (RX)", WIRE["uart"]), (47.5, "RXD", "PA9 (TX)", WIRE["uart"]),
                   (44.0, "GND", "GND", WIRE["gnd"])]:
    wire(ax, [(22, y), (34, y)], c)
    t(21.2, y, a, ha="right", fs=S)
    t(34.8, y, b, ha="left")
t(28, 53.6, "115,200 Baud", fs=S, color=SUB)

# --- Board B: 3V3, GND, PA8 SCL, PC9 SDA
box(ax, 75, 52, 23, 34, "green")
t(86.5, 83.0, "Board B, I2C Bus", fs=7.4)
box(ax, 82, 67.8, 14.5, 9.4, "gray", "SH1106 OLED", "Address 0x3C", fs=6.4, sfs=5.8, r=1.0, z=2)
box(ax, 82, 56.8, 14.5, 9.4, "gray", "ATECC608", "Address 0x60", fs=6.4, sfs=5.8, r=1.0, z=2)
t(86.5, 54.0, "RP1, RP2 4.7 kΩ Pull-Ups", fs=S, color=SUB)
for y, pin, lab, c in [(75.5, "3V3", "VCC", WIRE["3v3"]), (71.5, "GND", "GND", WIRE["gnd"]),
                       (67.5, "PA8 (SCL)", "SCL", WIRE["scl"]), (63.5, "PC9 (SDA)", "SDA", WIRE["sda"])]:
    wire(ax, [(66, y), (75, y)], c)
    t(65.2, y, pin, ha="right")
    t(75.8, y, lab, ha="left", fs=S)

# --- Board A, fed from the DevEBox's bottom header; wires step right so no two cross
box(ax, 33, 2, 65, 22, "red")
t(86.0, 15.0, "Board A", fs=7.6)
t(86.0, 11.8, "Avalanche Noise Source", fs=6.2, color=SUB)
t(86.0, 8.8, "Full-Size Breadboard", fs=6.2, color=SUB)
pins = [(37.0, 37.0, "5V", "L+ Rail", "5 V", WIRE["5v"]),
        (41.5, 44.6, "GND", "L− Rail", "", WIRE["gnd"]),
        (46.0, 52.2, "GND", "L− Rail", "Bottom", WIRE["gnd"]),
        (51.5, 59.8, "3V3", "R4 to", "1.65 V", WIRE["3v3"]),
        (57.0, 67.4, "PA4", "R8", "Noise Out", WIRE["pa4"]),
        (62.5, 75.0, "PB0", "R9, R10", "Monitor", WIRE["pb0"])]
for i, (xp, xt, pin, l1, l2, c) in enumerate(pins):
    lvl = 25.6 + i * 1.45
    wire(ax, [(xp, 35), (xp, lvl), (xt, lvl), (xt, 24)] if xp != xt else [(xp, 35), (xp, 24)], c)
    t(xp, 37.2, pin, fs=S)
    t(xt, 21.6, l1, fs=S)
    if l2:
        t(xt, 19.0, l2, fs=S, color=SUB)

# --- the boost module, on its own small breadboard
box(ax, 2, 2, 20, 22, "orange")
t(12, 21.0, "MT3608 Boost", fs=7.4)
t(12, 18.0, "Output About 28 V", fs=6.2, color=SUB)
for y, a, b, c in [(14.0, "VIN+", "L+", WIRE["5v"]), (10.5, "VIN−", "L−", WIRE["gnd"]),
                   (7.0, "VOUT+", "R16", WIRE["boost"]), (3.5 + 0.2, "VOUT−", "L−", WIRE["gnd"])]:
    wire(ax, [(22, y), (33, y)], c)
    t(21.2, y, a, ha="right", fs=S)
    t(33.8, y, b, ha="left", fs=S)

if len(sys.argv) > 1:          # a folder: PNG at 1000 dpi, PDF and SVG for the report
    save(fig, sys.argv[1], "wiring-devebox")
else:                          # the drawing kept in the repository
    save_svg(fig, Path(__file__).resolve().parents[2] / "wiring_devebox.svg")
print("ok")
