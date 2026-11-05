#!/usr/bin/env python3
"""Single source of truth for Zirah's breadboard layout.

Running this script:
  1. checks that the hole-by-hole layout really produces the circuit netlist
     (every connection present, no accidental shorts, no hole used twice),
  2. draws hardware/breadboard_A.svg, hardware/breadboard_A_dual.svg (Board A
     with the optional second noise source) and hardware/breadboard_B.svg for
     the Discovery, and hardware/breadboard_A_devebox.svg (as built) and
     hardware/breadboard_B_devebox.svg for the DevEBox,
  3. writes hardware/WIRING_TABLE.md.

Geometry used everywhere (hold the board this way when building):
  portrait, row 1 at the TOP, rows 1..30 going down; holes a b c d e on the
  LEFT of the centre groove, f g h i j on the RIGHT. In each row, a..e are
  connected together and f..j are connected together. The four long rails:
  "L+" and "L-" run down the left edge, "R+" and "R-" down the right edge.
"""
from pathlib import Path

ROWS = 32
LEFT = "abcde"
RIGHT = "fghij"
OUT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Board A: entropy source (analog). Each part: id, type, value, {pin: hole}.
# Holes are like "6c"; rails "L+", "L-", "R+", "R-"; off-board points are
# "DISCO:<pin>" (STM32F4DISCOVERY header) or "MT3608:<pin>".
# --------------------------------------------------------------------------

BOARD_A = [
    # power and signals from the Discovery board and the boost module
    ("W1", "wire", "red", {"1": "DISCO:5V", "2": "L+@1"}),
    ("W2", "wire", "black", {"1": "DISCO:GND", "2": "L-@1"}),
    ("W3", "wire", "red", {"1": "L+@30", "2": "R+@30"}),
    ("W4", "wire", "black", {"1": "L-@30", "2": "R-@30"}),
    ("W5", "wire", "red", {"1": "L+@3", "2": "MT3608:VIN+"}),
    ("W6", "wire", "black", {"1": "L-@3", "2": "MT3608:VIN-"}),
    ("W7", "wire", "orange", {"1": "MT3608:VOUT+", "2": "2a"}),
    ("W8", "wire", "black", {"1": "MT3608:VOUT-", "2": "L-@2"}),
    ("W9", "wire", "orange", {"1": "2e", "2": "2f"}),
    ("W10", "wire", "yellow", {"1": "7g", "2": "DISCO:PB0"}),
    ("W11", "wire", "black", {"1": "12b", "2": "L-@12"}),
    ("W12", "wire", "green", {"1": "15a", "2": "26a"}),
    ("W13", "wire", "purple", {"1": "DISCO:3V", "2": "23a"}),
    ("W14", "wire", "blue", {"1": "24a", "2": "20a"}),
    ("W15", "wire", "white", {"1": "16a", "2": "DISCO:PA1"}),
    ("W16", "wire", "black", {"1": "27a", "2": "L-@27"}),
    ("W17", "wire", "red", {"1": "24j", "2": "R+@23"}),
    ("W18", "wire", "gray", {"1": "25g", "2": "26g"}),
    ("W19", "wire", "black", {"1": "27j", "2": "R-@27"}),
    # boost filter and voltage monitor
    ("R1", "res", "1k", {"1": "2c", "2": "6c"}),
    ("C1", "elec", "100uF", {"+": "6a", "-": "L-@7"}),
    ("C2", "cer", "104", {"1": "6b", "2": "L-@5"}),
    ("R9", "res", "1M", {"1": "2h", "2": "7h"}),
    ("R10", "res", "100k", {"1": "7i", "2": "R-@6"}),
    ("C7", "cer", "104", {"1": "7j", "2": "R-@8"}),
    # avalanche junction
    ("R2", "res", "100k", {"1": "6d", "2": "11d"}),
    ("Q1", "npn", "2N3904", {"E": "11a", "B": "12a", "C": "13a"}),
    ("C3", "cer", "104", {"1": "11c", "2": "15c"}),
    # bias reference for the op-amp input
    ("R3", "res", "100k", {"1": "15d", "2": "19d"}),
    ("R4", "res", "100k", {"1": "23b", "2": "19b"}),
    ("R5", "res", "100k", {"1": "19c", "2": "L-@20"}),
    ("C4", "elec", "10uF", {"+": "19a", "-": "L-@18"}),
    # amplifier (LM358, gain 11)
    ("U1", "dip8", "LM358", {"1": "24e", "2": "25e", "3": "26e", "4": "27e",
                             "5": "27f", "6": "26f", "7": "25f", "8": "24f"}),
    ("R6", "res", "10k", {"1": "25b", "2": "29b"}),
    ("C5", "elec", "10uF", {"+": "29a", "-": "L-@29"}),
    ("R7", "res", "100k", {"1": "20c", "2": "25c"}),
    ("R8", "res", "1k", {"1": "20e", "2": "16e"}),
    ("C6", "cer", "101", {"1": "16b", "2": "L-@16"}),
    ("C8", "cer", "104", {"1": "24h", "2": "R-@24"}),
    ("C9", "elec", "10uF", {"+": "24i", "-": "R-@25"}),
]

