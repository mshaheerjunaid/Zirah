"""Shared look for the Zirah report figures: Liberation Sans, Title Case
labels, pastel boxes with saturated edges, 11.9 cm wide, saved as a 1000 dpi
PNG plus PDF and SVG."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

W_IN = 11.9 / 2.54
plt.rcParams.update({
    "font.family": "Liberation Sans", "font.size": 7.0, "axes.titlesize": 7.5,
    "axes.labelsize": 7.0, "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "lines.linewidth": 0.9,
    "svg.fonttype": "none", "pdf.fonttype": 42, "savefig.dpi": 1000,
})
INK = "#1A1A1A"
SUB = "#4D4D4D"
COL = {   # edge, fill
    "blue": ("#0072B2", "#E6F0F8"), "orange": ("#E69F00", "#FBF0DC"),
    "red": ("#D55E00", "#FBE6DA"), "green": ("#009E73", "#DDF2EC"),
    "gray": ("#4D4D4D", "#FFFFFF"), "purple": ("#CC79A7", "#F7E8F0"),
    "sky": ("#56B4E9", "#EAF5FC"),
}
WIRE = {  # the wire colours of the breadboard drawings, darkened where white would vanish
    "5v": "#D62828", "gnd": "#1A1A1A", "3v3": "#7B3FA0", "pa4": "#8C8C8C", "pb0": "#C9A400",
    "scl": "#2E9E44", "sda": "#1F5FBF", "boost": "#E07B00", "uart": "#0072B2",
}


def new(h_in, xlim=100, ylim=None):
    fig, ax = plt.subplots(figsize=(W_IN, h_in))
    ylim = ylim if ylim is not None else xlim * h_in / W_IN
    ax.set_xlim(0, xlim); ax.set_ylim(0, ylim); ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)
    return fig, ax


def box(ax, x, y, w, h, color="gray", title=None, sub=None, title_top=False, fs=7.0, sfs=6.2, lw=1.1, r=1.4,
        z=1):
    e, f = COL[color]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=f, ec=e, lw=lw,
                                zorder=z))
    if title and title_top:
        ax.text(x + w / 2, y + h - 1.2, title, ha="center", va="top", fontsize=fs, color=INK, zorder=z + 1)
        if sub:
            ax.text(x + w / 2, y + h - 1.2 - fs * 0.62, sub, ha="center", va="top", fontsize=sfs, color=SUB,
                    zorder=z + 1)
    elif title:
        if sub:
            ax.text(x + w / 2, y + h / 2 + fs * 0.22, title, ha="center", va="bottom", fontsize=fs, color=INK,
                    zorder=z + 1)
            ax.text(x + w / 2, y + h / 2 - fs * 0.10, sub, ha="center", va="top", fontsize=sfs, color=SUB,
                    zorder=z + 1)
        else:
            ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, color=INK, zorder=z + 1)


def arrow(ax, p, q, color=SUB, lw=1.0, both=False, ms=7, z=4, ls="-"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="<|-|>" if both else "-|>", mutation_scale=ms, lw=lw,
                                 color=color, shrinkA=0, shrinkB=0, zorder=z, linestyle=ls,
                                 joinstyle="miter", capstyle="butt"))


def wire(ax, pts, color, lw=1.2, z=3, ls="-"):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, zorder=z, solid_capstyle="round", solid_joinstyle="round", ls=ls)


def dot(ax, x, y, color, s=9, z=5):
    ax.scatter([x], [y], s=s, color=color, zorder=z, linewidths=0)


def save(fig, out_dir, name):
    from pathlib import Path
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=1000, facecolor="white")
    fig.savefig(out / f"{name}.pdf", facecolor="white")
    fig.savefig(out / f"{name}.svg", facecolor="white")
    plt.close(fig)
