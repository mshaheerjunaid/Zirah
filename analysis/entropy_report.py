#!/usr/bin/env python3
"""Quick statistics and figures for a raw Zirah entropy capture.

Works on Windows:
    py analysis\\entropy_report.py data\\entropy\\run1
    py analysis\\entropy_report.py data\\entropy\\run1 --compare data\\entropy\\control

Reads <prefix>_raw16.bin (uint16 little-endian ADC samples) and writes
figures plus a small JSON of statistics into analysis/figures/.

These numbers are descriptive only. The min-entropy claim must come from the
official NIST SP 800-90B tool, which builds on Linux or WSL2 (see data/entropy/README.md).
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np


def load(prefix):
    raw = np.fromfile(f"{prefix}_raw16.bin", dtype="<u2").astype(np.int32)
    if raw.size == 0:
        raise SystemExit(f"{prefix}_raw16.bin is empty or missing")
    return raw


def describe(x):
    sym8 = x & 0xFF
    counts = np.bincount(sym8, minlength=256)
    p = counts / counts.sum()
    nz = p[p > 0]
    n = x.size
    pmax = p.max()
    pu = min(1.0, pmax + 2.576 * math.sqrt(pmax * (1 - pmax) / (n - 1)))
    xc = x - x.mean()
    var = float(np.mean(xc * xc))
    ac = [float(np.mean(xc[:-k] * xc[k:]) / var) if var > 0 else 0.0 for k in range(1, 21)]
    return {
        "samples": int(n),
        "min": int(x.min()), "max": int(x.max()),
        "mean": float(x.mean()), "std": float(math.sqrt(var)),
        "clipped_fraction": float(np.mean((x == 0) | (x == 4095))),
        "distinct_12bit_values": int(np.unique(x).size),
        "shannon_entropy_8bit_symbols": float(-(nz * np.log2(nz)).sum()),
        "mcv_min_entropy_8bit_symbols": float(-math.log2(pu)),
        "lsb_ones_fraction": float(np.mean(x & 1)),
        "autocorrelation_lag1_to_20": ac,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", help="capture prefix, e.g. data/entropy/run1")
    ap.add_argument("--compare", help="second capture, e.g. a control run with the boost off")
    ap.add_argument("--rate", type=float, default=20000.0)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path("analysis/figures")
    out.mkdir(parents=True, exist_ok=True)
    name = Path(args.prefix).name
    x = load(args.prefix)
    stats = {name: describe(x)}
    y = None
    if args.compare:
        y = load(args.compare)
        stats[Path(args.compare).name] = describe(y)

    fig, ax = plt.subplots(figsize=(8, 3))
    t = np.arange(2000) / args.rate * 1000
    ax.plot(t, x[:2000], linewidth=0.6, color="#3b5b92", label="Avalanche Source On")
    if y is not None:
        ax.plot(t, y[:2000], linewidth=0.6, color="#c0392b", label="Control")
        ax.legend(loc="upper right")
    ax.set_title("Raw ADC Samples of the Amplified Avalanche Noise")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("ADC Value (12-Bit)")
    fig.tight_layout()
    fig.savefig(out / f"{name}_timeseries.png", dpi=160)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.hist(x, bins=128, color="#3b5b92", alpha=0.85, label="Avalanche Source On")
    if y is not None:
        ax.hist(y, bins=128, color="#c0392b", alpha=0.6, label="Control")
        ax.legend(loc="upper right")
    ax.set_title("Distribution of Raw Samples")
    ax.set_xlabel("ADC Value (12-Bit)")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(out / f"{name}_histogram.png", dpi=160)

    fig, ax = plt.subplots(figsize=(6, 3))
    ac = stats[name]["autocorrelation_lag1_to_20"]
    ax.bar(range(1, 21), ac, color="#3b5b92")
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_title("Autocorrelation of Raw Samples")
    ax.set_xlabel("Lag (Samples)")
    ax.set_ylabel("Correlation Coefficient")
    fig.tight_layout()
    fig.savefig(out / f"{name}_autocorr.png", dpi=160)

    (out / f"{name}_stats.json").write_text(json.dumps(stats, indent=2))
    for k, s in stats.items():
        print(f"{k}: std {s['std']:.1f} LSB, clipped {100 * s['clipped_fraction']:.2f} %, "
              f"lag-1 autocorr {s['autocorrelation_lag1_to_20'][0]:+.3f}, "
              f"MCV estimate {s['mcv_min_entropy_8bit_symbols']:.2f} bits/symbol")
    print(f"Figures and stats written to {out}")


if __name__ == "__main__":
    main()