NETS_A = {
    "5V": {"W1.1", "C9.+", "C8.1", "U1.8", "W5.2"},
    "GND": {"W2.1", "W6.2", "W8.1", "C1.-", "C2.2", "R10.2", "C7.2", "Q1.B", "R5.2",
            "C4.-", "U1.4", "U1.5", "C5.-", "C6.2", "C8.2", "C9.-"},
    "3V": {"W13.1", "R4.1"},
    "HV": {"W7.1", "R1.1", "R9.1"},
    "HVF": {"R1.2", "C1.+", "C2.1", "R2.1"},
    "MON": {"R9.2", "R10.1", "C7.1", "W10.2"},
    "E": {"R2.2", "Q1.E", "C3.1"},
    "Q1C": {"Q1.C"},
    "INP": {"C3.2", "R3.1", "U1.3"},
    "VREF": {"R3.2", "R4.2", "R5.1", "C4.+"},
    "INN": {"U1.2", "R6.1", "R7.2"},
    "RG": {"R6.2", "C5.+"},
    "OUT": {"U1.1", "R7.1", "R8.1"},
    "ADC": {"R8.2", "C6.1", "W15.2"},
    "OUTB": {"U1.7", "U1.6"},
}

# --------------------------------------------------------------------------
# Board A with the optional second noise source (source B) on the right half:
# a second 2N3904 junction fed from the filtered 12 V, the unused half B of
# the LM358 as its amplifier (same gain of 11), and its output to PB1. The
# two wires that parked half B (W18, W19) come out.
# --------------------------------------------------------------------------

SOURCE_B = [
    ("W20", "wire", "orange", {"1": "6e", "2": "6f"}),
    ("W21", "wire", "black", {"1": "11i", "2": "R-@11"}),
    ("W22", "wire", "purple", {"1": "19e", "2": "19f"}),
    ("W23", "wire", "green", {"1": "14j", "2": "27g"}),
    ("W24", "wire", "blue", {"1": "25j", "2": "22j"}),
    ("W25", "wire", "white", {"1": "17j", "2": "DISCO:PB1"}),
    ("R11", "res", "100k", {"1": "6g", "2": "10g"}),
    ("Q2", "npn", "2N3904", {"E": "10j", "B": "11j", "C": "12j"}),
    ("C10", "cer", "104", {"1": "10h", "2": "14h"}),
    ("R12", "res", "100k", {"1": "14g", "2": "19g"}),
    ("R13", "res", "100k", {"1": "22h", "2": "26h"}),
    ("R15", "res", "10k", {"1": "26i", "2": "29i"}),
    ("C12", "elec", "10uF", {"+": "29j", "-": "R-@29"}),
    ("R14", "res", "1k", {"1": "22g", "2": "17g"}),
    ("C11", "cer", "101", {"1": "17h", "2": "R-@17"}),
]

BOARD_A_DUAL = [p for p in BOARD_A if p[0] not in ("W18", "W19")] + SOURCE_B

NETS_A_DUAL = {k: set(v) for k, v in NETS_A.items() if k != "OUTB"}
NETS_A_DUAL["GND"] = (NETS_A["GND"] - {"U1.5"}) | {"Q2.B", "C12.-", "C11.2"}
NETS_A_DUAL["HVF"] = NETS_A["HVF"] | {"R11.1"}
NETS_A_DUAL["VREF"] = NETS_A["VREF"] | {"R12.2"}
NETS_A_DUAL.update({
    "EB": {"R11.2", "Q2.E", "C10.1"},
    "Q2C": {"Q2.C"},
    "INPB": {"C10.2", "R12.1", "U1.5"},
    "INNB": {"U1.6", "R13.2", "R15.1"},
    "RGB": {"R15.2", "C12.+"},
    "OUTB": {"U1.7", "R13.1", "R14.1"},
    "ADCB": {"R14.2", "C11.1", "W25.2"},
})

# --------------------------------------------------------------------------
# Board B: I2C bus (ATECC608 + OLED). Each module is plugged in with its four
# header pins in column j and its body hanging off the right edge of the
# board. Pin ORDER differs between sellers: the picture assumes
# GND, VCC, SCL, SDA from the top, but you must follow the labels printed on
# your module (see WIRING.md).
# --------------------------------------------------------------------------

BOARD_B = [
    ("W1", "wire", "purple", {"1": "DISCO:3V", "2": "L+@1"}),
    ("W2", "wire", "black", {"1": "DISCO:GND", "2": "L-@1"}),
    ("W3", "wire", "green", {"1": "DISCO:PA8", "2": "10a"}),
    ("W4", "wire", "blue", {"1": "DISCO:PC9", "2": "14a"}),
    ("RP1", "res", "4.7k", {"1": "10b", "2": "L+@10"}),
    ("RP2", "res", "4.7k", {"1": "14b", "2": "L+@14"}),
    ("OLED", "module", "OLED", {"GND": "3j", "VCC": "4j", "SCL": "5j", "SDA": "6j"}),
    ("ATECC", "module", "ATECC608", {"GND": "20j", "VCC": "21j", "SCL": "22j", "SDA": "23j"}),
    ("W5", "wire", "black", {"1": "3g", "2": "L-@3"}),
    ("W6", "wire", "purple", {"1": "4g", "2": "L+@4"}),
    ("W7", "wire", "green", {"1": "5g", "2": "10c"}),
    ("W8", "wire", "blue", {"1": "6g", "2": "14c"}),
    ("W9", "wire", "black", {"1": "20g", "2": "L-@20"}),
    ("W10", "wire", "purple", {"1": "21g", "2": "L+@21"}),
    ("W11", "wire", "green", {"1": "22g", "2": "10d"}),
    ("W12", "wire", "blue", {"1": "23g", "2": "14d"}),
]

