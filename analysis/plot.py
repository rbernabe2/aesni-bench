"""plot.py - statistics, graphs and tables from bench.exe CSV files.

Usage (from the repo root):
    py analysis/plot.py                         # newest results/run_*.csv
    py analysis/plot.py results/run_A.csv ...   # specific file(s), combined

Writes next to the (first) input CSV, named after it (<run> = its name):
    <run>_stats.csv / <run>_stats.md     per config x size: n, mean, std dev,
                                         coefficient of variation, min, max,
                                         95% confidence interval (+ latency,
                                         CPU, memory)
    <run>_speedup.csv                    paired per-round speedups
    <run>_fig1_throughput.png            throughput vs size (linear axis)
    <run>_fig1b_throughput_logy.png      same, log throughput axis
    <run>_fig2_speedup.png               AES-NI on / off, and AES-GCM / ChaCha20
    <run>_fig3_latency_small.png         latency per operation at 1 KB and 16 KB
    <run>_fig4_variability.png           every trial, as % deviation from its mean
    <run>_fig5_resources.png             CPU % of one core and peak memory (table)
    <run>_fig6_vs_openssl_speed.png      our harness vs `openssl speed`
    <run>_fig7_drift.png                 throughput by round (heat/turbo drift check)

Raw CSVs are only read, never modified.

Statistics used (plain language):
  * mean      = average over trials
  * std dev   = sample standard deviation (divides by n-1): typical spread
  * CV        = std dev / mean, in %: spread relative to the size of the number,
                so a 6,000 MB/s config and a 170 MB/s config can be compared
  * 95% CI    = mean +- t * std dev / sqrt(n). If we repeated the whole
                experiment many times, about 95% of such intervals would
                contain the true mean. t comes from Student's t-distribution
                (with only 10 trials the usual 1.96 would be too small).
"""

import glob
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")                 # draw to files, no window needed
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "results")

# Fixed config order and style. Color follows the configuration (never its
# rank). Marker + line style give a second cue so lines can be told apart
# without color (colorblind readers, grayscale printing).
CONFIGS = ["aesni_on", "aesni_off", "chacha_on", "chacha_masked_control",
           "aesni_off_fullmask", "chacha_fullmask"]
STYLE = {
    "aesni_on":              dict(color="#2a78d6", marker="o", linestyle="-",  label="AES-256-GCM, AES-NI on (baseline)"),
    "aesni_off":             dict(color="#eb6834", marker="s", linestyle="-",  label="AES-256-GCM, AES-NI off"),
    "chacha_on":             dict(color="#1baf7a", marker="^", linestyle="-",  label="ChaCha20-Poly1305"),
    "chacha_masked_control": dict(color="#eda100", marker="v", linestyle="--", label="ChaCha20-Poly1305, AES-NI mask (control)"),
    # diagnostics: the proposal's original mask, which also disables AVX2/AVX-512/VAES
    "aesni_off_fullmask":    dict(color="#e87ba4", marker="D", linestyle=":",  label="AES-256-GCM, original full mask (diagnostic)"),
    "chacha_fullmask":       dict(color="#008300", marker="P", linestyle=":",  label="ChaCha20-Poly1305, original full mask (diagnostic)"),
}
SHORT = {"aesni_on": "AES-NI on", "aesni_off": "AES-NI off", "chacha_on": "ChaCha20",
         "chacha_masked_control": "ChaCha20 + mask", "aesni_off_fullmask": "AES full mask",
         "chacha_fullmask": "ChaCha20 full mask"}
INK, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({"font.size": 11, "axes.titlesize": 13, "axes.labelsize": 11.5,
                     "legend.fontsize": 10, "figure.dpi": 150})

# Two-sided 95% critical values of Student's t-distribution, by degrees of
# freedom (n - 1). Standard table values; beyond 30 we use the normal 1.96.
T_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
         8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
         15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
         22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
         29: 2.045, 30: 2.042}


def t975(n):
    return T_975.get(n - 1, 1.96) if n >= 2 else float("nan")


