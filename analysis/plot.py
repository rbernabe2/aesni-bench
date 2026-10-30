"""plot.py - preliminary graphs and tables from bench.exe CSV files.

Usage (from the repo root):
    py analysis/plot.py                         # newest results/run_*.csv
    py analysis/plot.py results/run_A.csv ...   # specific file(s), combined

Writes next to the (first) input CSV, named after it:
    <run>_throughput.png   throughput vs data size, one line per config,
                           error bars = sample standard deviation over trials
    <run>_throughput_logy.png  same, with a log throughput axis
    <run>_table.png        the mean +- std dev table as an image (for slides)
    <run>_summary.csv      the same table as data
    <run>_summary.md       Markdown table + speedups + openssl speed comparison

Raw CSVs are only read, never modified.
"""

import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")                 # draw to files, no window needed
import matplotlib.pyplot as plt
import matplotlib.ticker
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "results")

# Fixed config order and style. Color follows the configuration (never its
# rank). Marker + line style give a second cue so the lines can be told apart
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
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e1e0d9"


def size_label(n):
    """1024 -> '1 KB', 1048576 -> '1 MB' (binary units, as bench.exe uses)."""
    for unit, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k and n % k == 0:
            return f"{n // k} {unit}"
    return f"{n} B"


def load(paths):
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    bad = df[df["verified"] != "PASS"]
    if len(bad):
        print(f"WARNING: {len(bad)} rows FAILED verification and are EXCLUDED:")
        print(bad[["config_label", "size_bytes", "trial"]].to_string(index=False))
    return df[df["verified"] == "PASS"]


def summarize(df):
    """Mean and sample std dev (n-1) per config x size, plus speedup vs baseline."""
    g = df.groupby(["config_label", "size_bytes"])
    s = g.agg(trials=("trial", "count"),
              mean_MBps=("throughput_MBps", "mean"), sd_MBps=("throughput_MBps", "std"),
              mean_latency_us=("latency_us_per_op", "mean"), sd_latency_us=("latency_us_per_op", "std"),
              mean_cpu_util_pct=("cpu_util_pct_one_core", "mean"),
              max_peak_mem_MB=("peak_mem_MB", "max"),
              iterations=("iterations", "first")).reset_index()
    base = s[s["config_label"] == "aesni_on"].set_index("size_bytes")["mean_MBps"]
    # speedup of the baseline over this config = how many times faster AES-NI-on is
    s["baseline_speedup_x"] = s["size_bytes"].map(base) / s["mean_MBps"]
    s["config_label"] = pd.Categorical(s["config_label"], CONFIGS, ordered=True)
    return s.sort_values(["config_label", "size_bytes"]).reset_index(drop=True)


def plot_throughput(s, out_png, title_note, log_y=False):
    """log_y=True draws a second version with a log throughput axis, so the
    slow configs (~170 MB/s) are readable next to the fast ones (~6,500 MB/s)."""
    fig, ax = plt.subplots(figsize=(9, 6.5), dpi=150)
    for cfg in CONFIGS:
        d = s[s["config_label"] == cfg]
        if d.empty:
            continue
        st = STYLE[cfg]
        ax.errorbar(d["size_bytes"], d["mean_MBps"], yerr=d["sd_MBps"].fillna(0),
                    color=st["color"], marker=st["marker"], linestyle=st["linestyle"],
                    linewidth=2, markersize=7, capsize=4, label=st["label"],
                    markeredgecolor="white", markeredgewidth=1)
    sizes = sorted(s["size_bytes"].unique())
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes)
    ax.set_xticklabels([size_label(int(n)) for n in sizes])
    ax.minorticks_off()
    if log_y:
        ax.set_yscale("log")
        plain = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
        ax.yaxis.set_major_formatter(plain)
        ax.yaxis.set_minor_formatter(plain)
    else:
        ax.set_ylim(bottom=0)
    ax.set_xlabel("Data size per encryption (log scale)", color=INK)
    ax.set_ylabel("Throughput (MB/s, 1 MB = 10^6 bytes)" + (", log scale" if log_y else ""), color=INK)
    ax.set_title("Encryption throughput vs. data size\n" + title_note, color=INK, fontsize=11)
    ax.grid(True, axis="y", which="both" if log_y else "major", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)
    # Legend below the plot so it never covers a line.
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    fig.text(0.01, 0.01, "Points = mean over trials; error bars = ±1 sample standard deviation.",
             fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out_png)
    plt.close(fig)


def fmt_pm(m, sd, digits=0):
    sd = 0.0 if pd.isna(sd) else sd
    return f"{m:,.{digits}f} ± {sd:,.{digits}f}"


def table_rows(s):
    rows = []
    for _, r in s.iterrows():
        rows.append([r["config_label"], size_label(int(r["size_bytes"])), int(r["trials"]),
                     fmt_pm(r["mean_MBps"], r["sd_MBps"]),
                     fmt_pm(r["mean_latency_us"], r["sd_latency_us"], 2),
                     f"{r['mean_cpu_util_pct']:.1f}", f"{r['max_peak_mem_MB']:.1f}",
                     f"{r['baseline_speedup_x']:.2f}x"])
    return rows


