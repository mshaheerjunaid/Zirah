"""Generate the step-by-step build page for Board A on the DevEBox.

Every hole on the page comes from BOARD_A_DEV in zirah_layout.py, and the page
is only written after that layout passes the netlist check and the
build-order check, so the page cannot name a hole the checks have not passed.

    python3 hardware/layout/build_guide.py   ->  hardware/board_a_build.html
"""

import html
import json
from pathlib import Path

import zirah_layout as z

OUT = Path(__file__).resolve().parent.parent / "board_a_build.html"
PARTS = {p[0]: p for p in z.BOARD_A_DEV}

BANDS = {"1k": ["brown", "black", "red"], "10k": ["brown", "black", "orange"],
         "100k": ["brown", "black", "yellow"]}
OHMS = {"1k": "1 k\u03a9", "10k": "10 k\u03a9", "100k": "100 k\u03a9"}
BAND_HEX = {"black": "#1b1b1b", "brown": "#7b4a21", "red": "#c62828", "orange": "#ee7a12",
            "yellow": "#f2c40f", "gold": "#c8a24a"}
CAP = {"10uF": "10 \u00b5F", "100uF": "100 \u00b5F"}
EXT_NAME = {"DEV:5V": "DevEBox 5V", "DEV:GND": "DevEBox GND", "DEV:3V3": "DevEBox 3V3",
            "DEV:PA4": "DevEBox PA4", "DEV:PB0": "DevEBox PB0",
            "MT3608:VIN+": "Boost VIN+", "MT3608:VIN-": "Boost VIN\u2212",
            "MT3608:VOUT+": "Boost VOUT+", "MT3608:VOUT-": "Boost VOUT\u2212"}
STRIP = {"L-": "the left blue (\u2212) strip", "L+": "the left red (+) strip",
         "R-": "the right blue (\u2212) strip", "R+": "the right red (+) strip"}
MINUS = "\u2212"

# --------------------------------------------------------------------------
# Drawing (pitch units: column a = 0, e = 4, f = 7, j = 11)
# --------------------------------------------------------------------------
P = 20
OX, OY = 172, 46
U_STRIP = {"L-": -3.3, "L+": -2.2, "R+": 13.2, "R-": 14.3}
W = int(OX + 14.3 * P + 128)
FLAG_W = 100
H = int(OY + (z.ROWS - 1) * P + 40)


def gx(col):
    return z.LEFT.index(col) if col in z.LEFT else 7 + z.RIGHT.index(col)


def pos(pt):
    if z.is_rail(pt):
        rail, row = pt.split("@")
        return OX + U_STRIP[rail] * P, OY + (int(row) - 1) * P
    return OX + gx(pt[-1]) * P, OY + (int(pt[:-1]) - 1) * P


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def board_svg():
    o = [f'<svg id="board" viewBox="0 0 {W} {H}" role="img" aria-label="Breadboard drawing">']
    top, bot = OY - 0.9 * P, OY + (z.ROWS - 0.1) * P
    o.append(f'<rect x="{f(OX - 4.1 * P)}" y="{f(top - 0.4 * P)}" width="{f(19.2 * P)}" '
             f'height="{f(bot - top + 0.8 * P)}" rx="10" class="bb"/>')
    o.append(f'<rect x="{f(OX + 4.55 * P)}" y="{f(top)}" width="{f(1.9 * P)}" height="{f(bot - top)}" class="groove"/>')
    for rail, u in U_STRIP.items():
        cls = "sp" if rail.endswith("+") else "sm"
        o.append(f'<rect x="{f(OX + (u - 0.42) * P)}" y="{f(top)}" width="{f(0.84 * P)}" '
                 f'height="{f(bot - top)}" rx="4" class="{cls}"/>')
        o.append(f'<text x="{f(OX + u * P)}" y="{f(top - 4)}" class="lab {cls}t">{"+" if rail.endswith("+") else MINUS}</text>')
    for r in range(1, z.ROWS + 1):
        y = OY + (r - 1) * P
        o.append(f'<text x="{f(OX - 1.05 * P)}" y="{f(y + 3.5)}" class="num">{r}</text>')
        o.append(f'<text x="{f(OX + 12.05 * P)}" y="{f(y + 3.5)}" class="num">{r}</text>')
        for u in list(U_STRIP.values()) + list(range(5)) + list(range(7, 12)):
            o.append(f'<rect x="{f(OX + u * P - 3)}" y="{f(y - 3)}" width="6" height="6" class="hole"/>')
    for c in z.LEFT + z.RIGHT:
        o.append(f'<text x="{f(OX + gx(c) * P)}" y="{f(top - 4)}" class="lab">{c}</text>')
    flags = []
    for pid, kind, val, pins in z.BOARD_A_DEV:
        o.append(f'<g class="part" data-part="{pid}">')
        o.extend(draw_part(pid, kind, val, pins, flags))
        o.append("</g>")
    o.append('<g id="probes"></g>')
    o.append("</svg>")
    return "\n".join(o)