def ci_halfwidth(values):
    """Half-width of the 95% confidence interval of the mean."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < 2:
        return float("nan")
    return t975(n) * v.std(ddof=1) / math.sqrt(n)


def size_label(n):
    """1024 -> '1 KB', 1048576 -> '1 MB' (binary units, as bench.exe uses)."""
    for unit, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k and n % k == 0:
            return f"{n // k} {unit}"
    return f"{n} B"


def style_axes(ax):
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED)


def footnote(fig, text):
    fig.text(0.01, 0.005, text, fontsize=8.5, color=MUTED, va="bottom")


# ---------------------------------------------------------------------------
# Loading and statistics
# ---------------------------------------------------------------------------

def load(paths):
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    bad = df[df["verified"] != "PASS"]
    if len(bad):
        print(f"WARNING: {len(bad)} rows FAILED verification and are EXCLUDED:")
        print(bad[["config_label", "size_bytes", "trial"]].to_string(index=False))
    df = df[df["verified"] == "PASS"].copy()
    df["config_label"] = pd.Categorical(df["config_label"],
                                        [c for c in CONFIGS if c in set(df["config_label"])], ordered=True)
    return df


def summarize(df):
    """Per config x size statistics."""
    rows = []
    for (cfg, size), g in df.groupby(["config_label", "size_bytes"], observed=True):
        t = g["throughput_MBps"].to_numpy(float)
        lat = g["latency_us_per_op"].to_numpy(float)
        mean, sd = t.mean(), (t.std(ddof=1) if len(t) > 1 else float("nan"))
        hw = ci_halfwidth(t)
        rows.append(dict(
            config_label=cfg, size_bytes=int(size), n=len(t),
            mean_MBps=mean, sd_MBps=sd, cv_pct=sd / mean * 100,
            min_MBps=t.min(), max_MBps=t.max(),
            ci95_low_MBps=mean - hw, ci95_high_MBps=mean + hw,
            mean_latency_us=lat.mean(), sd_latency_us=lat.std(ddof=1) if len(lat) > 1 else float("nan"),
            ci95_latency_us=ci_halfwidth(lat),
            mean_cpu_util_pct=g["cpu_util_pct_one_core"].mean(),
            sd_cpu_util_pct=g["cpu_util_pct_one_core"].std(ddof=1),
            min_cpu_util_pct=g["cpu_util_pct_one_core"].min(),
            max_cpu_util_pct=g["cpu_util_pct_one_core"].max(),
            mean_peak_mem_MB=g["peak_mem_MB"].mean(), max_peak_mem_MB=g["peak_mem_MB"].max(),
            min_iterations=int(g["iterations"].min()), max_iterations=int(g["iterations"].max())))
    s = pd.DataFrame(rows)
    s["config_label"] = pd.Categorical(s["config_label"], CONFIGS, ordered=True)
    return s.sort_values(["config_label", "size_bytes"]).reset_index(drop=True)


def paired_speedups(df):
    """Speedup ratios computed WITHIN each round (trial number), then averaged.

    run_all.ps1 measures every config for a given size right after each other
    in the same round, so dividing within a round cancels slow drift (heat,
    turbo) that affects both configs alike."""
    pairs = [("aesni_on", "aesni_off", "AES-NI on ÷ AES-NI off"),
             ("aesni_on", "chacha_on", "AES-GCM (AES-NI on) ÷ ChaCha20"),
             ("aesni_off", "chacha_on", "AES-GCM (AES-NI off) ÷ ChaCha20"),
             ("chacha_masked_control", "chacha_on", "ChaCha20 with mask ÷ without (control)")]
    piv = df.pivot_table(index=["size_bytes", "trial"], columns="config_label",
                         values="throughput_MBps", observed=True)
    rows = []
    for a, b, name in pairs:
        if a not in piv.columns or b not in piv.columns:
            continue
        for size, g in piv.groupby(level="size_bytes"):
            r = (g[a] / g[b]).dropna().to_numpy(float)
            if len(r) == 0:
                continue
            hw = ci_halfwidth(r)
            rows.append(dict(comparison=name, numerator=a, denominator=b, size_bytes=int(size),
                             n_rounds=len(r), mean_ratio=r.mean(),
                             sd_ratio=r.std(ddof=1) if len(r) > 1 else float("nan"),
                             ci95_low=r.mean() - hw, ci95_high=r.mean() + hw,
                             min_ratio=r.min(), max_ratio=r.max()))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def size_axis(ax, sizes):
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes)
    ax.set_xticklabels([size_label(int(n)) for n in sizes])
    ax.minorticks_off()
    ax.set_xlabel("Data size per encryption (log scale)", color=INK)


def fig_throughput(s, out_png, note, log_y=False):
    fig, ax = plt.subplots(figsize=(10, 6.8))
    for cfg in CONFIGS:
        d = s[s["config_label"] == cfg]
        if d.empty:
            continue
        st = STYLE[cfg]
        yerr = (d["ci95_high_MBps"] - d["mean_MBps"]).fillna(0)
        ax.errorbar(d["size_bytes"], d["mean_MBps"], yerr=yerr, color=st["color"], marker=st["marker"],
                    linestyle=st["linestyle"], linewidth=2, markersize=7, capsize=4, label=st["label"],
                    markeredgecolor="white", markeredgewidth=1)
    size_axis(ax, sorted(s["size_bytes"].unique()))
    if log_y:
        ax.set_yscale("log")
        plain = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
        ax.yaxis.set_major_formatter(plain)
        ax.yaxis.set_minor_formatter(plain)
        ax.grid(True, axis="y", which="both", color=GRID, linewidth=0.6)
    else:
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylabel("Throughput (MB/s, 1 MB = 10^6 bytes)" + (", log scale" if log_y else ""), color=INK)
    ax.set_title("Encryption throughput vs. data size\n" + note, color=INK)
    style_axes(ax)
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    footnote(fig, "Points = mean over trials; error bars = 95% confidence interval of the mean (often smaller than the marker).")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out_png)
    plt.close(fig)


def fig_speedup(sp, out_png, note):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5.8))
    sizes = sorted(sp["size_bytes"].unique())
    x = np.arange(len(sizes))

    # Panel A: AES-NI on / off (linear axis)
    d = sp[sp["numerator"].eq("aesni_on") & sp["denominator"].eq("aesni_off")].set_index("size_bytes").reindex(sizes)
    bars = a1.bar(x, d["mean_ratio"], width=0.6, color=STYLE["aesni_on"]["color"],
                  yerr=d["ci95_high"] - d["mean_ratio"], capsize=4, ecolor=INK)
    for xi, v in zip(x, d["mean_ratio"]):
        a1.text(xi, v * 1.01 + 0.3, f"{v:.1f}×", ha="center", va="bottom", fontsize=10, color=INK)
    a1.set_xticks(x, [size_label(int(n)) for n in sizes])
    a1.set_ylabel("Speedup (× faster)", color=INK)
    a1.set_xlabel("Data size per encryption", color=INK)
    a1.set_title("A. AES-256-GCM: AES-NI on ÷ AES-NI off", color=INK)
    a1.set_ylim(0, max(d["ci95_high"].max(), d["mean_ratio"].max()) * 1.15)
    style_axes(a1)

    # Panel B: AES-GCM ÷ ChaCha20, with and without hardware AES (log axis, 1 = equal speed)
    w = 0.38
    for off, (num, lab, col) in zip((-w / 2, w / 2), [
            ("aesni_on", "AES-GCM with AES-NI ÷ ChaCha20", STYLE["aesni_on"]["color"]),
            ("aesni_off", "AES-GCM without AES-NI ÷ ChaCha20", STYLE["aesni_off"]["color"])]):
        d = sp[sp["numerator"].eq(num) & sp["denominator"].eq("chacha_on")].set_index("size_bytes").reindex(sizes)
        a2.bar(x + off, d["mean_ratio"], width=w, color=col, label=lab,
               yerr=d["ci95_high"] - d["mean_ratio"], capsize=3, ecolor=INK)
        for xi, v in zip(x + off, d["mean_ratio"]):
            a2.text(xi, v * 1.08, f"{v:.2f}×", ha="center", va="bottom", fontsize=8.5, color=INK)
    a2.axhline(1.0, color=INK, linewidth=1)
    a2.text(len(sizes) - 0.5, 1.07, "equal speed", ha="right", va="bottom", fontsize=9, color=MUTED)
    a2.set_yscale("log")
    a2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}×"))
    a2.set_xticks(x, [size_label(int(n)) for n in sizes])
    a2.set_ylabel("AES-GCM speed ÷ ChaCha20 speed (log scale)", color=INK)
    a2.set_xlabel("Data size per encryption", color=INK)
    a2.set_title("B. AES-256-GCM vs ChaCha20-Poly1305\n(above 1 = AES faster, below 1 = ChaCha20 faster)", color=INK, fontsize=12)
    style_axes(a2)
    a2.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2)
    fig.suptitle("Speedups (ratios computed within each round, then averaged) · " + note, color=INK, fontsize=11)
    footnote(fig, "Bars = mean of per-round ratios; error bars = 95% confidence interval.")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(out_png)
    plt.close(fig)


def fig_latency_small(s, out_png, note):
    sizes = [n for n in (1024, 16384) if n in set(s["size_bytes"])]
    fig, axes = plt.subplots(1, len(sizes), figsize=(12, 5.4))
    axes = np.atleast_1d(axes)
    for ax, size in zip(axes, sizes):
        d = s[s["size_bytes"] == size]
        cfgs = [c for c in CONFIGS if c in set(d["config_label"])]
        d = d.set_index("config_label").reindex(cfgs)
        x = np.arange(len(cfgs))
        ax.bar(x, d["mean_latency_us"], color=[STYLE[c]["color"] for c in cfgs], width=0.65,
               yerr=d["ci95_latency_us"], capsize=4, ecolor=INK)
        for xi, v in zip(x, d["mean_latency_us"]):
            ax.text(xi, v * 1.02, f"{v:,.2f}", ha="center", va="bottom", fontsize=10, color=INK)
        ax.set_xticks(x, [SHORT[c] for c in cfgs], rotation=15)
        ax.set_ylabel("Latency per encryption (µs)", color=INK)
        ax.set_title(f"{size_label(size)} messages", color=INK)
        ax.set_ylim(0, d["mean_latency_us"].max() * 1.15)
        style_axes(ax)
    fig.suptitle("Time to encrypt + authenticate one message (lower is better) · " + note, color=INK, fontsize=11)
    footnote(fig, "Bars = mean over trials; error bars = 95% confidence interval. 1 µs = one millionth of a second.")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(out_png)
    plt.close(fig)


def fig_variability(df, out_png, note):
    """Every trial as % deviation from its own config x size mean."""
    d = df.copy()
    d["dev_pct"] = d.groupby(["config_label", "size_bytes"], observed=True)["throughput_MBps"].transform(
        lambda t: (t / t.mean() - 1) * 100)
    sizes = sorted(d["size_bytes"].unique())
    cfgs = [c for c in CONFIGS if c in set(d["config_label"])]
    fig, ax = plt.subplots(figsize=(12, 6))
    span = 0.7
    rng = np.random.default_rng(0)                  # fixed seed: same jitter every time
    for i, size in enumerate(sizes):
        for j, cfg in enumerate(cfgs):
            v = d[(d["size_bytes"] == size) & (d["config_label"] == cfg)]["dev_pct"].to_numpy()
            xc = i - span / 2 + span * (j + 0.5) / len(cfgs)
            ax.boxplot(v, positions=[xc], widths=span / len(cfgs) * 0.8, showfliers=False,
                       medianprops=dict(color=INK), boxprops=dict(color=AXIS), whiskerprops=dict(color=AXIS),
                       capprops=dict(color=AXIS))
            ax.scatter(xc + rng.uniform(-0.03, 0.03, len(v)), v, s=16, color=STYLE[cfg]["color"],
                       marker=STYLE[cfg]["marker"], zorder=3, label=STYLE[cfg]["label"] if i == 0 else None)
    ax.axhline(0, color=INK, linewidth=0.8)
    ax.set_xticks(range(len(sizes)), [size_label(int(n)) for n in sizes])
    ax.set_xlim(-0.6, len(sizes) - 0.4)
    ax.set_xlabel("Data size per encryption", color=INK)
    ax.set_ylabel("Trial throughput vs. its mean (%)", color=INK)
    ax.set_title("Consistency: every trial, as % above or below its configuration's mean\n" + note, color=INK)
    style_axes(ax)
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    footnote(fig, "Dots = individual trials; boxes = middle 50% of trials, line = median, whiskers = rest of the range.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out_png)
    plt.close(fig)


def fig_resources(s, out_png, note):
    cfgs = [c for c in CONFIGS if c in set(s["config_label"])]
    sizes = sorted(s["size_bytes"].unique())
    header = ["Size"] + [f"{SHORT[c]}\nCPU % (mean ± sd)" for c in cfgs] + ["Peak memory MB\n(max over all configs)"]
    rows = []
    for size in sizes:
        d = s[s["size_bytes"] == size].set_index("config_label")
        row = [size_label(int(size))]
        for c in cfgs:
            r = d.loc[c]
            row.append(f"{r['mean_cpu_util_pct']:.1f} ± {r['sd_cpu_util_pct']:.1f}")
        row.append(f"{d['max_peak_mem_MB'].max():,.1f}")
        rows.append(row)
    fig, ax = plt.subplots(figsize=(13, 0.5 * len(rows) + 2.2))
    ax.axis("off")
    t = ax.table(cellText=rows, colLabels=header, loc="center", cellLoc="center", colLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(10.5)
    t.scale(1, 2.0)
    for (r, _), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(weight="bold", color=INK)
            cell.set_height(cell.get_height() * 1.5)
    ax.set_title("Resource use per trial: CPU time ÷ wall time (% of ONE core) and peak memory\n" + note,
                 color=INK, fontsize=12)
    footnote(fig, "Single-threaded: ~100% of one core = ~6% of the whole 16-thread CPU in Task Manager. "
                  "Peak memory = process peak working set (1 MB = 2^20 bytes); mostly our input + output buffers.")
    fig.savefig(out_png, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)


def openssl_speed_table(s):
    """Our harness vs the newest openssl speed CSV at the block sizes both tools use."""
    files = sorted(glob.glob(os.path.join(RESULTS, "openssl_speed_*.csv")))
    if not files:
        return None, None
    o = pd.read_csv(files[-1])
    pairs = [("aesni_on", "aes-256-gcm_on"), ("aesni_off", "aes-256-gcm_masked"),
             ("chacha_on", "chacha20-poly1305_on"), ("chacha_masked_control", "chacha20-poly1305_masked"),
             ("aesni_off_fullmask", "aes-256-gcm_fullmask"), ("chacha_fullmask", "chacha20-poly1305_fullmask")]
    rows = []
    for cfg, run in pairs:
        for size in (1024, 16384):
            ours = s[(s["config_label"] == cfg) & (s["size_bytes"] == size)]
            evp = o[(o["run"] == run) & (o["block_bytes"] == size)]["throughput_MBps"]
            aead = o[(o["run"] == run + "_aead") & (o["block_bytes"] == size)]["throughput_MBps"]
            if ours.empty or evp.empty:
                continue
            rows.append(dict(config_label=cfg, size_bytes=size, ours_MBps=ours["mean_MBps"].iloc[0],
                             ours_ci95=ours["ci95_high_MBps"].iloc[0] - ours["mean_MBps"].iloc[0],
                             speed_evp_MBps=evp.iloc[0], speed_aead_MBps=aead.iloc[0] if not aead.empty else np.nan))
    t = pd.DataFrame(rows)
    if not t.empty:
        t["ours_div_evp"] = t["ours_MBps"] / t["speed_evp_MBps"]
        t["ours_div_aead"] = t["ours_MBps"] / t["speed_aead_MBps"]
    return t, os.path.basename(files[-1])


def fig_vs_openssl(t, ref, out_png, note):
    sizes = [n for n in (1024, 16384) if n in set(t["size_bytes"])]
    fig, axes = plt.subplots(1, len(sizes), figsize=(13, 5.6))
    axes = np.atleast_1d(axes)
    series = [("ours_MBps", "Our harness (complete messages)", "#2a78d6"),
              ("speed_evp_MBps", "openssl speed -evp", "#9aa0a6"),
              ("speed_aead_MBps", "openssl speed -evp -aead", "#5f6368")]
    for ax, size in zip(axes, sizes):
        d = t[t["size_bytes"] == size]
        cfgs = [c for c in CONFIGS if c in set(d["config_label"])]
        d = d.set_index("config_label").reindex(cfgs)
        x = np.arange(len(cfgs))
        w = 0.26
        for k, (col, lab, colr) in enumerate(series):
            ax.bar(x + (k - 1) * w, d[col], width=w, color=colr, label=lab,
                   yerr=d["ours_ci95"] if col == "ours_MBps" else None, capsize=3, ecolor=INK)
        ax.set_xticks(x, [SHORT[c] for c in cfgs], rotation=15)
        ax.set_ylabel("Throughput (MB/s)", color=INK)
        ax.set_title(f"{size_label(size)} messages", color=INK)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        style_axes(ax)
    axes[0].legend(frameon=False, loc="upper center", bbox_to_anchor=(1.1, -0.17), ncol=3)
    fig.suptitle(f"Our harness vs. OpenSSL's own benchmark (same libcrypto DLL) · {note}\nreference: {ref}",
                 color=INK, fontsize=11)
    footnote(fig, "At 1 KB the tools time different per-message work (see docs/SPEED_COMPARISON.md); compare at 16 KB.")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(out_png)
    plt.close(fig)


def fig_drift(df, out_png, note):
    """Throughput by round, normalised to each config x size mean: a slow
    upward/downward trend would show heat or turbo drift during the run."""
    d = df.copy()
    d["norm"] = d.groupby(["config_label", "size_bytes"], observed=True)["throughput_MBps"].transform(
        lambda t: t / t.mean() * 100)
    sizes = sorted(d["size_bytes"].unique())
    cfgs = [c for c in CONFIGS if c in set(d["config_label"])]
    ncol = 3
    nrow = math.ceil(len(sizes) / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(13, 3.6 * nrow), sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, size in zip(axes, sizes):
        for cfg in cfgs:
            g = d[(d["size_bytes"] == size) & (d["config_label"] == cfg)].sort_values("trial")
            st = STYLE[cfg]
            ax.plot(g["trial"], g["norm"], color=st["color"], marker=st["marker"], linestyle=st["linestyle"],
                    linewidth=1.5, markersize=5, label=st["label"])
        ax.axhline(100, color=INK, linewidth=0.8)
        ax.set_title(size_label(int(size)), color=INK)
        ax.set_xlabel("Round (time →)", color=INK)
        style_axes(ax)
    for ax in axes[::ncol]:
        ax.set_ylabel("% of config mean", color=INK)
    for ax in axes[len(sizes):]:
        ax.axis("off")
    axes[0].legend(frameon=False, loc="upper center", bbox_to_anchor=(1.7, -0.25 - 1.0 * (nrow - 1)), ncol=2)
    fig.suptitle("Drift check: throughput of each round relative to its configuration's mean · " + note,
                 color=INK, fontsize=11)
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(out_png)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def fmt(v, digits=1):
    return "n/a" if pd.isna(v) else f"{v:,.{digits}f}"


def markdown(paths, s, sp, ot, ref):
    md = [f"# Statistics for {', '.join(os.path.basename(p) for p in paths)}", "",
          "Generated by `analysis/plot.py`. Throughput uses 1 MB = 10^6 bytes; memory uses 1 MB = 2^20 bytes. "
          "SD = sample standard deviation; CV = SD ÷ mean; 95% CI = mean ± t·SD/√n (Student's t).", "",
          "## Throughput (MB/s)", "",
          "| Config | Size | n | Mean | SD | CV % | Min | Max | 95% CI |",
          "|---|---|---|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        md.append(f"| {r['config_label']} | {size_label(r['size_bytes'])} | {r['n']} | {fmt(r['mean_MBps'])} | "
                  f"{fmt(r['sd_MBps'])} | {fmt(r['cv_pct'], 2)} | {fmt(r['min_MBps'])} | {fmt(r['max_MBps'])} | "
                  f"{fmt(r['ci95_low_MBps'])} – {fmt(r['ci95_high_MBps'])} |")
    md += ["", "## Latency per operation (µs) and resources", "",
           "| Config | Size | Latency mean | Latency SD | Latency 95% CI ± | CPU % of one core (mean, min–max) | Peak mem MB (max) | Iterations per trial |",
           "|---|---|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        it = (f"{r['min_iterations']:,}" if r["min_iterations"] == r["max_iterations"]
              else f"{r['min_iterations']:,}–{r['max_iterations']:,}")
        md.append(f"| {r['config_label']} | {size_label(r['size_bytes'])} | {fmt(r['mean_latency_us'], 3)} | "
                  f"{fmt(r['sd_latency_us'], 3)} | {fmt(r['ci95_latency_us'], 3)} | "
                  f"{fmt(r['mean_cpu_util_pct'])} ({fmt(r['min_cpu_util_pct'])}–{fmt(r['max_cpu_util_pct'])}) | "
                  f"{fmt(r['max_peak_mem_MB'])} | {it} |")
    md += ["", "## Paired speedups (ratio computed within each round, then averaged)", "",
           "| Comparison | Size | Rounds | Mean ratio | SD | 95% CI | Min | Max |", "|---|---|---|---|---|---|---|---|"]
    for _, r in sp.iterrows():
        md.append(f"| {r['comparison']} | {size_label(r['size_bytes'])} | {r['n_rounds']} | {fmt(r['mean_ratio'], 3)} | "
                  f"{fmt(r['sd_ratio'], 3)} | {fmt(r['ci95_low'], 3)} – {fmt(r['ci95_high'], 3)} | "
                  f"{fmt(r['min_ratio'], 3)} | {fmt(r['max_ratio'], 3)} |")
    md += ["", "## Our harness vs `openssl speed` (same libcrypto DLL)", ""]
    if ot is None or ot.empty:
        md.append("No openssl_speed_*.csv found - run scripts/openssl_speed.ps1.")
    else:
        md += [f"Reference file: `{ref}`. Compare at **16 KB**: at small sizes the tools time different "
               "per-message work (see `docs/SPEED_COMPARISON.md`).", "",
               "| Config | Size | Ours (mean ± 95% CI) | speed -evp | ours ÷ evp | speed -aead | ours ÷ aead |",
               "|---|---|---|---|---|---|---|"]
        for _, r in ot.iterrows():
            md.append(f"| {r['config_label']} | {size_label(r['size_bytes'])} | {fmt(r['ours_MBps'])} ± {fmt(r['ours_ci95'])} | "
                      f"{fmt(r['speed_evp_MBps'])} | {fmt(r['ours_div_evp'], 3)} | {fmt(r['speed_aead_MBps'])} | "
                      f"{fmt(r['ours_div_aead'], 3)} |")
    return "\n".join(md) + "\n"


def main():
    def is_raw(p):        # skip the derived CSVs this script writes
        return not p.endswith(("_stats.csv", "_speedup.csv", "_summary.csv"))
    paths = sys.argv[1:] or [p for p in sorted(glob.glob(os.path.join(RESULTS, "run_*_t*.csv"))) if is_raw(p)][-1:]
    paths = [p for p in paths if is_raw(p)]
    if not paths:
        sys.exit("No results/run_*.csv files found. Run scripts/run_all.ps1 first.")
    df = load(paths)
    s = summarize(df)
    sp = paired_speedups(df)
    stem = os.path.splitext(paths[0])[0]
    n = int(s["n"].max())
    note = f"{os.path.basename(stem)} · {n} trials per point · OpenSSL EVP, single thread"

    s.to_csv(stem + "_stats.csv", index=False, float_format="%.4f")
    sp.to_csv(stem + "_speedup.csv", index=False, float_format="%.4f")
    ot, ref = openssl_speed_table(s)

    fig_throughput(s, stem + "_fig1_throughput.png", note)
    fig_throughput(s, stem + "_fig1b_throughput_logy.png", note, log_y=True)
    if not sp.empty:
        fig_speedup(sp, stem + "_fig2_speedup.png", note)
    fig_latency_small(s, stem + "_fig3_latency_small.png", note)
    fig_variability(df, stem + "_fig4_variability.png", note)
    fig_resources(s, stem + "_fig5_resources.png", note)
    if ot is not None and not ot.empty:
        fig_vs_openssl(ot, ref, stem + "_fig6_vs_openssl_speed.png", note)
    if df["trial"].nunique() > 1:
        fig_drift(df, stem + "_fig7_drift.png", note)

    md = markdown(paths, s, sp, ot, ref)
    with open(stem + "_stats.md", "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"Wrote {os.path.basename(stem)}_stats.csv/.md, _speedup.csv and fig1-fig7 PNGs")


if __name__ == "__main__":
    main()
