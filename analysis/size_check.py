"""size_check.py - analyse scripts/size_order_check.ps1 output.

Question: in the main run, 64 MB and 1 GB were slower than 256 KB. Is that a
cache/memory effect, or because the large sizes always ran last in each round
(CPU hotter, lower turbo clock)?

Usage: py analysis/size_check.py results/size_check_<ts>.csv results/run_<main>.csv

Writes results/size_check_<ts>_summary.md and results/size_check_<ts>_fig.png.
"""

import datetime as dt
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot import STYLE, SHORT, INK, MUTED, size_label, ci_halfwidth, style_axes, footnote  # noqa: E402


def to_local_naive(utc_text):
    """'2026-11-17T18:50:01.123Z' -> local wall-clock time without timezone,
    to match typeperf's timestamps (which are local time)."""
    t = dt.datetime.strptime(utc_text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    return t.astimezone().replace(tzinfo=None)


def load_perf(path):
    """typeperf CSV: first column = local timestamp, then one column per
    logical processor (plus _Total). Returns per-second MAX over the
    individual processors = the clock of the busiest core, roughly."""
    p = pd.read_csv(path)
    p = p.rename(columns={p.columns[0]: "time"})
    p["time"] = pd.to_datetime(p["time"], format="%m/%d/%Y %H:%M:%S.%f")
    cpu_cols = [c for c in p.columns[1:] if "_Total" not in c]
    vals = p[cpu_cols].apply(pd.to_numeric, errors="coerce")
    return pd.DataFrame({"time": p["time"], "max_perf_pct": vals.max(axis=1)})


def trial_clock(df, perf):
    out = []
    for _, r in df.iterrows():
        a, b = to_local_naive(r["start_utc"]), to_local_naive(r["end_utc"])
        w = perf[(perf["time"] >= a - dt.timedelta(seconds=0.5)) & (perf["time"] <= b + dt.timedelta(seconds=0.5))]
        out.append(w["max_perf_pct"].mean() if len(w) else np.nan)
    return out


def rel_to_256k(df):
    """Per config: mean throughput at each size as % of the same config's 256 KB mean."""
    rows = []
    for cfg, g in df.groupby("config_label"):
        base = g[g["size_bytes"] == 262144]["throughput_MBps"].mean()
        for size, h in g.groupby("size_bytes"):
            t = h["throughput_MBps"].to_numpy(float)
            rows.append(dict(config_label=cfg, size_bytes=int(size), n=len(t), mean_MBps=t.mean(),
                             ci95=ci_halfwidth(t), pct_of_256k=t.mean() / base * 100,
                             ci95_pct=ci_halfwidth(t) / base * 100))
    return pd.DataFrame(rows)


def main():
    check_csv, main_csv = sys.argv[1], sys.argv[2]
    stem = os.path.splitext(check_csv)[0]
    chk = pd.read_csv(check_csv)
    chk = chk[chk["verified"] == "PASS"].copy()
    mainrun = pd.read_csv(main_csv)
    sizes = sorted(chk["size_bytes"].unique())
    cfgs = [c for c in STYLE if c in set(chk["config_label"])]
    mainrun = mainrun[mainrun["config_label"].isin(cfgs) & mainrun["size_bytes"].isin(sizes)
                      & (mainrun["verified"] == "PASS")]

    perf_path = stem + "_perf.csv"
    have_perf = os.path.exists(perf_path)
    if have_perf:
        perf = load_perf(perf_path)
        chk["clock_pct_of_base"] = trial_clock(chk, perf)

    rc, rm = rel_to_256k(chk), rel_to_256k(mainrun)

    md = [f"# Size-order check: {os.path.basename(check_csv)}", "",
          f"Compared with the main run `{os.path.basename(main_csv)}` (large sizes always last in each round). "
          "In the check, every (size, config) pair ran once per round in a shuffled order with a cooldown "
          "before every trial (see the `_run.json` file).", "",
          "| Config | Size | Main run: MB/s (± 95% CI) | Main: % of 256 KB | Check: MB/s (± 95% CI) | Check: % of 256 KB | n (check) |",
          "|---|---|---|---|---|---|---|"]
    for cfg in cfgs:
        for size in sizes:
            m = rm[(rm.config_label == cfg) & (rm.size_bytes == size)].iloc[0]
            c = rc[(rc.config_label == cfg) & (rc.size_bytes == size)].iloc[0]
            md.append(f"| {cfg} | {size_label(size)} | {m.mean_MBps:,.1f} ± {m.ci95:,.1f} | {m.pct_of_256k:.1f}% | "
                      f"{c.mean_MBps:,.1f} ± {c.ci95:,.1f} | {c.pct_of_256k:.1f}% | {c.n} |")
    if have_perf:
        md += ["", "## Effective clock during each trial (check run)", "",
               "Windows `% Processor Performance` = effective clock as % of the 4.0 GHz base; we take the busiest "
               "logical processor each second and average over the trial. Approximate (1 sample per second).", "",
               "| Config | Size | Mean clock % of base | Min | Max |", "|---|---|---|---|---|"]
        for cfg in cfgs:
            for size in sizes:
                v = chk[(chk.config_label == cfg) & (chk.size_bytes == size)]["clock_pct_of_base"]
                md.append(f"| {cfg} | {size_label(size)} | {v.mean():.1f} | {v.min():.1f} | {v.max():.1f} |")
    with open(stem + "_summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))

    # Figure: % of 256 KB throughput, main run vs check, per config
    fig, axes = plt.subplots(1, len(cfgs), figsize=(4.4 * len(cfgs), 5.2), sharey=True)
    axes = np.atleast_1d(axes)
    w = 0.38
    x = np.arange(len(sizes))
    for ax, cfg in zip(axes, cfgs):
        for k, (tab, lab, alpha) in enumerate([(rm, "Main run (large sizes last)", 0.45),
                                               (rc, "Check (shuffled + cooldown)", 1.0)]):
            d = tab[tab.config_label == cfg].set_index("size_bytes").reindex(sizes)
            ax.bar(x + (k - 0.5) * w, d["pct_of_256k"], width=w, color=STYLE[cfg]["color"], alpha=alpha,
                   yerr=d["ci95_pct"], capsize=3, ecolor=INK, label=lab)
            for xi, v in zip(x + (k - 0.5) * w, d["pct_of_256k"]):
                ax.text(xi, v + 1.5, f"{v:.0f}%", ha="center", va="bottom", fontsize=9, color=INK)
        ax.axhline(100, color=INK, linewidth=0.8)
        ax.set_xticks(x, [size_label(int(s)) for s in sizes])
        ax.set_title(SHORT[cfg], color=INK)
        ax.set_ylim(0, 120)
        style_axes(ax)
    axes[0].set_ylabel("Throughput as % of the same config at 256 KB", color=INK)
    axes[0].legend(frameon=False, loc="lower left", fontsize=9)
    fig.suptitle("Is the slowdown at 64 MB / 1 GB real, or caused by run order?", color=INK)
    footnote(fig, "Lighter bars: main run. Solid bars: shuffled order with a cooldown before every trial. "
                  "Error bars = 95% CI. 100% = same speed as 256 KB.")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(stem + "_fig.png", dpi=150)
    print(f"Wrote {os.path.basename(stem)}_summary.md and _fig.png")


if __name__ == "__main__":
    main()
