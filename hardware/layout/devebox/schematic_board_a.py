"""Board A of Zirah as built on the DevEBox (5 November 2026), drawn in two
rows joined by the filtered bias net: the bias supply and its monitor on top,
the avalanche junction and the amplifier below. Values from the as-built
layout, hardware/layout/zirah_layout.py (BOARD_A_DEV)."""
import sys
import schemdraw
import schemdraw.elements as elm

FS = float(sys.argv[2]) if len(sys.argv) > 2 else 14
out = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "schematic_A_devebox.svg")
schemdraw.config(font="Liberation Sans", fontsize=FS, lw=1.3)

with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.2)
    # ---------------- row 1: bias supply and monitor
    d += elm.Dot(open=True).at((0, 0)).label("5 V\nDevEBox", loc="left")
    d += elm.Line().right(0.8)
    d += (bst := elm.Ic(pins=[elm.IcPin(name="VIN", side="left"), elm.IcPin(name="VOUT", side="right"),
                              elm.IcPin(name="GND", side="bottom")], size=(2.8, 1.5))
          .right().anchor("VIN").label("MT3608", loc="top", ofst=0.12))
    d += elm.Line().at(bst.GND).down(0.5)
    d += elm.Ground()
    d += elm.Line().at(bst.VOUT).right(1.0)
    d += (bo := elm.Dot())
    d += elm.Label().at((bo.center[0], bo.center[1] + 0.55)).label("About 28 V", halign="center")
    d += elm.Resistor().right().label("R16\n10 kΩ", loc="bottom")
    d += (hv := elm.Dot())
    d += elm.Label().at((hv.center[0] - 0.2, hv.center[1] + 2.1)).label("Bias Supply\n13.6 to 14.2 V", halign="right")
    d += elm.Resistor().down().label("R17\n10 kΩ", loc="bottom")
    d += elm.Ground()
    # monitor branch above: R9 and R10 divide the bias by 11 for PB0
    d += elm.Line().at(hv.center).up(3.6)
    d += elm.Resistor().right().label("R9 100 kΩ", loc="top")
    d += (mon := elm.Dot())
    d += elm.Resistor().down(1.9).label("R10\n10 kΩ", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(mon.center).right(1.9)
    d += (mc := elm.Dot())
    d += elm.Capacitor().down(1.9).label("C7\n100 nF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(mc.center).right(1.0)
    d += elm.Dot(open=True).label("PB0\nMonitor", loc="right")
    # ripple filter
    d += elm.Resistor().at(hv.center).right(2.8).label("R1 1 kΩ", loc="top")
    d += (hvf := elm.Dot())
    d += elm.Capacitor(polar=True).down().label("C1\n100 µF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(hvf.center).right(2.2)
    d += (hvf2 := elm.Dot())
    d += elm.Capacitor().down().label("C2\n100 nF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(hvf2.center).right(1.4)
    d += elm.Tag(width=3.1, height=0.8).right().label("Filtered Bias", loc="center", fontsize=FS * 0.9)

    # ---------------- row 2: avalanche junction, reference and amplifier
    y2 = -8.4
    d += (tg := elm.Tag(width=3.1, height=0.8).at((3.1, y2)).left().label("Filtered Bias", loc="center", fontsize=FS * 0.9))
    d += elm.Resistor().at((3.1, y2)).right(2.6).label("R2\n100 kΩ", loc="top")
    d += (e := elm.Dot())
    d += elm.Line().at(e.center).down(0.8)
    d += (q := elm.BjtNpn(circle=True).right().flip().anchor("emitter"))
    d += elm.Label().at((q.center[0] + 0.7, q.center[1] - 0.05)).label("Q1 2N3904", halign="left")
    d += elm.Line().at(q.base).left(0.35)
    d += elm.Ground()
    d += elm.Line().at(q.collector).down(0.3)
    d += elm.Dot(open=True).label("Collector Open", loc="bottom")
    d += elm.Capacitor().at(e.center).right(3.6).label("C3 100 nF", loc="bottom")
    d += (inp := elm.Dot())
    d += elm.Resistor().down().label("R3\n100 kΩ", loc="bottom")
    d += (vr := elm.Dot())
    d += elm.Label().at((vr.center[0] + 0.15, vr.center[1] - 0.65)).label("1.65 V", halign="left")
    d += elm.Line().at(vr.center).left(1.8)
    d += elm.Capacitor(polar=True).down().label("C4\n10 µF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(vr.center).right(1.8)
    d += (vrr := elm.Dot())
    d += elm.Resistor().down().label("R5\n100 kΩ", loc="bottom")
    d += elm.Ground()
    d += elm.Resistor().at(vrr.center).right().label("R4\n100 kΩ", loc="top")
    d += elm.Dot(open=True).label("3.3 V\nDevEBox", loc="right")
    # amplifier: gain 11 for the noise, gain 1 for the DC level because of C5
    d += elm.Line().at(inp.center).right(1.2)
    d += (op := elm.Opamp(leads=True).right().anchor("in2").label("U1A\nLM358", loc="center", ofst=(-0.35, 0)))
    d += elm.Line().at(op.in1).up(1.5)
    d += (inn := elm.Dot())
    d += elm.Resistor().right(op.out[0] - inn.center[0]).label("R7 100 kΩ", loc="top")
    d += elm.Line().down(inn.center[1] - op.out[1])
    d += elm.Resistor().at(inn.center).left(2.2).label("R6 10 kΩ", loc="top")
    d += elm.Capacitor(polar=True).down(1.3).label("C5\n10 µF", loc="bottom")
    d += elm.Ground()
    d += elm.Dot().at(op.out)
    d += elm.Resistor().at(op.out).right().label("R8\n1 kΩ", loc="top")
    d += (adc := elm.Dot())
    d += elm.Capacitor().down().label("C6\n100 pF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().at(adc.center).right(1.0)
    d += elm.Dot(open=True).label("PA4\nADC", loc="right")
    d.save(out)
print("ok")