HEADERS = ["Config", "Size", "Trials", "Throughput MB/s (mean ± sd)", "Latency µs/op (mean ± sd)",
           "CPU % of 1 core", "Peak mem MB", "Baseline is N× faster"]


def plot_table(s, out_png):
    rows = table_rows(s)
    headers = [h.replace(" (", "\n(").replace("Baseline is", "Baseline is\n") for h in HEADERS]
    fig, ax = plt.subplots(figsize=(13, 0.3 * len(rows) + 1.2), dpi=150)
    ax.axis("off")
    t = ax.table(cellText=rows, colLabels=headers, loc="center", cellLoc="right", colLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    t.auto_set_column_width(list(range(len(headers))))   # widen columns to fit their text
    t.scale(1, 1.3)
    for (r, _), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(weight="bold", color=INK)
            cell.set_height(cell.get_height() * 2)       # room for two-line headers
    fig.savefig(out_png, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def openssl_comparison(s):
    """Compare our harness with the newest `openssl speed` CSV at the block sizes both tools use."""
    files = sorted(glob.glob(os.path.join(RESULTS, "openssl_speed_*.csv")))
    if not files:
        return "No openssl_speed_*.csv found - run scripts/openssl_speed.ps1.\n"
    o = pd.read_csv(files[-1])
    pairs = [("aesni_on", "aes-256-gcm_on"), ("aesni_off", "aes-256-gcm_masked"),
             ("chacha_on", "chacha20-poly1305_on"), ("chacha_masked_control", "chacha20-poly1305_masked"),
             ("aesni_off_fullmask", "aes-256-gcm_fullmask"), ("chacha_fullmask", "chacha20-poly1305_fullmask")]
    lines = [f"Reference file: `{os.path.basename(files[-1])}`",
             "",
             "Compare at **16 KB**. At small sizes the tools time different work per message: "
             "`openssl speed` streams ChaCha20-Poly1305 (no nonce/Final/tag per message) and "
             "re-loads the AES-GCM key every message, while bench.exe times complete messages "
             "with the key reused. See `docs/SPEED_COMPARISON.md`.",
             "",
             "| Config | Size | Our harness MB/s | openssl speed -evp MB/s | ours / evp | openssl speed -aead MB/s | ours / aead |",
             "|---|---|---|---|---|---|---|"]
    for cfg, run in pairs:
        for size in (1024, 16384):
            ours = s[(s["config_label"] == cfg) & (s["size_bytes"] == size)]["mean_MBps"]
            evp = o[(o["run"] == run) & (o["block_bytes"] == size)]["throughput_MBps"]
            aead = o[(o["run"] == run + "_aead") & (o["block_bytes"] == size)]["throughput_MBps"]
            if ours.empty or evp.empty:
                continue
            ov, ev = ours.iloc[0], evp.iloc[0]
            av = aead.iloc[0] if not aead.empty else None
            lines.append(f"| {cfg} | {size_label(size)} | {ov:,.0f} | {ev:,.0f} | {ov / ev:.2f} | "
                         + (f"{av:,.0f} | {ov / av:.2f} |" if av else "n/a | n/a |"))
    return "\n".join(lines) + "\n"


def main():
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(RESULTS, "run_*.csv")))[-1:]
    if not paths:
        sys.exit("No results/run_*.csv files found. Run scripts/run_all.ps1 first.")
    df = load(paths)
    s = summarize(df)
    stem = os.path.splitext(paths[0])[0]
    trials = int(df.groupby(["config_label", "size_bytes"])["trial"].count().max())
    note = f"{os.path.basename(stem)} · up to {trials} trials per point · OpenSSL EVP, single thread"

    plot_throughput(s, stem + "_throughput.png", note)
    plot_throughput(s, stem + "_throughput_logy.png", note, log_y=True)
    plot_table(s, stem + "_table.png")
    s.to_csv(stem + "_summary.csv", index=False, float_format="%.3f")

    md = [f"# Summary of {', '.join(os.path.basename(p) for p in paths)}", "",
          "Mean ± sample standard deviation over trials. Throughput uses 1 MB = 10^6 bytes; "
          "peak memory uses 1 MB = 2^20 bytes. \"Baseline is N× faster\" = mean throughput of "
          "aesni_on divided by this config's mean throughput at the same size.", "",
          "| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
    md += ["| " + " | ".join(str(c) for c in row) + " |" for row in table_rows(s)]
    md += ["", "## Comparison with `openssl speed` (same libcrypto DLL)", "", openssl_comparison(s)]
    with open(stem + "_summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print("\n".join(md))
    print(f"\nWrote {stem}_throughput.png, _table.png, _summary.csv, _summary.md")


if __name__ == "__main__":
    main()
