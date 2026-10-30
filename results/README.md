# Results index

Raw output is never edited or overwritten. Each file's own header or env
snapshot records exactly how it was produced. All runs were on 2026-10-29,
Armoury Crate mode "Performance", on AC power, with OpenSSL 3.6.5.

## `openssl speed` cross-checks

All three runs have identical commands. **What `_masked` means changed between
them**, because each run taught us something about the mask (see the README,
"Why not the proposal's mask?"). Every `.txt` file records its exact
`OPENSSL_ia32cap` value and OpenSSL's resulting CPUINFO bits.

| Timestamp | `_masked` used | What it showed | Use it for |
|---|---|---|---|
| `20261029_171601` | `~0x200000200000000` (proposal's original) | AES-GCM 33× slower, but the ChaCha20 control also 2.3× slower: this string zeroes OpenSSL's AVX2/AVX-512 word too | History only |
| `20261029_172035` | `~0x200000200000000:~0x0:~0x0` (AES-NI + PCLMULQDQ only) | AES-GCM **unchanged**: on Zen 4, OpenSSL uses VAES/VPCLMULQDQ, which this leaves enabled | History only |
| `20261029_172506` | `~0x200000200000000:~0x60000000000:~0x0` (all hardware AES) | AES-GCM 34× slower, ChaCha20 control unchanged within noise | **Current reference** |

Run `20261029_172506` also includes `_fullmask` (the original string) and
`_vaes_masked` (only VAES + VPCLMULQDQ hidden, classic AES-NI still on)
diagnostics.

## Method probe (diagnostic)

| File | What it is |
|---|---|
| `method_probe_20261029_181211.csv` | `src/method_probe.cpp`: times our per-message method against `openssl speed`'s, side by side. Rows: 1 KB (no mask, all-hardware-AES mask) and 16 KB, 5 reps × 1 s each; then 256 B / 2 KB / 4 KB / 8 KB, no mask, 3 reps × 0.5 s each. Explains the 1 KB gap; see `docs/SPEED_COMPARISON.md`. |

## Harness runs

| Run | Trials | Configs | Notes |
|---|---|---|---|
| `run_20261029_172841_t3` | 3 | 4 experiment configs + 2 `*_fullmask` diagnostics | Week 2 preliminary run. 108/108 trials verified PASS. `_env.md` = environment at run time, `_summary.md` = table + openssl speed comparison, `.png` = graphs |
