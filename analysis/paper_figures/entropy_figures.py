"""The noise figures of the report, drawn from the 1,000,000-sample capture
(combined/sourceA_1M_raw16.bin in the dataset) and the NIST tool outputs."""
import sys
import numpy as np
from scipy import stats
import matplotlib.pyplot as plt
from style import W_IN, COL, INK, SUB, save

RAW = sys.argv[1] if len(sys.argv) > 1 else "combined/sourceA_1M_raw16.bin"
OUT = sys.argv[2] if len(sys.argv) > 2 else "out"
BLUE, ORANGE, RED, GREY = COL["blue"][0], COL["orange"][0], COL["red"][0], "#8C8C8C"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False})
x = np.fromfile(RAW, dtype="<u2").astype(float)
assert x.size == 1_000_000

# --- raw samples and the histogram of all samples
fig, (a, b) = plt.subplots(1, 2, figsize=(W_IN, 2.05), gridspec_kw={"width_ratios": [1.35, 1]})
seg = x[:400]
a.plot(np.arange(seg.size) / 20.0, seg, color=BLUE, lw=0.6)
a.axhline(x.mean(), color=GREY, lw=0.6, ls="--")
a.text(20.7, x.mean(), "Mean\n2052", ha="left", va="center", color=SUB, fontsize=5.8, linespacing=1.1)
a.set_xlim(-0.3, 24.2)
a.set_title("Raw Samples From One Block")
a.set_xlabel("Time (Ms)".replace("Ms", "ms"))
a.set_ylabel("ADC Value (LSB)")
bins = np.arange(1650, 2715, 8)
b.hist(x, bins=bins, color=BLUE, alpha=0.85, label="Measured")
mu, sd = x.mean(), x.std(ddof=1)
g = np.linspace(bins[0], bins[-1], 400)
b.plot(g, stats.norm.pdf(g, mu, sd) * x.size * 8, color=RED, lw=0.9, label="Normal Fit")
b.set_title("All 1,000,000 Samples")
b.set_xlabel("ADC Value (LSB)")
b.set_ylabel("Count")
b.set_ylim(0, b.get_ylim()[1] * 1.34)
b.ticklabel_format(axis="y", style="plain")
b.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v / 1000:.0f}K" if v else "0"))
b.legend(loc="upper right", fontsize=5.8, handlelength=1.4)
b.text(0.03, 0.96, f"Std Dev {sd:.1f} LSB\nClipped 0", transform=b.transAxes, va="top", fontsize=5.8, color=INK,
       linespacing=1.15)
fig.tight_layout(pad=0.3, w_pad=1.2)
save(fig, OUT, "noise-trace-hist")

# --- autocorrelation inside the contiguous blocks, and the averaged spectrum
runs = [(p * 50000 + k * 2048, min(p * 50000 + k * 2048 + 2048, p * 50000 + 50000))
        for p in range(20) for k in range(25)]


def acf(lag):
    num = den = 0.0
    for s, e in runs:
        v = x[s:e] - x[s:e].mean()
        num += np.dot(v[:-lag], v[lag:])
        den += np.dot(v, v)
    return num / den


lags = np.arange(1, 41)
ac = np.array([acf(k) for k in lags])
blk = np.array([x[s:s + 2048] - x[s:s + 2048].mean() for p in range(20)
                for s in range(p * 50000, p * 50000 + 50000 - 2047, 2048)])
hann = np.hanning(2048)
P = (np.abs(np.fft.rfft(blk * hann, axis=1)) ** 2).mean(0)
f = np.fft.rfftfreq(2048, 1 / 20000)
fz = np.fft.rfftfreq(65536, 1 / 20000)
Pz = (np.abs(np.fft.rfft(blk * hann, n=65536, axis=1)) ** 2).mean(0)
peaks = []
for k in (1, 2, 3, 5):
    m = (fz > k * 115.6 - 20) & (fz < k * 115.6 + 20)
    peaks.append(fz[m][np.argmax(Pz[m])])
print("blocks", len(blk), "peaks", [round(p, 1) for p in peaks], "ac1 %.4f ac10 %.4f ac40 %.4f" % (ac[0], ac[9], ac[39]))
fig, (a, b) = plt.subplots(1, 2, figsize=(W_IN, 2.05))
a.bar(lags, ac, color=BLUE, width=0.7)
for s in (1, -1):
    a.axhline(s * 2 / np.sqrt(x.size), color=RED, lw=0.6, ls="--")
a.set_title("Autocorrelation Within Blocks")
a.set_xlabel("Lag (Samples, 50 µs Each)")
a.set_ylabel("Correlation")
a.set_ylim(-0.01, 0.12)
a.text(40.5, 0.111, "Dashed Lines Mark ±2/√N", ha="right", color=RED, fontsize=5.8)
keep = f > 0
b.semilogx(f[keep], 10 * np.log10(P[keep] / np.median(P[keep])), color=BLUE, lw=0.5)
b.set_title(f"Power Spectrum, Averaged Over {len(blk)} Blocks")
b.set_xlabel("Frequency (Hz)")
b.set_ylabel("Power Relative to Median (dB)")
b.set_xlim(10, 10000)
b.set_xticks([10, 100, 1000, 10000])
b.set_xticklabels(["10", "100", "1000", "10000"])
for fx in peaks:
    near = np.where(abs(f - fx) < 10)[0]
    i = near[np.argmax(P[near])]
    yv = 10 * np.log10(P[i] / np.median(P[keep]))
    b.annotate(f"{fx:.0f}", (f[i], yv), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=5.4,
               color=RED)
b.text(0.98, 0.52, "Lines Near 116 Hz\nand Its Harmonics", transform=b.transAxes, ha="right", va="top", fontsize=5.8,
       color=RED, linespacing=1.15)
fig.tight_layout(pad=0.3, w_pad=1.2)
save(fig, OUT, "noise-acf-psd")

# --- SP 800-90B non-IID estimates for the 8-bit symbols (values from nist/ea_non_iid_sym8.txt)
est = [("Most Common Value", 7.248809), ("T-Tuple", 7.059069), ("Longest Repeated Substring", 7.894752),
       ("Multi Most Common in Window", 7.511007), ("Lag Prediction", 7.905125),
       ("Multi Markov Model With Counting", 7.597188), ("LZ78Y Prediction", 7.597450),
       ("Bit-String Bound, 8 × 0.862", 8 * 0.862243)]
fig, a = plt.subplots(figsize=(W_IN, 2.35))
names = [n for n, _ in est][::-1]
vals = [v for _, v in est][::-1]
cols = [ORANGE if n.startswith("Bit") else BLUE for n in names]
a.barh(names, vals, color=cols, height=0.62)
a.axvline(6.897942, color=INK, lw=0.7, ls="--")
a.axvline(1.0, color=GREY, lw=0.7, ls=":")
a.text(6.85, 7.78, "Assessed 6.90", ha="right", va="center", fontsize=5.8, color=INK)
a.text(1.08, 7.78, "Firmware Credits 1.00", ha="left", va="center", fontsize=5.8, color=SUB)
for i, v in enumerate(vals):
    a.text(v + 0.06, i, f"{v:.2f}", va="center", fontsize=5.6, color=INK)
a.set_xlim(0, 8.7)
a.set_ylim(-0.55, 8.15)
a.set_xlabel("Min-Entropy (Bits per 8-Bit Symbol)")
a.set_title("NIST SP 800-90B Non-IID Estimates, 1,000,000 Symbols", pad=8)
fig.tight_layout(pad=0.3)
save(fig, OUT, "sp80090b-estimates")
print("ok")
