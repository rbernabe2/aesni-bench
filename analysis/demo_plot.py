"""demo_plot.py - quick bar chart for the live demo (scripts/demo.ps1).

Usage: py analysis/demo_plot.py <demo.csv> <out.png>
Shows mean throughput of each config at each size, one dot per trial.
"""

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

COLORS = {"aesni_on": "#2a78d6", "aesni_off": "#eb6834"}
LABELS = {"aesni_on": "AES-NI on", "aesni_off": "AES-NI off"}


def size_label(n):
    for unit, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k and n % k == 0:
            return f"{n // k} {unit}"
    return f"{n} B"


def main():
    csv, out = sys.argv[1], sys.argv[2]
    df = pd.read_csv(csv)
    sizes = sorted(df["size_bytes"].unique())
    cfgs = [c for c in COLORS if c in set(df["config_label"])]
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=130)
    w = 0.38
    x = np.arange(len(sizes))
    for k, cfg in enumerate(cfgs):
        means = [df[(df.config_label == cfg) & (df.size_bytes == s)]["throughput_MBps"].mean() for s in sizes]
        xs = x + (k - 0.5) * w
        ax.bar(xs, means, width=w, color=COLORS[cfg], label=LABELS[cfg])
        for xi, s, m in zip(xs, sizes, means):
            pts = df[(df.config_label == cfg) & (df.size_bytes == s)]["throughput_MBps"]
            ax.scatter([xi] * len(pts), pts, color="#0b0b0b", s=12, zorder=3)
            ax.text(xi, m * 1.02, f"{m:,.0f}", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(x, [size_label(int(s)) for s in sizes])
    ax.set_xlabel("Data size per encryption")
    ax.set_ylabel("Throughput (MB/s, 1 MB = 10^6 bytes)")
    ax.set_title("Live demo: AES-256-GCM with and without AES-NI\n(bars = mean, dots = individual trials)")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(True, axis="y", color="#e1e0d9")
    ax.set_axisbelow(True)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out)


if __name__ == "__main__":
    main()