NETS_B = {
    "3V": {"W1.1", "RP1.2", "RP2.2", "OLED.VCC", "ATECC.VCC"},
    "GND": {"W2.1", "OLED.GND", "ATECC.GND"},
    "SCL": {"W3.1", "RP1.1", "OLED.SCL", "ATECC.SCL"},
    "SDA": {"W4.1", "RP2.1", "OLED.SDA", "ATECC.SDA"},
}

# Board B on the DevEBox: the same holes, with the DevEBox header pins (3V3
# instead of the Discovery's 3V). The netlist is the same.
_DEV_PIN = {"DISCO:3V": "DEV:3V3", "DISCO:GND": "DEV:GND", "DISCO:PA8": "DEV:PA8", "DISCO:PC9": "DEV:PC9"}
BOARD_B_DEV = [(pid, kind, val, {k: _DEV_PIN.get(v, v) for k, v in pins.items()})
               for pid, kind, val, pins in BOARD_B]


# --------------------------------------------------------------------------
# Board A for the DevEBox STM32F4XX_M board on a full-size breadboard (source
# A only, rows 2 to 30). Laid out so that it can be built in one clean pass
# from the top of the board to the bottom:
#   * every resistor lies flat along one column over four or five rows, and
#     no hole under a flat part is needed by anything else;
#   * every electrolytic capacitor stands in two neighbouring holes of one
#     column, its minus row joined to the minus strip by a short wire, so
#     short legs are never a problem, and no wire goes in beside it;
#   * only wires go into the long strips;
#   * the emitter row, the op-amp input row and the inverting-input row carry
#     no wire at all, which keeps mains hum out of the high-impedance nodes.
# Part ids follow the circuit (R1 to R10, C1 to C9); wire ids follow the
# build order. Off-board points "DEV:<pin>" are the DevEBox header pins.
# --------------------------------------------------------------------------

BOARD_A_DEV = [
    # flat parts, top to bottom
    ("R4", "res", "100k", {"1": "2b", "2": "6b"}),
    ("R5", "res", "100k", {"1": "6d", "2": "10d"}),
    ("C6", "cer", "101", {"1": "8c", "2": "10c"}),
    ("R8", "res", "1k", {"1": "12a", "2": "8a"}),
    ("R6", "res", "10k", {"1": "16b", "2": "11b"}),
    ("R7", "res", "100k", {"1": "12c", "2": "16c"}),
    ("R3", "res", "100k", {"1": "17a", "2": "13a"}),
    ("C8", "cer", "104", {"1": "15g", "2": "14g"}),
    ("C3", "cer", "104", {"1": "19d", "2": "17d"}),
    ("R10", "res", "10k", {"1": "23h", "2": "19h"}),
    ("C7", "cer", "104", {"1": "23i", "2": "19i"}),
    ("R2", "res", "100k", {"1": "23b", "2": "19b"}),
    ("C2", "cer", "104", {"1": "23c", "2": "22c"}),
    ("R1", "res", "1k", {"1": "27a", "2": "23a"}),
    ("R9", "res", "100k", {"1": "27g", "2": "23g"}),
    # bias divider, added on the bench: the MT3608 modules would not regulate below about
    # 26 V, so R16 and R17 halve the boost output to about 14 V at row 27
    ("R17", "res", "10k", {"1": "27c", "2": "30c"}),
    ("R16", "res", "10k", {"1": "31d", "2": "27d"}),
    # taller parts
    ("U1", "dip8", "LM358", {"1": "15e", "2": "16e", "3": "17e", "4": "18e",
                             "5": "18f", "6": "17f", "7": "16f", "8": "15f"}),
    ("Q1", "npn", "2N3904", {"E": "19c", "B": "20c", "C": "21c"}),
    # electrolytic capacitors
    ("C4", "elec", "10uF", {"+": "6e", "-": "7e"}),
    ("C5", "elec", "10uF", {"+": "11e", "-": "10e"}),
    ("C9", "elec", "10uF", {"+": "15h", "-": "14h"}),
    ("C1", "elec", "100uF", {"+": "23e", "-": "22e"}),
    # short wires on the breadboard
    ("W1", "wire", "blue", {"1": "6a", "2": "13e"}),
    ("W2", "wire", "black", {"1": "7a", "2": "L-@7"}),
    ("W3", "wire", "black", {"1": "10b", "2": "L-@10"}),
    ("W4", "wire", "green", {"1": "12d", "2": "15d"}),
    ("W5", "wire", "black", {"1": "14j", "2": "R-@27"}),
    ("W6", "wire", "red", {"1": "15j", "2": "R+@15"}),
    ("W7", "wire", "gray", {"1": "16g", "2": "17g"}),
    ("W8", "wire", "black", {"1": "18a", "2": "L-@18"}),
    ("W9", "wire", "gray", {"1": "18g", "2": "19g"}),
    ("W10", "wire", "black", {"1": "19j", "2": "R-@26"}),
    ("W11", "wire", "black", {"1": "20a", "2": "L-@20"}),
    ("W12", "wire", "black", {"1": "22a", "2": "L-@22"}),
    ("W13", "wire", "orange", {"1": "27e", "2": "27f"}),
    ("W14", "wire", "red", {"1": "L+@30", "2": "R+@30"}),
    ("W15", "wire", "black", {"1": "L-@30", "2": "R-@30"}),
    # long wires: the boost module, then the DevEBox (power unplugged)
    ("W16", "wire", "red", {"1": "L+@29", "2": "MT3608:VIN+"}),
    ("W17", "wire", "black", {"1": "L-@29", "2": "MT3608:VIN-"}),
    ("W18", "wire", "black", {"1": "MT3608:VOUT-", "2": "L-@28"}),
    ("W19", "wire", "orange", {"1": "MT3608:VOUT+", "2": "31a"}),
    ("W20", "wire", "red", {"1": "DEV:5V", "2": "L+@1"}),
    ("W21", "wire", "black", {"1": "DEV:GND", "2": "L-@1"}),
    ("W22", "wire", "purple", {"1": "DEV:3V3", "2": "2a"}),
    ("W23", "wire", "white", {"1": "8b", "2": "DEV:PA4"}),
    ("W24", "wire", "yellow", {"1": "23j", "2": "DEV:PB0"}),
    # added on the bench: the ground for R17, and a second DevEBox ground to the bottom of the left
    # blue strip, which on this breadboard is split part-way down
    ("W25", "wire", "black", {"1": "30a", "2": "L-@31"}),
    ("W26", "wire", "black", {"1": "DEV:GND", "2": "L-@32"}),
]

