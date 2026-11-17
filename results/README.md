# Results index

Raw output is never edited or overwritten. Each file's own header or env
snapshot records exactly how it was produced. Every run used OpenSSL 3.6.5,
Armoury Crate mode "Performance" and AC power.

**Use for the final results:** `run_20261117_103103_t10*`, plus
`openssl_speed_20261117_104621*` and `size_check_20261117_105507*`.

## Harness runs

| Run | Date | Trials | Configs | Notes |
|---|---|---|---|---|
| `run_20261029_172841_t3` | 2026-10-29 | 3 | 4 experiment configs + 2 `*_fullmask` diagnostics | Week 2 preliminary run, configs run one after another. 108/108 trials verified. `_summary.md` / `_table.png` / `_throughput*.png` were made by the Week 2 version of `plot.py`. |
| **`run_20261117_103103_t10`** | 2026-11-17 | 10 | 4 experiment configs | **Week 3 final run.** Interleaved rounds with rotated config order, 30 s cooldown, ≥ 1.0 s per trial. 240/240 trials verified, 14.9 min. Files: `.csv` raw · `.log` console output · `_env.md` environment at start · `_run.json` parameters and start/end time · `_stats.md` / `_stats.csv` statistics · `_speedup.csv` paired speedups · `_fig1`…`_fig7` figures. |

## Size-order check (diagnostic)

| File | What it is |
|---|---|
| `size_check_20261117_105507.csv` | `scripts/size_order_check.ps1`: 256 KB / 64 MB / 1 GB × aesni_on / aesni_off / chacha_on × 5 rounds, shuffled order (seed in `_run.json`), 10 s cooldown before every trial. 45/45 verified. |
| `size_check_20261117_105507_perf.csv` | Windows `% Processor Performance` per logical processor, 1 sample/s, logged during the check. It stayed at about 60–61% for every trial, so it is not a usable clock measurement on this laptop. |
| `size_check_20261117_105507_summary.md` / `_fig.png` | `analysis/size_check.py`: large sizes as % of 256 KB, main run vs check. Shows most of the main run's large-size drop was run order (heat), not cache. |

## `openssl speed` cross-checks

The commands are identical across runs. **What `_masked` means changed between
the three Oct 29 runs**, because each run taught us something about the mask
(see the main README). Every `.txt` file records its exact `OPENSSL_ia32cap`
value and OpenSSL's resulting CPUINFO bits.

| Timestamp | `_masked` used | What it showed | Use it for |
|---|---|---|---|
| `20261029_171601` | `~0x200000200000000` (proposal's original) | AES-GCM 33× slower, but the ChaCha20 control also 2.3× slower: this string zeroes OpenSSL's AVX2/AVX-512 word too | History only |
| `20261029_172035` | `~0x200000200000000:~0x0:~0x0` (AES-NI + PCLMULQDQ only) | AES-GCM **unchanged**: on Zen 4, OpenSSL uses VAES/VPCLMULQDQ, which this leaves enabled | History only |
| `20261029_172506` | `~0x200000200000000:~0x60000000000:~0x0` (all hardware AES) | AES-GCM 34× slower, ChaCha20 control unchanged within noise | Week 2 reference |
| **`20261117_104621`** | same as `20261029_172506` | AES-GCM 5,695.6 → 153.3 MB/s at 16 KB (37.1×); ChaCha20 1,938.1 → 1,916.8 (1.01×) | **Week 3 reference** (same session as the final run) |

Runs `20261029_172506` and `20261117_104621` also include `_fullmask` (the
original string) and `_vaes_masked` (only VAES + VPCLMULQDQ hidden, classic
AES-NI still on) diagnostics.

## Method probe (diagnostic)

| File | What it is |
|---|---|
| `method_probe_20261029_181211.csv` | `src/method_probe.cpp`: times our per-message method against `openssl speed`'s, side by side. Rows: 1 KB (no mask, all-hardware-AES mask) and 16 KB, 5 reps × 1 s each; then 256 B / 2 KB / 4 KB / 8 KB, no mask, 3 reps × 0.5 s each. Explains the 1 KB gap; see `docs/SPEED_COMPARISON.md`. |

## Demo runs

`demo/demo_<timestamp>.csv` / `.png`: output of `scripts/demo.ps1`. Short
3-trial runs for showing the method live. **Not used** for any reported result.