def dot(x, y, cls="leg"):
    return f'<circle cx="{f(x)}" cy="{f(y)}" r="3.2" class="{cls}"/>'


def draw_part(pid, kind, val, pins, flags):
    o = []
    pts = [p for p in pins.values() if not z.is_ext(p)]
    for p in pts:
        x, y = pos(p)
        o.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="7.5" class="ring"/>')
    if kind == "res":
        (x1, y1), (x2, y2) = pos(pins["1"]), pos(pins["2"])
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        o.append(f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" class="wireleg"/>')
        bw, bh = 0.64 * P, 2.1 * P
        o.append(f'<rect x="{f(mx - bw / 2)}" y="{f(my - bh / 2)}" width="{f(bw)}" height="{f(bh)}" rx="5" class="rbody"/>')
        for i, colour in enumerate(BANDS[val] + ["gold"]):
            t = [0.2, 0.36, 0.52, 0.8][i]
            o.append(f'<rect x="{f(mx - bw / 2)}" y="{f(my - bh / 2 + t * bh - 2)}" width="{f(bw)}" height="4" fill="{BAND_HEX[colour]}"/>')
        o.append(dot(x1, y1))
        o.append(dot(x2, y2))
        o.append(f'<text x="{f(mx + 0.55 * P)}" y="{f(my + 3)}" class="tag">{pid}</text>')
    elif kind == "cer":
        (x1, y1), (x2, y2) = pos(pins["1"]), pos(pins["2"])
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        span = abs(y1 - y2) / P
        cx = mx
        o.append(f'<path d="M{f(x1)} {f(y1)} L{f(cx)} {f(my)} L{f(x2)} {f(y2)}" class="wireleg" fill="none"/>')
        o.append(f'<circle cx="{f(cx)}" cy="{f(my)}" r="{f((0.36 if span < 1.5 else 0.42) * P)}" class="cbody"/>')
        o.append(f'<text x="{f(cx)}" y="{f(my + 2.6)}" class="ctext">{val}</text>')
        o.append(dot(x1, y1))
        o.append(dot(x2, y2))
        o.append(f'<text x="{f(cx + 0.55 * P)}" y="{f(my + 3)}" class="tag">{pid}</text>')
    elif kind == "elec":
        (xp, yp), (xm, ym) = pos(pins["+"]), pos(pins["-"])
        mx, my = (xp + xm) / 2, (yp + ym) / 2
        r = (1.2 if val == "100uF" else 0.86) * P
        o.append(f'<circle cx="{f(mx)}" cy="{f(my)}" r="{f(r)}" class="ebody"/>')
        sy = 1 if ym > yp else -1
        o.append(f'<path d="M{f(mx - r * 0.8)} {f(my + sy * r * 0.6)} A{f(r)} {f(r)} 0 0 {0 if sy > 0 else 1} '
                 f'{f(mx + r * 0.8)} {f(my + sy * r * 0.6)} Z" class="stripe"/>')
        o.append(dot(xp, yp))
        o.append(dot(xm, ym))
        o.append(f'<text x="{f(xp + r + 2)}" y="{f(yp + 4)}" class="pm">+</text>')
        o.append(f'<text x="{f(mx + r + 3)}" y="{f(my + 3)}" class="tag" dx="8">{pid}</text>')
    elif kind == "npn":
        (xe, ye), (_, yc) = pos(pins["E"]), pos(pins["C"])
        o.append(f'<path d="M{f(xe - 0.45 * P)} {f(ye - 0.55 * P)} L{f(xe - 0.45 * P)} {f(yc + 0.55 * P)} '
                 f'C{f(xe + 1.05 * P)} {f(yc + 0.55 * P)} {f(xe + 1.05 * P)} {f(ye - 0.55 * P)} '
                 f'{f(xe - 0.45 * P)} {f(ye - 0.55 * P)} Z" class="tbody"/>')
        for leg, hole in pins.items():
            x, y = pos(hole)
            o.append(dot(x, y, "legl"))
            o.append(f'<text x="{f(x + 7)}" y="{f(y + 3.5)}" class="tleg">{leg}</text>')
        o.append(f'<text x="{f(xe - 0.7 * P)}" y="{f((ye + yc) / 2 + 3)}" class="tag tend">{pid}</text>')
    elif kind == "dip8":
        x1, y1 = pos(pins["1"])
        x8, _ = pos(pins["8"])
        _, y4 = pos(pins["4"])
        o.append(f'<rect x="{f(x1 + 0.25 * P)}" y="{f(y1 - 0.55 * P)}" width="{f(x8 - x1 - 0.5 * P)}" '
                 f'height="{f(y4 - y1 + 1.1 * P)}" rx="3" class="chip"/>')
        o.append(f'<path d="M{f((x1 + x8) / 2 - 7)} {f(y1 - 0.55 * P)} A7 7 0 0 0 {f((x1 + x8) / 2 + 7)} '
                 f'{f(y1 - 0.55 * P)} Z" class="notch"/>')
        for pin, hole in pins.items():
            x, y = pos(hole)
            o.append(dot(x, y, "legl"))
            dx = 9 if int(pin) <= 4 else -9
            o.append(f'<text x="{f(x + dx)}" y="{f(y + 3.5)}" class="pin">{pin}</text>')
        o.append(f'<text x="{f((x1 + x8) / 2)}" y="{f((y1 + y4) / 2 + 4)}" class="chipt">{pid}</text>')
    elif kind == "wire":
        a, b = pins["1"], pins["2"]
        if z.is_ext(a) or z.is_ext(b):
            ext, onb = (a, b) if z.is_ext(a) else (b, a)
            x, y = pos(onb)
            right = (not z.is_rail(onb) and onb[-1] in z.RIGHT) or onb.startswith("R")
            ex = W - 8 - FLAG_W if right else 8
            ey = flag_y(pid)
            tx = ex if right else ex + FLAG_W
            o.append(f'<path d="M{f(x)} {f(y)} C{f((x + tx) / 2)} {f(y)} {f((x + tx) / 2)} {f(ey)} {f(tx)} {f(ey)}" '
                     f'class="wo"/><path d="M{f(x)} {f(y)} C{f((x + tx) / 2)} {f(y)} {f((x + tx) / 2)} {f(ey)} '
                     f'{f(tx)} {f(ey)}" class="w" stroke="{z.WIRE_COLORS[val]}"/>')
            o.append(f'<rect x="{f(ex)}" y="{f(ey - 9)}" width="{FLAG_W}" height="18" rx="9" class="flag"/>')
            o.append(f'<text x="{f(ex + FLAG_W / 2)}" y="{f(ey + 4)}" class="flagt">{EXT_NAME[ext]}</text>')
            o.append(dot(x, y, "end"))
        else:
            (x1, y1), (x2, y2) = pos(a), pos(b)
            if y1 == y2:
                cx, cy = (x1 + x2) / 2, y1 - 0.9 * P
            elif x1 == x2:
                cx, cy = x1 + 0.9 * P, (y1 + y2) / 2
            else:
                cx, cy = (x1 + x2) / 2 + 0.4 * P, (y1 + y2) / 2 - 0.4 * P
            d = f"M{f(x1)} {f(y1)} Q{f(cx)} {f(cy)} {f(x2)} {f(y2)}"
            o.append(f'<path d="{d}" class="wo"/><path d="{d}" class="w" stroke="{z.WIRE_COLORS[val]}"/>')
            o.append(dot(x1, y1, "end"))
            o.append(dot(x2, y2, "end"))
    return o


_flag_slots = {}


def flag_y(wire_id):
    """Label rows for off-board wires, nudged apart so labels never overlap. Keyed by
    wire, since two wires can go to the same off-board pin (two DevEBox grounds)."""
    if not _flag_slots:
        want = []
        for pid, kind, val, pins in z.BOARD_A_DEV:
            if kind == "wire" and any(z.is_ext(p) for p in pins.values()):
                e = [p for p in pins.values() if z.is_ext(p)][0]
                o = [p for p in pins.values() if not z.is_ext(p)][0]
                side = "R" if ((not z.is_rail(o) and o[-1] in z.RIGHT) or o.startswith("R")) else "L"
                want.append((side, pos(o)[1], pid))
        for side in "LR":
            last = -1e9
            for _, y, wid in sorted(w for w in want if w[0] == side):
                y = max(y, last + 21)
                _flag_slots[wid] = y
                last = y
    return _flag_slots[wire_id]


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def h(pt):
    if z.is_rail(pt):
        rail, row = pt.split("@")
        return f"{STRIP[rail]} beside row {row}"
    return f'<b class="hl">{pt}</b>'


def part_step(pid):
    _, kind, val, pins = PARTS[pid]
    if kind == "res":
        a, b = pins["1"], pins["2"]
        top, bottom = sorted((a, b), key=lambda p: int(p[:-1]))
        bands = ", ".join(BANDS[val] + ["gold"])
        return (f"{pid} {OHMS[val]} Resistor",
                f"<p>Bands {bands}, read from either end.</p>"
                f"<p>Bend both legs down right at the body. Lay it flat along column {top[-1]} "
                f"with one leg in {h(top)} and the other in {h(bottom)}, pressed against the board.</p>")
    if kind == "cer":
        a, b = sorted(pins.values(), key=lambda p: int(p[:-1]))
        span = abs(int(a[:-1]) - int(b[:-1]))
        fit = {1: "The holes are side by side, so pinch the legs together if they sit wider apart.",
               2: "The holes are two apart, which matches most of these capacitors as they come."}.get(
            span, "Spread the legs to reach; they are long enough.")
        return (f"{pid} Ceramic Capacitor {val}",
                f"<p>The small disc printed <b>{val}</b>. Legs in {h(a)} and {h(b)}. {fit}</p>")
    if kind == "elec":
        return (f"{pid} {CAP[val]} Capacitor",
                f"<p>{CAP[val]} electrolytic. The long leg is plus (+) and goes in {h(pins['+'])}. "
                f"The short leg, on the side with the stripe, goes in {h(pins['-'])}. It stands upright.</p>"
                "<p>Getting this one the right way round matters: a reversed electrolytic shorts the supply.</p>")
    if kind == "npn":
        return (f"{pid} 2N3904 Transistor",
                f"<p>Hold it with the flat face toward the left strips and the legs straight down. "
                f"E (emitter) in {h(pins['E'])}, B (base) in {h(pins['B'])}, C (collector) in {h(pins['C'])}.</p>"
                "<p>Push it down so the legs are short. No extension wires on it: long wires here pick up "
                "mains hum. This is the noise source; you will check it later by measuring row 19.</p>")
    if kind == "dip8":
        return (f"{pid} LM358 Op-Amp",
                "<p>The notch (or dot) points to row 1. The chip sits across the middle groove: "
                f"pin 1 in {h(pins['1'])}, pins 1 to 4 down column e to {h(pins['4'])}, "
                f"and pins 5 to 8 up column f from {h(pins['5'])} to {h(pins['8'])}.</p>"
                "<p>Line all eight legs up over their holes before you press, then check that no leg folded under.</p>")
    a, b = pins["1"], pins["2"]
    if z.is_ext(a) or z.is_ext(b):
        ext, onb = (a, b) if z.is_ext(a) else (b, a)
        dev, pin = ext.split(":")
        pin = pin.replace("-", "\u2212")
        if dev == "DEV" and pid == "W26":
            what = f"A female-to-male wire from a second GND pin on the DevEBox to {h(onb)}."
        elif dev == "DEV":
            what = f"A female-to-male wire from the DevEBox's {pin} pin to {h(onb)}."
        else:
            what = f"Plug the boost module's {pin} wire into {h(onb)}."
        why = {"DEV:PA4": " This carries the noise signal to the ADC.",
               "DEV:PB0": " This lets the firmware watch the bias supply.",
               "MT3608:VOUT+": " This is the raw boost output; R16 and R17 halve it for row 27.",
               "DEV:3V3": " This feeds the 1.65 V reference."}.get(ext, "")
        return (f"{pid} {EXT_NAME[ext]}", f"<p>{what}{why}</p>")
    notes = {"W1": " It carries the 1.65 V reference from row 6 down to row 13.",
             "W4": " It joins the op-amp's output in row 15 to row 12.",
             "W6": " This powers the chip.",
             "W7": " Together with W9 it parks the chip's unused half.",
             "W8": " This grounds the chip.",
             "W9": " Row 19 on the right is ground, so this grounds pin 5.",
             "W11": " This grounds the transistor's base.",
             "W13": " It takes the bias supply across the groove to the monitor resistor R9.",
             "W25": " This grounds R17, the lower half of the bias divider.",
             "W14": " The two red strips are now one 5 V supply.",
             "W15": " The two blue strips are now one ground."}
    colour = val
    if z.is_rail(a) and z.is_rail(b):
        text = f"<p>A {colour} wire from {h(a)} to {h(b)}.{notes.get(pid, '')}</p>"
        return (f"{pid} {colour.title()} Strip Link", text)
    text = f"<p>A {colour} wire from {h(a)} to {h(b)}.{notes.get(pid, '')}</p>"
    target = "Blue Strip" if (z.is_rail(b) and b.startswith(("L-", "R-"))) else (
        "Red Strip" if z.is_rail(b) else f"{b}")
    return (f"{pid} {colour.title()} Wire, {a} to {target}", text)


def _free_hole(pt):
    if z.is_rail(pt):
        return
    used = {q for _, _, _, pins in z.BOARD_A_DEV for q in pins.values()}
    covered = set().union(*(z.covered_holes(k, pins) for _, k, _, pins in z.BOARD_A_DEV))
    assert pt not in used and pt not in covered, f"probe hole {pt} is not free"
    r, c = int(pt[:-1]), gx(pt[-1])
    for pid, kind, val, pins in z.BOARD_A_DEV:
        if kind == "elec":
            (ra, ca), (rb, cb) = [(int(q[:-1]), gx(q[-1])) for q in pins.values()]
            rad = 1.3 if val == "100uF" else 1.0
            d = ((r - (ra + rb) / 2) ** 2 + (c - (ca + cb) / 2) ** 2) ** 0.5
            assert d >= rad + 0.5, f"probe hole {pt} is against {pid}"


def probes(red, black="L-@24"):
    _free_hole(red)
    _free_hole(black)
    return [{"at": list(pos(red)), "c": "red"}, {"at": list(pos(black)), "c": "black"}]


def steps():
    out = []
    counts = {}
    for pid, kind, val, _ in z.BOARD_A_DEV:
        if kind == "wire":
            continue
        key = (f"{OHMS[val]} resistors" if kind == "res" else f"ceramic {val}" if kind == "cer" else
               f"{CAP[val]} electrolytic" if kind == "elec" else
               "2N3904 (bring the spares)" if kind == "npn" else "LM358")
        counts[key] = counts.get(key, 0) + 1
    nwires = sum(1 for p in z.BOARD_A_DEV if p[1] == "wire" and not any(z.is_ext(x) for x in p[3].values()))
    kit = "".join(f"<li>{k}: {v}</li>" for k, v in counts.items())
    out.append({"stage": "Before You Start", "title": "Get the Bench Ready", "parts": [], "probes": [], "html":
        "<ul><li>Unplug every USB cable. Unplug again before every change to the board, however small.</li>"
        "<li>Unplug the laptop charger. It adds mains hum to the noise.</li>"
        "<li>Turn the new breadboard so row 1 is at the top and the letters a to j read from left to right. "
        "Find the red (+) and blue (\u2212) strip on each side. If your board has them the other way round, "
        "follow the colours, not the drawing.</li>"
        "<li>We use rows 1 to 32. If a strip's coloured line has a gap part-way down, the strip is split there; W26 joins the left blue strip's two pieces, and the beep checks prove the rest.</li>"
        "<li>Board B (OLED and ATECC608) stays as it is, still wired to the DevEBox.</li>"
        "<li>The CP2102 stays wired TXD to PA10, RXD to PA9 and GND to GND. Its 5V wire stays disconnected.</li></ul>"
        f"<p>Lay these out first:</p><ul class=\"kit\">{kit}<li>short jumper wires: {nwires}</li>"
        "<li>female-to-male wires for the DevEBox: 5</li></ul>"})
    stage_of = {"res": "Flat Parts, Top to Bottom", "cer": "Flat Parts, Top to Bottom",
                "dip8": "The Chip and the Transistor", "npn": "The Chip and the Transistor",
                "elec": "Electrolytic Capacitors"}
    for pid, kind, val, pins in z.BOARD_A_DEV:
        if kind == "wire":
            ext = any(z.is_ext(x) for x in pins.values())
            stage = ("DevEBox Wires" if any(x.startswith("DEV:") for x in pins.values()) else
                     "Boost Module Wires" if ext else "Short Wires on the Board")
        else:
            stage = stage_of[kind]
        title, text = part_step(pid)
        out.append({"stage": stage, "title": title, "html": text, "parts": [pid], "probes": []})
    out.append({"stage": "DevEBox Wires", "title": "Take One Photo", "parts": [], "probes": [], "html":
        "<p>Still unplugged: take one clear photo of the whole breadboard from straight above, in good light, "
        "with the DevEBox and the boost module in the picture.</p>"})
    beep = [
        ("L+@5", "L-@5", "Left Red to Left Blue", "No steady beep. A beep here means 5 V is shorted to ground."),
        ("R+@5", "R-@5", "Right Red to Right Blue", "No steady beep."),
        ("R-@24", "L-@24", "Left Blue to Right Blue", "Beep. This proves W15 joins the two grounds."),
        ("R+@24", "L+@24", "Left Red to Right Red", "Beep. This proves W14 joins the two 5 V strips."),
        ("L-@2", "L-@32", "Left Blue, Top to Bottom", "Beep. Some full-size boards split each strip part-way down; "
         "on this one W26 joins the two pieces of the left blue strip."),
        ("31b", "L-@26", "Row 31 to Blue", "No beep. Row 31 is the raw boost output."),
        ("27b", "L-@25", "Row 27 to Blue", "No steady beep. Row 27 is the bias supply. It reaches ground only "
         "through the 10 k\u03a9 R17, which a meter does not beep for."),
        ("2c", "L-@2", "Row 2 to Blue", "No beep. Row 2 is 3.3 V."),
        ("19a", "L-@19", "Row 19 to Blue", "No steady beep. Row 19 is the transistor's emitter."),
        ("18b", "L-@18", "Row 18 to Blue", "Beep. The chip's ground pin is connected."),
        ("6c", "13d", "Row 6 to Row 13", "Beep. This proves W1, the reference link."),
    ]
    for red, black, title, text in beep:
        out.append({"stage": "Check with the Power Off", "title": title, "parts": [],
                    "probes": probes(red, black),
                    "html": f"<p>Meter on the beep setting. Red probe on {h(red)}, black probe on {h(black)}.</p>"
                            f"<p><b>Expect</b> {text}</p>"})
    out.append({"stage": "Check with the Power Off", "title": "Send Checkpoint 1", "parts": [], "probes": [],
                "html": "<p>Send me the photo and say which checks, if any, did not match. "
                        "Wait for my OK before plugging anything in.</p>"})
    volts = [
        ("L+@6", "Left Red Strip, 5 V", "4.6 to 5.2 V", "the DevEBox's 5 V. If it is near 0, unplug and check W20 and W21."),
        ("31b", "Row 31, Boost Output", "about 24 to 28 V", "the raw MT3608 output. On this build neither of two "
         "modules would regulate below about 26 V whatever the trimmer did, which is why R16 and R17 halve it."),
        ("27b", "Row 27, Bias Supply", "about half of row 31, between 12 and 15 V", "the supply for the transistor and "
         "the monitor. This build measured 13.9 V. The firmware accepts 9 to 20 V."),
        ("23f", "Row 23 Right, Monitor", "about one eleventh of row 27, near 1.3 V", "the monitor that PB0 reads."),
        ("2c", "Row 2, 3.3 V", "3.2 to 3.4 V", "the DevEBox's 3.3 V."),
        ("6c", "Row 6, Reference", "1.6 to 1.7 V", "the reference, half of 3.3 V."),
        ("17b", "Row 17, Op-Amp Input", "the same as row 6", "the op-amp's input, held at the reference by R3."),
        ("12e", "Row 12, Op-Amp Output", "1.5 to 1.8 V", "the op-amp's output."),
        ("8e", "Row 8, Signal to PA4", "the same as row 12", "the signal going to PA4."),
        ("19a", "Row 19, the Transistor", "7 to 9 V", "the voltage where the transistor's junction breaks down and makes noise."),
    ]
    for red, title, expect, what in volts:
        out.append({"stage": "Power On and Measure", "title": title, "parts": [], "probes": probes(red),
                    "html": f"<p>Meter on DC 20 V. Red probe on {h(red)}, black probe in the left blue strip.</p>"
                            f"<p><b>Expect</b> {expect}. This is {what}</p>"})
    out[-1]["html"] += ("<p>If row 19 reads below 6 V, this transistor breaks down too low to make good noise. "
                        "Unplug the USB-C, swap in the next 2N3904 in the same holes and the same way round, "
                        "plug in and measure again. Write down each reading. If every one reads below 6 V, "
                        "use the zener in the next step.</p>")
    out.append({"stage": "Power On and Measure", "title": "Zener Backup", "parts": [],
                "probes": probes("19a"), "html":
        "<p>Unplug the USB-C first. Take Q1 out. Fold the 1N4739A's plain-end leg back down alongside "
        f"its body so both legs point down side by side. Band end in {h('19c')}, plain end in {h('20c')}.</p>"
        "<p>Plug in and measure row 19 again. <b>Expect</b> about 8 to 9.5 V.</p>"})
    cmds = [
        (".\\zirah info", "TRNG OK, and the line BOARD devebox noise=PA4 serial=PA9/PA10 hse_mhz=8."),
        (".\\zirah vbias", "about 12 to 15 V. This build read 13.9 V."),
        (".\\zirah health", "TRNG OK."),
        (".\\zirah analyze", "MEAN near 2000 (1350 to 2750 passes the start-up check), STD about 20 or more, "
                            "AUTOCORR1 below 0.4, CLIPPED 0. This build showed MEAN 2033, STD 88.0, AUTOCORR1 \u22120.002."),
    ]
    for cmd, expect in cmds:
        out.append({"stage": "Software Checks", "title": cmd.replace(".\\zirah ", "Run ").title(), "parts": [],
                    "probes": [], "html": f"<p>In PowerShell, in D:\\zirah:</p><pre><code>{html.escape(cmd)}</code></pre>"
                                          f"<p><b>Expect</b> {expect}</p>"})
    out.append({"stage": "Software Checks", "title": "Send Checkpoint 2", "parts": [], "probes": [], "html":
        "<p>In one message: the readings from Power On and Measure (just list any that were off), "
        "and the full output of the four commands. Then we make the key.</p>"})
    return out


STAGE_NOTES = {
    "Flat Parts, Top to Bottom": "Resistors and the small ceramic capacitors go in first because they lie low. "
                                 "Every one runs straight down one column.",
    "The Chip and the Transistor": "These stand taller, so they go in after the flat parts.",
    "Electrolytic Capacitors": "Each one stands upright in two neighbouring holes. Long leg is plus (+); "
                               "the stripe on the can marks the minus leg.",
    "Short Wires on the Board": "Colours are suggestions. Black for every ground wire keeps the photo easy to check.",
    "Boost Module Wires": "The MT3608 sits off the board on its four wires. Its VOUT+ goes to row 31, and R16 "
                          "and R17 halve it for row 27.",
    "DevEBox Wires": "The DevEBox stays unplugged. Its pin names are printed on the underside.",
    "Check with the Power Off": "Everything unplugged. Push a jumper pin into a free hole of the named row "
                                "and touch the probe to it. A short chirp that stops at once is a capacitor "
                                "charging and counts as no beep.",
    "Power On and Measure": "Plug the USB-C cable into the DevEBox. The CP2102 stays on PA9, PA10 and GND for the "
                            "serial link, with its 5V wire out. Take each measuring jumper out again afterwards: "
                            "a loose wire left in rows 16 to 19 picks up hum.",
    "Software Checks": "Close the Zirah app first so the serial port is free.",
}


def page():
    data = steps()
    for i, s in enumerate(data, 1):
        s["n"] = i
    css = (Path(__file__).with_name("build_guide.css")).read_text()
    js = (Path(__file__).with_name("build_guide.js")).read_text()
    return f"""<title>Zirah Board A Build</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&family=JetBrains+Mono:wght@500&display=swap">
<style>
{css}
</style>
<header class="top">
  <div class="wrap">
    <h1>Zirah Board A Build</h1>
    <p class="sub">Source A on the DevEBox as built, one part at a time, from row 1 down. Every hole here
    comes from the project's layout script after its netlist check passed.</p>
    <div class="bar" aria-hidden="true"><span id="barfill"></span></div>
  </div>
</header>
<main class="wrap bench">
  <section class="now" aria-live="polite">
    <p class="where"><span id="count"></span><span id="stage"></span></p>
    <h2 id="title"></h2>
    <p class="note" id="note"></p>
    <div id="body" class="body"></div>
    <div class="nav">
      <button id="prev" type="button">Back</button>
      <button id="next" type="button" class="go">Next</button>
    </div>
  </section>
  <section class="drawing">{board_svg()}</section>
  <nav class="list" id="list" aria-label="All steps"></nav>
</main>
<script>
const STEPS = {json.dumps(data, ensure_ascii=False)};
const NOTES = {json.dumps(STAGE_NOTES, ensure_ascii=False)};
{js}
</script>
"""


def main():
    z.check(z.BOARD_A_DEV, z.NETS_A_DEV, "Board A (DevEBox)")
    z.check_build(z.BOARD_A_DEV, z.NETS_A_DEV, "Board A (DevEBox)", z.QUIET_NODES_DEV)
    OUT.write_text(page())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