NETS_A_DEV = {
    "5V": {"W20.1", "W16.2", "U1.8", "C8.1", "C9.+"},
    "GND": {"W21.1", "W17.2", "W18.1", "C4.-", "R5.2", "C6.2", "C5.-", "C8.2", "C9.-",
            "U1.4", "U1.5", "Q1.B", "C2.2", "C1.-", "R10.2", "C7.2", "R17.2"},
    "3V3": {"W22.1", "R4.1"},
    "VREF": {"R4.2", "R5.1", "C4.+", "R3.2"},
    "ADC": {"R8.2", "C6.1", "W23.2"},
    "RG": {"R6.2", "C5.+"},
    "OUT": {"U1.1", "R8.1", "R7.1"},
    "INN": {"U1.2", "R6.1", "R7.2"},
    "INP": {"U1.3", "R3.1", "C3.2"},
    "OUTB": {"U1.7", "U1.6"},
    "E": {"R2.2", "Q1.E", "C3.1"},
    "Q1C": {"Q1.C"},
    "HVF": {"R2.1", "C2.1", "R1.2", "C1.+"},
    "BOOST": {"W19.1", "R16.1"},
    "HV": {"R16.2", "R17.1", "R1.1", "R9.1"},
    "MON": {"R9.2", "R10.1", "C7.1", "W24.2"},
}

# Nodes that must carry no wire, so that no length of wire picks up hum.
QUIET_NODES_DEV = ("E", "INP", "INN")


# --------------------------------------------------------------------------
# Connectivity check
# --------------------------------------------------------------------------

RAILS = ("L+", "L-", "R+", "R-")


def is_rail(pt):
    return pt.split("@")[0] in RAILS


def is_ext(pt):
    return ":" in pt


def node_of(point):
    """Electrical node a hole/rail/off-board point belongs to."""
    if is_ext(point):
        return point
    if is_rail(point):
        rail, row = point.split("@")
        assert 1 <= int(row) <= ROWS, point
        return rail
    row, col = int(point[:-1]), point[-1]
    assert 1 <= row <= ROWS and col in LEFT + RIGHT, point
    return f"row{row}{'L' if col in LEFT else 'R'}"


def check(parts, nets, name):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    used = {}
    pin_node = {}
    for pid, kind, _, pins in parts:
        for pin, pt in pins.items():
            if not is_ext(pt):
                assert pt not in used, f"{name}: hole {pt} used twice ({used[pt]} and {pid}.{pin})"
                used[pt] = f"{pid}.{pin}"
            pin_node[f"{pid}.{pin}"] = node_of(pt)
        if kind == "wire":
            a, b = pins.values()
            union(node_of(a), node_of(b))
    for n in pin_node.values():
        find(n)

    def group(p):
        return find(pin_node[p])

    errors = []
    for net, members in nets.items():
        roots = {group(m) for m in members}
        if len(roots) != 1:
            errors.append(f"net {net} is split: " + ", ".join(sorted(members)))
    seen = {}
    for net, members in nets.items():
        r = group(next(iter(members)))
        if r in seen:
            errors.append(f"SHORT between nets {seen[r]} and {net}")
        seen[r] = net
    declared = set().union(*nets.values())
    for pid, kind, _, pins in parts:
        if kind == "wire":
            continue
        for pin in pins:
            if f"{pid}.{pin}" not in declared:
                errors.append(f"{pid}.{pin} is not in any net")
    if errors:
        raise SystemExit(f"{name} layout check FAILED:\n  " + "\n  ".join(errors))
    print(f"{name}: layout check passed ({len(parts)} parts, {len(nets)} nets, "
          f"{len(used)} holes used)")


def _half(col):
    return "L" if col in LEFT else "R"


def covered_holes(kind, pins):
    """Holes that lie under a flat two-legged part, so nothing else may use them.

    A resistor or ceramic capacitor with its legs in one column covers the
    holes between them; one with its legs in one row covers the holes
    between them in that row; one leg in column a (or j) with the other in
    the strip beside it covers nothing. Electrolytic capacitors stand on two
    neighbouring holes and cover nothing.
    """
    if kind not in ("res", "cer"):
        return set()
    a, b = list(pins.values())
    if is_rail(a) or is_rail(b):
        hole, rail = (a, b) if is_rail(b) else (b, a)
        row, col = int(hole[:-1]), hole[-1]
        side = rail.split("@")[0][0]
        if side == "L":
            return {f"{row}{c}" for c in LEFT[:LEFT.index(col)]}
        return {f"{row}{c}" for c in RIGHT[RIGHT.index(col) + 1:]}
    ra, ca, rb, cb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    if ca == cb:
        lo, hi = sorted((ra, rb))
        return {f"{r}{ca}" for r in range(lo + 1, hi)}
    if ra == rb and _half(ca) == _half(cb):
        cols = LEFT if ca in LEFT else RIGHT
        lo, hi = sorted((cols.index(ca), cols.index(cb)))
        return {f"{ra}{c}" for c in cols[lo + 1:hi]}
    raise SystemExit(f"{kind} {pins}: place it along one column or one row")


