"""Zirah's parts and what crosses the serial link, in the report style."""
import sys
from style import new, box, arrow, wire, save, INK, SUB, COL

fig, ax = new(3.05)          # 11.9 cm wide


def t(x, y, s, ha="center", va="center", fs=6.0, color=INK, **kw):
    ax.text(x, y, s, ha=ha, va=va, fontsize=fs, color=color, zorder=6, **kw)


def group(x, y, w, h, title, color):
    e, f = COL[color]
    from matplotlib.patches import FancyBboxPatch
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.6", fc=f, ec=e, lw=1.1,
                                zorder=1))
    t(x + 1.3, y + h - 1.4, title, ha="left", va="top", fs=6.8)


# the PC
group(1, 21, 28, 43, "Windows PC", "gray")
box(ax, 3, 47.5, 24, 9.6, "gray", "Desktop App and CLI", "Hashing, Signed Copies", fs=6.4, sfs=5.6, z=2)
box(ax, 3, 35.5, 24, 9.6, "gray", "Host Core", "Manifest and Trust Store", fs=6.4, sfs=5.6, z=2)
box(ax, 3, 23.5, 24, 9.6, "gray", "PC-Side Verification", "Independent Libraries", fs=6.4, sfs=5.6, z=2)
arrow(ax, (15, 47.3), (15, 45.3), color=SUB)
arrow(ax, (15, 35.3), (15, 33.3), color=SUB)

# the serial link
t(41.5, 60.2, "CP2102 USB to UART", fs=6.0, color=SUB)
t(41.5, 57.0, "115,200 Baud, PA9 and PA10", fs=5.6, color=SUB)
arrow(ax, (29.3, 46.0), (53.7, 46.0), color=COL["blue"][0], lw=1.1)
t(41.5, 48.3, "32-Byte Manifest Digest D", fs=5.8, color=COL["blue"][0])
arrow(ax, (53.7, 38.0), (29.3, 38.0), color=COL["red"][0], lw=1.1)
t(41.5, 40.3, "ML-DSA-65 and ECDSA Halves", fs=5.8, color=COL["red"][0])

# the DevEBox
group(54, 21, 25, 43, "DevEBox STM32F407VE", "blue")
box(ax, 55.5, 47.5, 22, 9.6, "gray", "Command Parser", "K1 Press, Fail Closed", fs=6.4, sfs=5.6, z=2)
box(ax, 55.5, 35.5, 22, 9.6, "gray", "Masked ML-DSA-65", "Two Shares, Refreshed", fs=6.4, sfs=5.6, z=2)
box(ax, 55.5, 23.5, 22, 9.6, "gray", "Entropy Pipeline", "Health Tests, SHAKE256", fs=6.4, sfs=5.6, z=2)
arrow(ax, (66.5, 47.3), (66.5, 45.3), color=SUB)
arrow(ax, (66.5, 33.5), (66.5, 35.3), color=SUB)

# Board B
group(82, 34, 17, 30, "Board B", "green")
box(ax, 83.5, 48.2, 14, 8.6, "gray", "ATECC608", "P-256 Key", fs=6.2, sfs=5.6, z=2)
box(ax, 83.5, 36.4, 14, 8.6, "gray", "SH1106 OLED", "Digest Prefix", fs=6.0, sfs=5.6, z=2)
arrow(ax, (77.8, 52.5), (83.2, 52.5), color=SUB, both=True)
arrow(ax, (77.8, 40.7), (83.2, 40.7), color=SUB, both=True)

# Board A, the noise source
group(26, 1, 73, 16.5, "Board A, Noise Source", "red")
specs = [("MT3608", "About 28 V"), ("Divider, Filter", "13.6 to 14.2 V"), ("2N3904 B-E", "Avalanche"),
         ("LM358 Gain 11", "RC to PA4")]
x0 = 28.5
for i, (a, b) in enumerate(specs):
    box(ax, x0 + i * 17.4, 2.6, 14.6, 8.6, "gray", a, b, fs=6.0, sfs=5.4, z=2)
    if i:
        arrow(ax, (x0 + i * 17.4 - 2.6, 6.9), (x0 + i * 17.4 - 0.3, 6.9), color=SUB)
# noise up into the pipeline, bias monitor up into the parser side
wire(ax, [(92.6, 11.4), (92.6, 28.3), (79.6, 28.3)], COL["blue"][0], lw=1.1)
arrow(ax, (80.0, 28.3), (77.8, 28.3), color=COL["blue"][0], lw=1.1)
t(93.6, 22.0, "Noise", ha="left", fs=5.6, color=COL["blue"][0])
t(93.6, 19.6, "to PA4", ha="left", fs=5.6, color=COL["blue"][0])
wire(ax, [(50.6, 11.4), (50.6, 28.3), (53.4, 28.3)], SUB, lw=1.0)
arrow(ax, (53.0, 28.3), (55.3, 28.3), color=SUB)
t(49.6, 24.2, "Bias Monitor", ha="right", fs=5.6, color=SUB)
t(49.6, 21.8, "to PB0", ha="right", fs=5.6, color=SUB)

save(fig, sys.argv[1] if len(sys.argv) > 1 else "out", "architecture")
print("ok")
