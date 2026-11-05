"""Board B of Zirah as specified in hardware/WIRING.md: the SH1106 OLED and the
ATECC608 on the software I2C bus of the DevEBox (PA8 SCL, PC9 SDA)."""
import sys
import schemdraw
import schemdraw.elements as elm

FS = float(sys.argv[2]) if len(sys.argv) > 2 else 14
PL = FS * 0.88
schemdraw.config(font="Liberation Sans", fontsize=FS, lw=1.3)
out = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "schematic_B_devebox.svg")

with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.0)
    dev = elm.Ic(size=(3.0, 7.0), pins=[
        elm.IcPin(name="PA8 (SCL)", side="right", pos=6.0 / 7.0, anchorname="scl", lblsize=PL),
        elm.IcPin(name="PC9 (SDA)", side="right", pos=1.0 / 7.0, anchorname="sda", lblsize=PL),
        elm.IcPin(name="3V3", side="top", pos=0.5, anchorname="v", lblsize=PL),
        elm.IcPin(name="GND", side="bottom", pos=0.5, anchorname="g", lblsize=PL),
    ]).right().at((0, 0)).anchor("sda")
    d += dev
    d += elm.Label().at((dev.center[0], dev.center[1] + 0.35)).label("DevEBox", fontsize=FS * 1.05)
    d += elm.Label().at((dev.center[0], dev.center[1] - 0.45)).label("STM32F407VE", fontsize=FS * 0.9)
    d += elm.Line().at(dev.v).up(0.6)
    d += elm.Vdd().label("3V3", fontsize=FS)
    d += elm.Line().at(dev.g).down(0.6)
    d += elm.Ground()

    y_scl, y_sda = dev.scl[1], dev.sda[1]
    x0 = dev.scl[0]

    # pull-up on SCL, close to the DevEBox
    d += elm.Line().at(dev.scl).right(1.6)
    d += (j1 := elm.Dot())
    d += elm.Resistor().up(2.0).label("RP1\n4.7 kΩ", loc="bottom", fontsize=FS)
    d += elm.Vdd().label("3V3", fontsize=FS)

    def module(xc, name, addr):
        m = elm.Ic(size=(3.4, 3.2), pins=[
            elm.IcPin(name="SCL", side="top", pos=0.5, anchorname="c", lblsize=PL),
            elm.IcPin(name="SDA", side="bottom", pos=0.5, anchorname="a", lblsize=PL),
            elm.IcPin(name="VCC", side="left", slot="3/4", anchorname="v", lblsize=PL),
            elm.IcPin(name="GND", side="right", slot="3/4", anchorname="g", lblsize=PL),
        ], leadlen=0.45)
        m = m.right().at((xc, (y_scl + y_sda) / 2)).anchor("center")
        d.add(m)
        d.add(elm.Label().at((m.center[0], m.center[1] - 0.18)).label(name, fontsize=FS * 0.95))
        d.add(elm.Label().at((m.center[0], m.center[1] - 0.70)).label(addr, fontsize=FS * 0.85))
        d.add(elm.Line().at(m.v).left(0.5))
        d.add(elm.Vdd().label("3V3", fontsize=FS))
        d.add(elm.Line().at(m.g).right(0.5))
        d.add(elm.Ground())
        return m

    oled = module(x0 + 5.6, "SH1106 OLED", "Address 0x3C")
    atecc = module(x0 + 11.4, "ATECC608", "Address 0x60")
    # SCL bus to both modules
    d += elm.Line().at(j1.center).right(oled.c[0] - j1.center[0])
    d += (jo := elm.Dot())
    d += elm.Line().at(jo.center).down(jo.center[1] - oled.c[1])
    d += elm.Line().at(jo.center).right(atecc.c[0] - jo.center[0])
    d += elm.Line().down(y_scl - atecc.c[1])
    # SDA bus to both modules, then the pull-up beyond the last one
    d += elm.Line().at(dev.sda).right(oled.a[0] - dev.sda[0])
    d += (jd := elm.Dot())
    d += elm.Line().at(jd.center).up(oled.a[1] - jd.center[1])
    d += elm.Line().at(jd.center).right(atecc.a[0] - jd.center[0])
    d += (jd2 := elm.Dot())
    d += elm.Line().at(jd2.center).up(atecc.a[1] - jd2.center[1])
    d += elm.Line().at(jd2.center).right(4.0)
    d += elm.Resistor().up(2.6).label("RP2\n4.7 kΩ", loc="bottom", fontsize=FS)
    d += elm.Vdd().label("3V3", fontsize=FS)
    d.save(out)
print("ok")