def check_build(parts, nets, name, quiet=()):
    """The rules that let the board be built in a clean physical order."""
    errors = []
    covered = {}
    for pid, kind, _, pins in parts:
        for h in covered_holes(kind, pins):
            covered[h] = pid
    for pid, kind, _, pins in parts:
        for pin, pt in pins.items():
            if pt in covered and covered[pt] != pid:
                errors.append(f"{pid}.{pin} in {pt} lies under {covered[pt]}")
        if kind == "elec":
            a, b = pins["+"], pins["-"]
            if is_rail(a) or is_rail(b):
                errors.append(f"{pid}: an electrolytic must not go into a strip")
                continue
            ra, ca, rb, cb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
            adjacent = (ca == cb and abs(ra - rb) == 1) or (ra == rb and _half(ca) == _half(cb) and
                                                            abs((LEFT + RIGHT).index(ca) - (LEFT + RIGHT).index(cb)) == 1)
            if not adjacent:
                errors.append(f"{pid}: an electrolytic must stand in two neighbouring holes ({a}, {b})")
    # A wire's plug is 2.5 mm square and goes in last, so it must clear the can
    # of every electrolytic (10 uF about 5 mm across, 100 uF up to 8 mm).
    def gx(col):
        return LEFT.index(col) if col in LEFT else 7 + RIGHT.index(col)
    cans = []
    for pid, kind, val, pins in parts:
        if kind == "elec" and not any(is_rail(p) for p in pins.values()):
            (ra, ca), (rb, cb) = [(int(p[:-1]), p[-1]) for p in pins.values()]
            cans.append((pid, (ra + rb) / 2, (gx(ca) + gx(cb)) / 2, 1.3 if val == "100uF" else 1.0))
    for pid, kind, _, pins in parts:
        if kind != "wire":
            continue
        for pt in pins.values():
            if is_rail(pt) or is_ext(pt):
                continue
            r, c = int(pt[:-1]), gx(pt[-1])
            for cid, cr, cc, rad in cans:
                if ((r - cr) ** 2 + (c - cc) ** 2) ** 0.5 < rad + 0.5:
                    errors.append(f"{pid} in {pt} would press against {cid}")
    owner = {}
    for pid, kind, _, pins in parts:
        for pin, pt in pins.items():
            owner[f"{pid}.{pin}"] = (kind, pt)
    for net in quiet:
        rows = {node_of(owner[m][1]) for m in nets[net]}
        for pid, kind, _, pins in parts:
            if kind == "wire" and any(node_of(pt) in rows for pt in pins.values()):
                errors.append(f"quiet node {net} carries wire {pid}")
    if errors:
        raise SystemExit(f"{name} build-order check FAILED:\n  " + "\n  ".join(errors))
    print(f"{name}: build-order check passed ({len(covered)} covered holes, all free)")


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

P = 22                      # hole pitch in px
X0, Y0 = 90, 96             # hole 1a
GAP = 2 * P                 # centre groove
XR = X0 + 4 * P + GAP       # hole 1f
RAIL_X = {"L+": X0 - 2.6 * P, "L-": X0 - 1.6 * P,
          "R+": XR + 4 * P + 1.6 * P, "R-": XR + 4 * P + 2.6 * P}
EXT_X = RAIL_X["R-"] + 4.2 * P   # left edge of the off-board panel

WIRE_COLORS = {"red": "#d62828", "black": "#222222", "orange": "#f77f00", "yellow": "#d4a017",
               "green": "#2a9d8f", "purple": "#7b2cbf", "blue": "#1d4e89", "white": "#9aa0a6",
               "gray": "#6c757d"}


def xy(pt):
    if is_rail(pt):
        rail, row = pt.split("@")
        return RAIL_X[rail], Y0 + (int(row) - 1) * P
    row, col = int(pt[:-1]), pt[-1]
    y = Y0 + (row - 1) * P
    x = X0 + LEFT.index(col) * P if col in LEFT else XR + RIGHT.index(col) * P
    return x, y


