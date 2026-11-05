#!/usr/bin/env python3
"""Draws hardware/schematic.svg (circuit schematic of the Zirah entropy source
and I2C bus). The component names match breadboard_A.svg / WIRING_TABLE.md."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "schematic.svg"
o = []
INK = "#1b1b1b"


def line(x1, y1, x2, y2, w=1.8, c=INK, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="{w}"{d}/>')


def poly(pts, w=1.8):
    p = " ".join(f"{x},{y}" for x, y in pts)
    o.append(f'<polyline points="{p}" fill="none" stroke="{INK}" stroke-width="{w}"/>')


def text(x, y, s, size=12, anchor="start", weight="normal", fill=INK):
    o.append(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
             f'font-weight="{weight}" fill="{fill}">{s}</text>')


def dot(x, y):
    o.append(f'<circle cx="{x}" cy="{y}" r="3.4" fill="{INK}"/>')


def res_h(x1, x2, y, name, val):
    """Horizontal resistor between x1 and x2 (box symbol)."""
    mid = (x1 + x2) / 2
    line(x1, y, mid - 22, y)
    line(mid + 22, y, x2, y)
    o.append(f'<rect x="{mid - 22}" y="{y - 8}" width="44" height="16" fill="#fff" stroke="{INK}" stroke-width="1.8"/>')
    text(mid, y - 13, name, 11, "middle", "bold")
    text(mid, y + 23, val, 11, "middle")


def res_v(x, y1, y2, name, val, side=1):
    mid = (y1 + y2) / 2
    line(x, y1, x, mid - 22)
    line(x, mid + 22, x, y2)
    o.append(f'<rect x="{x - 8}" y="{mid - 22}" width="16" height="44" fill="#fff" stroke="{INK}" stroke-width="1.8"/>')
    anchor = "start" if side > 0 else "end"
    text(x + side * 13, mid - 2, name, 11, anchor, "bold")
    text(x + side * 13, mid + 12, val, 11, anchor)


def cap_v(x, y1, y2, name, val, polar=False, side=1):
    mid = (y1 + y2) / 2
    line(x, y1, x, mid - 4)
    line(x, mid + 4, x, y2)
    line(x - 13, mid - 4, x + 13, mid - 4, 2.4)
    if polar:
        o.append(f'<path d="M {x - 13} {mid + 8} Q {x} {mid + 1} {x + 13} {mid + 8}" fill="none" stroke="{INK}" stroke-width="2.4"/>')
        text(x - 18 * (1 if side > 0 else -1), mid - 6, "+", 12, "middle", "bold")
    else:
        line(x - 13, mid + 4, x + 13, mid + 4, 2.4)
    anchor = "start" if side > 0 else "end"
    text(x + side * 18, mid - 2, name, 11, anchor, "bold")
    text(x + side * 18, mid + 12, val, 11, anchor)


def cap_h(x1, x2, y, name, val):
    mid = (x1 + x2) / 2
    line(x1, y, mid - 4, y)
    line(mid + 4, y, x2, y)
    line(mid - 4, y - 13, mid - 4, y + 13, 2.4)
    line(mid + 4, y - 13, mid + 4, y + 13, 2.4)
    text(mid, y - 18, name, 11, "middle", "bold")
    text(mid, y + 28, val, 11, "middle")


def gnd(x, y):
    line(x, y, x, y + 8)
    line(x - 11, y + 8, x + 11, y + 8, 2)
    line(x - 7, y + 12, x + 7, y + 12, 2)
    line(x - 3, y + 16, x + 3, y + 16, 2)


def port(x, y, label, left=False):
    w = 9 * len(label) + 12
    x0 = x - w if left else x
    o.append(f'<rect x="{x0}" y="{y - 11}" width="{w}" height="22" rx="4" fill="#eef2f7" stroke="#33415c"/>')
    text(x0 + w / 2, y + 4, label, 11.5, "middle", "bold", "#33415c")


W, H = 1250, 1500
o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="Arial, Helvetica, sans-serif">')
o.append(f'<rect width="{W}" height="{H}" fill="#fff"/>')
text(20, 32, "Zirah Entropy Source and Secure-Element Bus", 19, weight="bold")
text(20, 54, "Avalanche breakdown of a reverse-biased base-emitter junction, amplified by an LM358 "
     "and sampled by the STM32F407 ADC on PA1. Part names match the breadboard drawings.", 12, fill="#444")

Y = 130
# ---- boost and filter ----
port(20, Y, "5V")
line(48, Y, 80, Y)
o.append(f'<rect x="80" y="{Y - 30}" width="120" height="60" rx="6" fill="#f7f7f7" stroke="{INK}" stroke-width="1.8"/>')
text(140, Y - 6, "MT3608", 13, "middle", "bold")
text(140, Y + 12, "Boost, About 12 V", 11, "middle")
line(140, Y + 30, 140, Y + 44)
gnd(140, Y + 44)
line(200, Y, 250, Y)
dot(250, Y)
text(250, Y - 12, "HV", 11, "middle", "bold", "#c1121f")
res_h(250, 360, Y, "R1", "1 k")
dot(360, Y)
text(372, Y - 12, "HVF (Filtered)", 11, "start", "bold", "#c1121f")
line(360, Y, 560, Y)
dot(410, Y)
cap_v(410, Y, Y + 90, "C1", "100 uF", polar=True)
gnd(410, Y + 90)
dot(482, Y)
cap_v(482, Y, Y + 90, "C2", "100 nF")
gnd(482, Y + 90)

# ---- monitor divider (to PB0) ----
res_v(250, Y, Y + 110, "R9", "1 M", 1)
dot(250, Y + 110)
res_v(250, Y + 110, Y + 210, "R10", "100 k", 1)
gnd(250, Y + 210)
line(250, Y + 110, 200, Y + 110)
dot(200, Y + 110)
cap_v(200, Y + 110, Y + 190, "C7", "100 nF", side=-1)
gnd(200, Y + 190)
line(200, Y + 110, 150, Y + 110)
port(150, Y + 110, "PB0", left=True)
text(150, Y + 136, "Boost Monitor", 10.5, "end", fill="#444")

# ---- avalanche junction, coupling, amplifier and output ----
# Drawn by one function for source A and for the optional source B, which
# is the same circuit on the other half of U1, biased from the same VREF.

def source_chain(dy, n):
    """One noise source from the filtered 12 V to its ADC pin, shifted down by dy."""
    y0 = Y + dy
    res_v(560, y0, y0 + 110, n["R_feed"], "100 k")
    dot(560, y0 + 110)
    text(548, y0 + 104, "E Node", 11, "end", "bold", "#c1121f")
    TX, TY = 560, y0 + 175
    o.append(f'<circle cx="{TX}" cy="{TY}" r="26" fill="none" stroke="{INK}" stroke-width="1.8"/>')
    line(TX - 10, TY - 16, TX - 10, TY + 16, 3)
    line(TX - 10, TY - 6, TX + 8, TY - 22)
    line(TX + 8, TY - 22, TX, TY - 46)
    line(TX, TY - 46, TX, y0 + 110)
    o.append(f'<polygon points="{TX + 8},{TY - 22} {TX - 2},{TY - 20} {TX + 4},{TY - 13}" fill="{INK}"/>')
    line(TX - 10, TY + 6, TX + 8, TY + 22)
    line(TX + 8, TY + 22, TX + 24, TY + 38)
    text(TX + 26, TY + 50, "C: Not Connected", 10.5, "start", fill="#666")
    line(TX - 10, TY, TX - 50, TY)
    gnd(TX - 50, TY)
    text(TX - 34, TY + 40, f"{n['Q']} 2N3904", 11.5, "end", "bold")
    text(TX - 34, TY + 55, "B-E Junction Reverse", 10.5, "end", fill="#444")
    text(TX - 34, TY + 68, "Biased into Avalanche", 10.5, "end", fill="#444")

    line(560, y0 + 110, 600, y0 + 110)
    cap_h(600, 680, y0 + 110, n["C_cpl"], "100 nF")
    dot(680, y0 + 110)
    text(680, y0 + 96, "IN+", 11, "middle", "bold", "#c1121f")
    ax, ay = 820, y0 + 130
    line(680, y0 + 110, ax, y0 + 110)
    res_v(680, y0 + 110, y0 + 260, n["R_bias"], "100 k", 1)
    dot(680, y0 + 260)
    text(672, y0 + 254, "VREF About 1.5 V", 11, "end", "bold", "#c1121f")

    o.append(f'<polygon points="{ax},{ay - 50} {ax},{ay + 50} {ax + 100},{ay}" fill="#fff" stroke="{INK}" '
             f'stroke-width="1.8"/>')
    text(ax + 8, ay - 14, "+", 15, weight="bold")
    text(ax + 9, ay + 27, "-", 17, weight="bold")
    text(ax + 40, ay + 4, "U1", 11.5, weight="bold")
    text(ax + 20, ay + 70, n["half"], 10.5, fill="#444")
    ox = ax + 100
    line(ax - 20, ay + 20, ax, ay + 20)
    line(ax - 20, ay + 20, ax - 20, ay + 140)
    dot(ax - 20, ay + 140)
    line(ox, ay, ox + 40, ay)
    dot(ox + 40, ay)
    line(ox + 40, ay, ox + 40, ay + 140)
    res_h(ax - 20, ox + 40, ay + 140, n["R_fb"], "100 k")
    res_v(ax - 20, ay + 140, ay + 240, n["R_g"], "10 k", 1)
    cap_v(ax - 20, ay + 240, ay + 320, n["C_g"], "10 uF", polar=True, side=-1)
    gnd(ax - 20, ay + 320)
    text(ax + 60, ay + 270, f"AC Gain = 1 + {n['R_fb']}/{n['R_g']} = 11", 11, fill="#444")
    text(ax + 60, ay + 286, f"DC Gain = 1 ({n['C_g']} Blocks DC)", 11, fill="#444")
    line(ox + 40, ay, ox + 60, ay)
    res_h(ox + 60, ox + 160, ay, n["R_out"], "1 k")
    dot(ox + 160, ay)
    cap_v(ox + 160, ay, ay + 90, n["C_out"], "100 pF")
    gnd(ox + 160, ay + 90)
    line(ox + 160, ay, ox + 190, ay)
    port(ox + 190, ay, n["pin"])
    text(ox + 190, ay + 28, n["pin_note"], 10.5, fill="#444")
    return ax


AX = source_chain(0, {"R_feed": "R2", "Q": "Q1", "C_cpl": "C3", "R_bias": "R3", "half": "LM358, Half A",
                      "R_fb": "R7", "R_g": "R6", "C_g": "C5", "R_out": "R8", "C_out": "C6", "pin": "PA1",
                      "pin_note": "Source A to ADC1"})

# The bias reference, shared by both sources.
line(620, Y + 260, 740, Y + 260)
dot(620, Y + 260)
res_v(620, Y + 260, Y + 360, "R4", "100 k", -1)
line(620, Y + 360, 620, Y + 372)
port(605, Y + 384, "3V")
res_v(680, Y + 260, Y + 360, "R5", "100 k", 1)
gnd(680, Y + 360)
dot(740, Y + 260)
cap_v(740, Y + 260, Y + 350, "C4", "10 uF", polar=True)
gnd(740, Y + 350)

text(AX - 20, Y - 40, "U1 pin 8 = 5V (C8 100 nF + C9 10 uF to GND), pin 4 = GND.", 10.5, fill="#444")
text(AX - 20, Y - 25, "Half B: the optional source B below, or if it is not fitted, pin 5 to GND and pin 6 to pin 7.",
     10.5, fill="#444")

# ---- I2C bus ----
IY = 720
text(20, 620, "Secure Element and Display, Software I2C at 3 V", 14, weight="bold")
port(20, IY, "PA8 (SCL)")
port(20, IY + 50, "PC9 (SDA)")
line(110, IY, 1000, IY)
line(110, IY + 50, 1000, IY + 50)
text(1006, IY + 4, "SCL", 11, weight="bold")
text(1006, IY + 54, "SDA", 11, weight="bold")
dot(430, IY)
res_v(430, IY - 90, IY, "RP1", "4.7 k", 1)
port(415, IY - 102, "3V")
dot(360, IY + 50)
res_v(360, IY + 50, IY + 140, "RP2", "4.7 k", 1)
port(345, IY + 152, "3V")
for x0, name, sub in ((520, "ATECC608", "0x60, P-256 Key in Slot 0"),
                      (760, "OLED SH1106", "0x3C, 128 x 64")):
    o.append(f'<rect x="{x0}" y="{IY + 100}" width="170" height="56" rx="6" fill="#1d3557"/>')
    text(x0 + 85, IY + 124, name, 12.5, "middle", "bold", "#fff")
    text(x0 + 85, IY + 142, sub, 10.5, "middle", fill="#dfe7f2")
    line(x0 + 50, IY, x0 + 50, IY + 100)
    dot(x0 + 50, IY)
    line(x0 + 120, IY + 50, x0 + 120, IY + 100)
    dot(x0 + 120, IY + 50)
    text(x0 + 53, IY + 94, "SCL", 9.5)
    text(x0 + 123, IY + 94, "SDA", 9.5)
text(520, IY + 176, "Both modules: VCC to 3V and GND to GND. Lines that cross without a dot are not connected.",
     10.5, fill="#444")

# ---- optional second noise source ----
BY = 960
line(20, BY - 30, W - 20, BY - 30, 1, "#bbb")
text(20, BY - 2, "Optional Second Noise Source (Source B), the Same Circuit on Half B of U1", 14, weight="bold")
text(20, BY + 18, "Fed from HVF like source A and biased from the same VREF. The firmware finds it on PB1 at "
     "power-up; entropy is credited to source A only.", 11, fill="#444")
port(480, BY + 40, "HVF")
line(530, BY + 40, 560, BY + 40)
dot(560, BY + 40)
source_chain(BY - Y + 40, {"R_feed": "R11", "Q": "Q2", "C_cpl": "C10", "R_bias": "R12",
                           "half": "LM358, Half B", "R_fb": "R13", "R_g": "R15",
                           "C_g": "C12", "R_out": "R14", "C_out": "C11", "pin": "PB1",
                           "pin_note": "Source B to ADC2"})
line(680, BY + 300, 680, BY + 318)
port(656, BY + 330, "VREF")

o.append("</svg>")
OUT.write_text("\n".join(x for x in o if x))
print(f"wrote {OUT}")