def svg_board(parts, title, subtitle, ext_groups):
    width = int(EXT_X + 230)
    height = int(Y0 + ROWS * P + 70)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="Arial, Helvetica, sans-serif">',
         f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
         f'<text x="16" y="28" font-size="18" font-weight="bold">{title}</text>',
         f'<text x="16" y="48" font-size="11.5" fill="#444">{subtitle}</text>']
    bx0, bx1 = RAIL_X["L+"] - 0.8 * P, RAIL_X["R-"] + 0.8 * P
    o.append(f'<rect x="{bx0}" y="{Y0 - 1.2 * P}" width="{bx1 - bx0}" height="{ROWS * P + 1.6 * P}" '
             f'rx="10" fill="#f4f1ea" stroke="#999"/>')
    o.append(f'<rect x="{X0 + 4.5 * P}" y="{Y0 - 0.7 * P}" width="{GAP - P}" height="{ROWS * P}" '
             f'fill="#e3ded2"/>')
    for rail, x in RAIL_X.items():
        col = "#d62828" if rail.endswith("+") else "#1d4e89"
        o.append(f'<line x1="{x}" y1="{Y0 - 0.7 * P}" x2="{x}" y2="{Y0 + (ROWS - 0.3) * P}" '
                 f'stroke="{col}" stroke-width="1.2" stroke-dasharray="3,3"/>')
        o.append(f'<text x="{x}" y="{Y0 - 1.4 * P}" font-size="11" text-anchor="middle" '
                 f'fill="{col}" font-weight="bold">{rail}</text>')
        for r in range(1, ROWS + 1):
            o.append(f'<rect x="{x - 3}" y="{Y0 + (r - 1) * P - 3}" width="6" height="6" fill="#c8c8c8"/>')
    for r in range(1, ROWS + 1):
        y = Y0 + (r - 1) * P
        o.append(f'<text x="{X0 - 0.72 * P}" y="{y + 4}" font-size="9" text-anchor="middle" fill="#777">{r}</text>')
        o.append(f'<text x="{XR + 4 * P + 0.72 * P}" y="{y + 4}" font-size="9" text-anchor="middle" fill="#777">{r}</text>')
        for c in LEFT + RIGHT:
            hx, hy = xy(f"{r}{c}")
            o.append(f'<rect x="{hx - 3}" y="{hy - 3}" width="6" height="6" fill="#c8c8c8"/>')
    for c in LEFT + RIGHT:
        hx, _ = xy(f"1{c}")
        o.append(f'<text x="{hx}" y="{Y0 - 1.4 * P}" font-size="10" text-anchor="middle" fill="#777">{c}</text>')

    # off-board panel
    ext_y = {}
    y = Y0
    for gname, pins in ext_groups:
        top = y - 0.8 * P
        o.append(f'<rect x="{EXT_X - 10}" y="{top}" width="210" height="{len(pins) * 1.6 * P + 0.6 * P}" '
                 f'rx="6" fill="#eef2f7" stroke="#8aa1c1"/>')
        o.append(f'<text x="{EXT_X + 95}" y="{top - 6}" font-size="11" text-anchor="middle" '
                 f'font-weight="bold" fill="#33415c">{gname}</text>')
        for key, label in pins:
            ext_y[key] = y
            o.append(f'<circle cx="{EXT_X}" cy="{y}" r="4.5" fill="#33415c"/>')
            o.append(f'<text x="{EXT_X + 12}" y="{y + 4}" font-size="11.5" fill="#111">{label}</text>')
            y += 1.6 * P
        y += 1.6 * P

    def mark(pt):
        hx, hy = xy(pt)
        o.append(f'<circle cx="{hx}" cy="{hy}" r="4" fill="#111"/>')

    for pid, kind, val, pins in parts:
        if kind == "wire":
            continue
        if kind in ("res", "cer", "elec"):
            (p1, h1), (p2, h2) = list(pins.items())
            a, b = xy(h1), xy(h2)
            mark(h1)
            mark(h2)
            o.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#555" stroke-width="2"/>')
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            if kind == "res":
                o.append(f'<rect x="{mx - 11}" y="{my - 8}" width="22" height="16" rx="4" '
                         f'fill="#e7c98a" stroke="#8a6d2f"/>')
            elif kind == "cer":
                o.append(f'<circle cx="{mx}" cy="{my}" r="9" fill="#f4a261" stroke="#9c5a1f"/>')
            else:
                o.append(f'<circle cx="{mx}" cy="{my}" r="11" fill="#264653" stroke="#111"/>')
                pa = a if p1 == "+" else b
                o.append(f'<text x="{pa[0] + 6}" y="{pa[1] - 6}" font-size="13" font-weight="bold" fill="#d62828">+</text>')
            o.append(f'<text x="{mx}" y="{my + 3.5}" font-size="8.5" text-anchor="middle" '
                     f'fill="{"#fff" if kind == "elec" else "#000"}" font-weight="bold">{pid}</text>')
        elif kind == "npn":
            pts = [xy(h) for h in pins.values()]
            x = pts[0][0]
            y0, y1 = pts[0][1], pts[-1][1]
            # D shape: straight (flat) side on the left, toward the L rails
            o.append(f'<path d="M {x - 9} {y0 - 9} L {x - 9} {y1 + 9} C {x + 22} {y1 + 9} '
                     f'{x + 22} {y0 - 9} {x - 9} {y0 - 9} Z" fill="#333"/>')
            for pin, h in pins.items():
                hx, hy = xy(h)
                o.append(f'<circle cx="{hx}" cy="{hy}" r="3" fill="#ddd"/>')
                o.append(f'<text x="{hx + 24}" y="{hy + 4}" font-size="10.5" font-weight="bold" fill="#c1121f">{pin}</text>')
            o.append(f'<text x="{x - 14}" y="{(y0 + y1) / 2 + 4}" font-size="10" text-anchor="end" font-weight="bold">{pid}</text>')
        elif kind == "dip8":
            x1, y1 = xy(pins["1"])
            x8, _ = xy(pins["8"])
            _, y4 = xy(pins["4"])
            o.append(f'<rect x="{x1 - 7}" y="{y1 - 11}" width="{x8 - x1 + 14}" height="{y4 - y1 + 22}" rx="3" fill="#222"/>')
            o.append(f'<path d="M {(x1 + x8) / 2 - 8} {y1 - 11} A 8 8 0 0 0 {(x1 + x8) / 2 + 8} {y1 - 11} Z" fill="#f4f1ea"/>')
            o.append(f'<circle cx="{x1 + 6}" cy="{y1 - 3}" r="2.5" fill="#fff"/>')
            o.append(f'<text x="{(x1 + x8) / 2}" y="{(y1 + y4) / 2 + 4}" font-size="11" fill="#fff" text-anchor="middle" font-weight="bold">{pid}</text>')
            for pin, h in pins.items():
                hx, hy = xy(h)
                dx = 12 if int(pin) <= 4 else -12
                o.append(f'<text x="{hx + dx}" y="{hy + 4}" font-size="9" text-anchor="middle" fill="#fff" font-weight="bold">{pin}</text>')
        elif kind == "module":
            pts = [xy(h) for h in pins.values()]
            x = pts[0][0]
            ys = [p[1] for p in pts]
            o.append(f'<rect x="{x - 6}" y="{min(ys) - 12}" width="{RAIL_X["R-"] - x + 60}" '
                     f'height="{max(ys) - min(ys) + 24}" rx="4" fill="#1d3557" opacity="0.88"/>')
            for pin, h in pins.items():
                hx, hy = xy(h)
                o.append(f'<circle cx="{hx}" cy="{hy}" r="3.5" fill="#ffd166"/>')
                o.append(f'<text x="{hx + 10}" y="{hy + 4}" font-size="9.5" fill="#fff" font-weight="bold">{pin}</text>')
            o.append(f'<text x="{x + 52}" y="{(min(ys) + max(ys)) / 2 + 4}" font-size="11" fill="#fff" font-weight="bold">{pid}</text>')

    for pid, kind, color, pins in parts:
        if kind != "wire":
            continue
        a, b = pins["1"], pins["2"]
        col = WIRE_COLORS[color]
        if is_ext(a) or is_ext(b):
            ext, onb = (a, b) if is_ext(a) else (b, a)
            hx, hy = xy(onb)
            ex, ey = EXT_X, ext_y[ext]
            mark(onb)
            o.append(f'<path d="M {hx} {hy} C {hx + 60} {hy - 30}, {ex - 90} {ey}, {ex - 5} {ey}" fill="none" '
                     f'stroke="{col}" stroke-width="3.2" stroke-linecap="round" opacity="0.85"/>')
            o.append(f'<text x="{ex - 30}" y="{ey - 5}" font-size="9" fill="{col}" font-weight="bold">{pid}</text>')
        else:
            (ax, ay), (bx, by) = xy(a), xy(b)
            mark(a)
            mark(b)
            bend = 26 if ax == bx else 0
            cy = (ay + by) / 2 - (14 if ay == by else 0)
            o.append(f'<path d="M {ax} {ay} Q {(ax + bx) / 2 + bend} {cy} {bx} {by}" fill="none" '
                     f'stroke="{col}" stroke-width="3.2" stroke-linecap="round" opacity="0.85"/>')
            o.append(f'<text x="{(ax + bx) / 2 + bend + 3}" y="{cy - 3}" font-size="8.5" fill="{col}" font-weight="bold">{pid}</text>')
    o.append("</svg>")
    return "\n".join(o)


KIND_NAME = {"wire": "jumper wire", "res": "resistor", "cer": "ceramic capacitor",
             "elec": "electrolytic capacitor", "npn": "NPN transistor",
             "dip8": "op-amp IC", "module": "module"}


def pretty(pt, labels):
    if is_ext(pt):
        return labels[pt]
    if is_rail(pt):
        rail, row = pt.split("@")
        return f"{rail} rail beside row {row}"
    return f"hole {pt}"


def wiring_table(parts, title, labels):
    lines = [f"### {title}", "", "| Part | Type | Value | Connections |", "|---|---|---|---|"]
    import re

    def key(p):
        m = re.match(r"([A-Z]+)(\d*)", p[0])
        return (p[1] == "wire", m.group(1), int(m.group(2) or 0))

    for pid, kind, val, pins in sorted(parts, key=key):
        if kind == "wire":
            a, b = pins.values()
            desc = f"{pretty(a, labels)} to {pretty(b, labels)}"
            val = f"{val} wire"
        else:
            desc = "; ".join(f"{k} in {pretty(v, labels)}" for k, v in pins.items())
        lines.append(f"| {pid} | {KIND_NAME[kind]} | {val} | {desc} |")
    return "\n".join(lines) + "\n"


EXT_LABELS = {"DEV:5V": "DevEBox pin 5V", "DEV:GND": "DevEBox pin GND",
              "DEV:3V3": "DevEBox pin 3V3", "DEV:PA4": "DevEBox pin PA4",
              "DEV:PB0": "DevEBox pin PB0", "DEV:PA8": "DevEBox pin PA8",
              "DEV:PC9": "DevEBox pin PC9",
              "DISCO:5V": "Discovery pin 5V", "DISCO:GND": "Discovery pin GND",
              "DISCO:3V": "Discovery pin 3V", "DISCO:PB0": "Discovery pin PB0",
              "DISCO:PA1": "Discovery pin PA1", "DISCO:PB1": "Discovery pin PB1",
              "DISCO:PA8": "Discovery pin PA8",
              "DISCO:PC9": "Discovery pin PC9", "MT3608:VIN+": "MT3608 VIN+",
              "MT3608:VIN-": "MT3608 VIN-", "MT3608:VOUT+": "MT3608 VOUT+",
              "MT3608:VOUT-": "MT3608 VOUT-"}


def main():
    check(BOARD_A, NETS_A, "Board A")
    check(BOARD_A_DUAL, NETS_A_DUAL, "Board A with source B")
    check(BOARD_B, NETS_B, "Board B")
    check(BOARD_B_DEV, NETS_B, "Board B (DevEBox)")
    check(BOARD_A_DEV, NETS_A_DEV, "Board A (DevEBox)")
    check_build(BOARD_A_DEV, NETS_A_DEV, "Board A (DevEBox)", QUIET_NODES_DEV)
    groups_a = [("MT3608 Boost Module (Off-Board)",
                 [("MT3608:VIN+", "VIN+"), ("MT3608:VIN-", "VIN-"),
                  ("MT3608:VOUT+", "VOUT+  (About 12 V)"), ("MT3608:VOUT-", "VOUT-")]),
                ("STM32F4DISCOVERY Header Pins",
                 [("DISCO:5V", "5V"), ("DISCO:GND", "GND"), ("DISCO:3V", "3V"),
                  ("DISCO:PA1", "PA1  (Noise to ADC)"), ("DISCO:PB0", "PB0  (Boost Monitor)")])]
    groups_dual = [groups_a[0],
                   ("STM32F4DISCOVERY Header Pins",
                    [("DISCO:5V", "5V"), ("DISCO:GND", "GND"), ("DISCO:3V", "3V"),
                     ("DISCO:PA1", "PA1  (Source A)"), ("DISCO:PB0", "PB0  (Boost Monitor)"),
                     ("DISCO:PB1", "PB1  (Source B)")])]
    groups_b = [("STM32F4DISCOVERY Header Pins",
                 [("DISCO:3V", "3V"), ("DISCO:GND", "GND"),
                  ("DISCO:PA8", "PA8  (SCL)"), ("DISCO:PC9", "PC9  (SDA)")])]
    (OUT / "breadboard_A.svg").write_text(svg_board(
        BOARD_A, "Zirah Board A, Entropy Source",
        "Portrait, row 1 at the top, holes a to e left of the groove. Dots mark used holes.",
        groups_a))
    (OUT / "breadboard_A_dual.svg").write_text(svg_board(
        BOARD_A_DUAL, "Zirah Board A with the Optional Second Noise Source",
        "Source B on the right half: Q2, R11 to R15, C10 to C12 and W20 to W25. W18 and W19 are removed.",
        groups_dual))
    (OUT / "breadboard_B.svg").write_text(svg_board(
        BOARD_B, "Zirah Board B, I2C Bus",
        "Modules plug into column j with their bodies off the right edge. Follow YOUR module's pin labels.",
        groups_b))
    groups_dev = [("MT3608 Boost Module (Off-Board)",
                   [("MT3608:VIN+", "VIN+"), ("MT3608:VIN-", "VIN-"),
                    ("MT3608:VOUT+", "VOUT+  (About 28 V)"), ("MT3608:VOUT-", "VOUT-")]),
                  ("DevEBox Header Pins",
                   [("DEV:5V", "5V"), ("DEV:GND", "GND  (Two Pins)"), ("DEV:3V3", "3V3"),
                    ("DEV:PA4", "PA4  (Noise to ADC)"), ("DEV:PB0", "PB0  (Bias Monitor)")])]
    groups_b_dev = [("DevEBox Header Pins",
                     [("DEV:3V3", "3V3"), ("DEV:GND", "GND"),
                      ("DEV:PA8", "PA8  (SCL)"), ("DEV:PC9", "PC9  (SDA)")])]
    (OUT / "breadboard_B_devebox.svg").write_text(svg_board(
        BOARD_B_DEV, "Zirah Board B on the DevEBox, I2C Bus",
        "As specified in WIRING.md. On the measured build the ATECC608 module sat beside the board on jumper wires.",
        groups_b_dev))
    (OUT / "breadboard_A_devebox.svg").write_text(svg_board(
        BOARD_A_DEV, "Zirah Board A on the DevEBox, Full-Size Breadboard",
        "As built and measured on 5 November 2026: R16 and R17 halve the boost output, W25 and W26 added on the bench.",
        groups_dev))
    md = ["# Zirah Wiring Table", "",
          "This file is generated by hardware/layout/zirah_layout.py from the same data that",
          "draws the breadboard_*.svg drawings, and that data is checked against the",
          "circuit netlist every time the script runs. Edit the script, not this file.", "",
          "Hole names assume the board is held portrait with row 1 at the top and holes a to e",
          "on the left of the centre groove. Rail names: L+ and L- run down the left edge,",
          "R+ and R- down the right edge.", "",
          wiring_table(BOARD_A, "Board A, Entropy Source", EXT_LABELS), "",
          wiring_table(SOURCE_B, "Board A, Optional Second Noise Source (Add These, Remove W18 and W19)",
                       EXT_LABELS), "",
          wiring_table(BOARD_B, "Board B, I2C Bus", EXT_LABELS), "",
          wiring_table(BOARD_B_DEV, "Board B on the DevEBox, I2C Bus", EXT_LABELS), "",
          wiring_table(BOARD_A_DEV, "Board A on the DevEBox, as Built",
                       EXT_LABELS)]
    (OUT / "WIRING_TABLE.md").write_text("\n".join(md))
    print("wrote breadboard_A.svg, breadboard_A_dual.svg, breadboard_B.svg, breadboard_A_devebox.svg, "
          "breadboard_B_devebox.svg, WIRING_TABLE.md")


if __name__ == "__main__":
    main()
