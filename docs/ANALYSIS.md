# Analysis

**Data used** (all in `results/`):

| Source | What it is |
|---|---|
| `run_20261117_103103_t10.csv` | Main experiment: 4 configs × 6 sizes × 10 trials, interleaved rounds, all 240 trials verified |
| `run_20261117_103103_t10_stats.md` / `_stats.csv` / `_speedup.csv` | Statistics computed from it by `analysis/plot.py` |
| `size_check_20261117_105507*` | Follow-up check of the large-size results (shuffled order + cooldown), 45 trials |
| `openssl_speed_20261117_104621*` | `openssl speed` cross-check, same session |
| `method_probe_20261029_181211.csv` | Diagnostic explaining the 1 KB difference with `openssl speed` (see `docs/SPEED_COMPARISON.md`) |

Sections are labelled **Measured** (numbers straight from the files above) or
**Interpretation** (our explanation, from the OpenSSL source and general
computer-architecture knowledge). Throughput: 1 MB = 10^6 bytes. Sizes:
1 KB = 1,024 bytes. "±" is the 95% confidence interval of the mean unless
stated otherwise.

---

## 1. Throughput (Measured)

Mean MB/s over 10 trials (± 95% CI), from `run_20261117_103103_t10_stats.md`:

| Size | aesni_on | aesni_off | chacha_on | chacha_masked_control |
|---|---|---|---|---|
| 1 KB | 2,630.7 ± 61.4 | 153.6 ± 4.1 | 1,008.3 ± 31.2 | 1,021.1 ± 16.8 |
| 16 KB | 6,071.3 ± 89.3 | 166.4 ± 4.3 | 1,913.6 ± 26.2 | 1,911.0 ± 23.3 |
| 256 KB | 6,693.8 ± 59.1 | 169.0 ± 4.2 | 2,009.0 ± 27.5 | 1,992.2 ± 17.9 |
| 1 MB | 6,522.7 ± 167.6 | 164.2 ± 8.5 | 1,966.5 ± 43.4 | 1,922.0 ± 69.8 |
| 64 MB | 6,039.5 ± 126.7 | 165.3 ± 4.6 | 1,759.0 ± 60.9 | 1,737.6 ± 36.6 |
| 1 GB | 5,961.1 ± 154.6 | 152.2 ± 1.7 | 1,708.1 ± 45.0 | 1,714.7 ± 62.5 |

### Speedups (Measured)

Computed **within each round** (the two configs ran back to back at the same
size), then averaged over the 10 rounds (`_speedup.csv`):

| Size | AES-NI on ÷ off | AES-GCM (AES-NI on) ÷ ChaCha20 | AES-GCM (AES-NI off) ÷ ChaCha20 |
|---|---|---|---|
| 1 KB | **17.14×** (16.77–17.50) | 2.61× (2.51–2.72) | 0.153× (0.147–0.158) |
| 16 KB | **36.52×** (35.84–37.20) | 3.17× (3.12–3.22) | 0.087× (0.085–0.089) |
| 256 KB | **39.64×** (38.64–40.64) | 3.33× (3.30–3.37) | 0.084× (0.082–0.086) |
| 1 MB | **39.84×** (38.45–41.23) | 3.32× (3.24–3.40) | 0.083× (0.080–0.087) |
| 64 MB | **36.58×** (35.47–37.69) | 3.44× (3.32–3.56) | 0.094× (0.090–0.099) |
| 1 GB | **39.17×** (38.38–39.96) | 3.49× (3.40–3.59) | 0.089× (0.087–0.092) |

In words:
- **With** its hardware, AES-256-GCM is **2.6–3.5× faster** than ChaCha20-Poly1305.
- **Without** it, the order flips. AES-GCM runs at 0.083–0.153× of ChaCha20's
  speed, so **ChaCha20 is about 6.5× (1 KB) to 12× (1 MB) faster**. These are
  the reciprocals of the mean ratios.
- Hardware AES makes AES-GCM **17× faster at 1 KB and 36–40× faster from 16 KB up**.

The same session's `openssl speed -evp` run agrees on the size of the effect:
5,695.6 → 153.3 MB/s at 16 KB, **37.1×**.

## 2. System-level explanation (Interpretation)

Which code runs is documented, with source file and line references, in
`docs/CODE_PATHS.md`. Summary:

**AES-NI on.** On Zen 4, OpenSSL 3.6.5 picks its AVX-512 **VAES + VPCLMULQDQ**
GCM code. One AES-NI instruction performs a whole AES round on a 16-byte
block in dedicated silicon. The VAES versions do that for several blocks per
instruction across a 512-bit register. GCM's authentication (GHASH) needs
multiplication in GF(2^128). PCLMULQDQ/VPCLMULQDQ is a carry-less
multiplier built for exactly that. So both halves of AES-GCM run on
special-purpose hardware. Our baseline is therefore "AES-NI + VAES", not
classic AES-NI alone. The `openssl speed` diagnostic that hides only VAES +
VPCLMULQDQ (classic AES-NI path) gave 2,102.1 MB/s at 16 KB, against
5,695.6 with VAES (`openssl_speed_20261117_104621`).

**AES-NI off.** OpenSSL falls back to **bit-sliced AES** (BSAES, SSSE3), which
processes 8 blocks at once using only logic instructions. Short tails use
**table-lookup AES**, and GHASH uses a **4-bit lookup table** instead of a
multiply instruction. All of it is hand-written assembly, so this is
OpenSSL's best software path, not naïve C. AES was designed around byte
substitutions (S-boxes) and GF(2^8) arithmetic. These are cheap in
hardware but expensive in general-purpose instructions, and GHASH's
128-bit carry-less multiply is the same story. That is why removing the
hardware costs a factor of about 37–40 on large data.

**ChaCha20-Poly1305.** ChaCha20 was designed for software. It uses only
32-bit **A**dd, **R**otate and **X**or (ARX), which every CPU executes in one
cycle, and these vectorise naturally. OpenSSL uses AVX-512 (16 lanes ×
32 bits) for ChaCha20 and AVX/AVX2/AVX-512 for Poly1305's 130-bit
arithmetic. **The AES-NI mask does not touch these SIMD units**, which is
correct for this comparison: SIMD is general-purpose hardware, while the
mask removes only AES-specific hardware. So the comparison is "AES with its
dedicated hardware vs ChaCha20 with ordinary vector hardware", plus "AES
without its dedicated hardware". ChaCha20 sits in between: much faster than
software AES, but hardware AES-GCM is 2.61–3.49× faster than it on this CPU.

## 3. Small messages vs large data: where the curves change shape

**Measured.** Every config is slowest at 1 KB and reaches its plateau by
16–256 KB. From 1 KB to 16 KB:
- aesni_on rises 2,630.7 → 6,071.3 MB/s (2.3×)
- chacha_on rises 1,008.3 → 1,913.6 (1.9×)
- aesni_off rises only 153.6 → 166.4 (1.08×)

A two-point fit through the 16 KB and 256 KB latencies
(`_stats.md`, "Per-message overhead estimate") gives:

| Config | Fixed cost per message | Cost per byte | Break-even size |
|---|---|---|---|
| aesni_on | 0.269 µs | 0.1484 ns | 1,810 bytes |
| chacha_on | 0.434 µs | 0.4963 ns | 874 bytes |
| aesni_off | (model fits poorly: predicts 7.735 µs at 1 KB, measured 6.675) | 5.9161 ns | not reliable |

The ChaCha20 fixed cost agrees with the separate `method_probe` measurement:
a complete message costs about 0.39–0.44 µs more than streaming the same
bytes (`docs/SPEED_COMPARISON.md`).

**Interpretation.** Every message has fixed work besides the bulk
encryption:
- loading a new nonce
- for GCM: encrypting the counter block used for the tag
- for ChaCha20-Poly1305: generating a fresh Poly1305 key with one ChaCha20 block
- finalising the authenticator and copying out the tag
- the OpenSSL EVP function-call layers

When the per-byte cost is tiny (hardware AES: 0.15 ns/byte), that fixed cost
dominates small messages: below about 1.8 KB, more than half of AES-GCM's
time is overhead. Software AES spends about 6 ns on *every* byte, so the
fixed cost hardly matters and its curve is nearly flat. This is why the
AES-NI speedup is "only" 17× at 1 KB but 36–40× once messages are large.
Hardware accelerates the per-byte work, not the per-message bookkeeping.

## 4. Cache effects at 64 MB and 1 GB

Cache sizes from `docs/ENVIRONMENT.md` (Windows reports totals):
- L1: 512 KB total
- L2: 8,192 KB total, 1 MB per core across 8 cores
- L3: 16,384 KB shared

Each encryption reads the input and writes an output of the same size, so
the working set is about 2× the message size:

| Size | Working set | Where it fits |
|---|---|---|
| 256 KB | 512 KB | fits one core's L2 |
| 1 MB | 2 MB | spills out of L2 into L3 |
| 64 MB | 128 MB | 8× bigger than L3, comes from DRAM |
| 1 GB | 2 GB | comes from DRAM |

**Measured (main run).** All configs peak at 256 KB and are slower at
64 MB and 1 GB: aesni_on 89.1%, chacha_on 85.0% and aesni_off 90.0% of their
256 KB speed at 1 GB.

**This surprised us, so we investigated.** A drop for software AES at
1 GB (90.0%) cannot be memory bandwidth. At 152 MB/s it moves well under
1 GB/s. We noticed a flaw in our own run order: every round went
1 KB → 1 GB, so **the large sizes always ran last, after the CPU had been
under load the longest**. Size was mixed up with heat.

`scripts/size_order_check.ps1` re-measured 256 KB, 64 MB and 1 GB for three
configs. It ran in a **shuffled order with a 10 s cooldown before every
trial**, 5 rounds (`size_check_20261117_105507_summary.md`):

| Config | Size | Main run: % of 256 KB | Check: % of 256 KB |
|---|---|---|---|
| aesni_on | 64 MB | 90.2% | **100.9%** |
| aesni_on | 1 GB | 89.1% | **95.6%** |
| aesni_off | 64 MB | 97.8% | **98.6%** |
| aesni_off | 1 GB | 90.0% | **98.2%** |
| chacha_on | 64 MB | 87.6% | **98.1%** |
| chacha_on | 1 GB | 85.0% | **94.9%** |

**Conclusions:**
- **Most of the large-size slowdown in the main run was an artifact of run
  order (heat / turbo), not of the caches.**
- At 64 MB, three times past L3, there is essentially no slowdown once order
  and temperature are controlled.
- A **small slowdown at 1 GB remains**. For ChaCha20 it is outside the
  confidence intervals: 1,946.7 ± 83.7 vs 2,051.8 ± 37.0 MB/s. For aesni_on,
  5 trials cannot separate it from noise (256 KB: 6,587.5 ± 691.1).
- The remaining drop is not proportional to data rate (aesni_on moves about
  6.3 GB/s, ChaCha20 about 1.9 GB/s, and both lose about 5%). So we do **not**
  see memory bandwidth limiting throughput.

**Interpretation.** The access pattern is a simple forward stream, which the
CPU's hardware prefetchers predict well, so DRAM latency is mostly hidden.
Possible causes of the small 1 GB effect, which we did not test, include
TLB misses on 4 KB pages (a 2 GiB working set spans 524,288 pages) and
residual heat from generating 1 GB of random data at the start of each trial.

The cooled trials were also faster overall. For example, aesni_off at 256 KB
measured 183.7 MB/s in the check vs 169.0 in the main run. **Absolute numbers
on this laptop depend on how hot it is**, which is why we compare configs
using paired, same-round ratios.

## 5. Resource use (Measured, then Interpretation)

- **CPU utilisation:** CPU time ÷ wall time, averaged per config × size, was
  96.7–99.4% of one core. Individual trials ranged 88.8–101.2%
  (`_stats.md`). Values just over 100% come from Windows' ~15.6 ms CPU-timer
  granularity; values below 100% mean the thread was briefly descheduled.
  The benchmark is single-threaded, so it keeps **one** of the 16 logical
  processors busy. Task Manager would show that as about 6% of the whole CPU.
  The ciphers do not differ in CPU utilisation; they differ in how much work
  they finish per CPU-second.
- **Peak memory:** 8.5 MB at 1–16 KB, 9.0 MB at 256 KB, 10.5 MB at 1 MB,
  136.5 MB at 64 MB and 2,056.5 MB at 1 GB, identical for all four configs.
  That is 2 × the message size (input + output buffer) plus about 8.5 MB of
  program and OpenSSL. Neither cipher needs meaningful extra memory. Their
  internal state (key schedule, GHASH tables, ChaCha20 state) is a few KB.

## 6. Did the ChaCha20 masked control stay unchanged? (Measured)

Yes. The per-round ratio "ChaCha20 with mask ÷ without" was 0.978–1.014
across sizes, and **every 95% CI includes 1.00** (`_speedup.csv`). The
same-session `openssl speed` gave 1,938.1 vs 1,916.8 MB/s at 16 KB (1.01×).

Note: with the proposal's *original* mask string (`~0x200000200000000`),
ChaCha20 was **not** unchanged. It dropped about 2.4× (1,974.8 → 817.8 MB/s at
256 KB in `run_20261029_172841_t3`), because that string also zeroes
OpenSSL's AVX2/AVX-512 capability word. The control did its job by
catching that.

## 7. Agreement with `openssl speed` (Measured, then Interpretation)

At **16 KB** our harness is within 10% of `openssl speed` in every config.
Ours ÷ `-evp`: aesni_on 1.066, aesni_off 1.085, chacha_on 0.987,
control 0.997. Ours ÷ `-aead`: 0.990, 0.925, 0.969, 0.903.

At **1 KB** they differ: aesni_on ours ÷ evp 1.367, chacha_on 0.580. This is
explained and measured in `docs/SPEED_COMPARISON.md`. `openssl speed`
streams ChaCha20-Poly1305 without any per-message work, even with `-aead`,
because its AEAD loop only handles GCM/CCM/OCB/SIV modes. It also re-loads
the AES-GCM key for every message. Our harness times complete messages with
the key reused, which is what a TLS connection does. **Neither tool is
wrong; they measure different things at small sizes.**

Single `openssl speed` runs also vary between sessions. For example,
aes-256-gcm masked at 16 KB was 175.4 MB/s on 2026-10-29
(`openssl_speed_20261029_172506`) and 153.3 MB/s on 2026-11-17. So we
treat agreement within about 10% as good.

## 8. Variability (Measured)

Coefficient of variation per config × size, in `_stats.md`:
- aesni_on 1.23–3.63%
- aesni_off 1.60–7.24%
- chacha_on 1.91–4.84%
- control 1.26–5.10%

The largest single outlier was one aesni_off trial at 1 MB, 18.3% below its
mean (Figure 4). In Figure 7 (drift), round 3 at 1 MB dipped for **all four
configs at once**, which points to a short burst of background activity
rather than a cipher effect. Because the paired ratios are computed within a
round, such events affect both sides of a ratio and largely cancel. No
steady drift across the 10 rounds is visible.

## 9. Trade-offs: when to use which (Interpretation)

- **CPU with AES hardware** (any recent x86 or ARMv8 with crypto extensions):
  AES-256-GCM is the faster choice, 2.6–3.5× ChaCha20 here, and the
  hardware instructions also run in constant time.
- **CPU without AES hardware** (older or low-end processors, some embedded and
  mobile chips): ChaCha20-Poly1305 is the clear choice. It was 6.5–12×
  faster than software AES-GCM here. It is also naturally constant-time,
  while table-based software AES can leak key information through cache
  timing.
- **Small messages** (packets of about 1 KB): per-message overhead matters
  more for ChaCha20 (0.434 µs fixed cost vs 0.269 µs for hardware AES-GCM),
  so AES-GCM's advantage holds there too, though it shrinks to 2.6×.
- This is why TLS 1.3 offers both suites. A server can prefer AES-GCM but
  honour clients without AES hardware that ask for ChaCha20-Poly1305.

## 10. Results that surprised us

1. **"AES-NI off" did not turn off hardware AES at first.** Hiding the AES-NI
   bit left AES-GCM at full speed, because on Zen 4 OpenSSL uses the newer
   VAES instructions, and their check does not look at the AES-NI bit.
2. **The proposal's mask also disabled AVX2/AVX-512.** Without the `:` parts
   OpenSSL zeroes the other capability words, which slowed ChaCha20 2.4×.
3. **The software fallback is bit-sliced AES (BSAES), not VPAES.** OpenSSL
   checks BSAES first; both need only SSSE3.
4. **The apparent "cache cliff" at 64 MB / 1 GB was mostly our run order.**
5. **Per-message overhead hurts ChaCha20 more than AES-GCM.** About 0.43 µs vs
   0.27 µs per message, so `openssl speed`'s streaming ChaCha20 numbers
   overstate small-message speed.

## 11. Limitations

- **One machine, one OpenSSL version** (3.6.5), one compiler (MSVC 19.44),
  Windows 11. Results on other CPUs, especially ones without VAES/AVX-512, will
  differ. The classic AES-NI path was 2.7× slower than VAES in this session's
  `openssl speed` (2,102.1 vs 5,695.6 MB/s at 16 KB).
- **Single-threaded.** Real servers encrypt many connections in parallel; we
  did not measure scaling across cores.
- **Laptop turbo and heat.** Absolute numbers move by several percent with
  temperature (Section 4). The main run fixed config-order bias by
  interleaving and rotating, but **not size-order bias**: sizes always ran
  1 KB → 1 GB. Cross-size comparisons in the main run are therefore biased
  against the large sizes. Use the size check for those. Next time: shuffle
  sizes too.
- **We could not measure clock speed.** Windows' `% Processor Performance`
  counter stayed at about 60–61% of base for every trial in the size check, so
  on this laptop it does not track the real turbo clock.
- **Background activity.** Before the main run the whole-CPU load was
  8.6–15.8%, mostly a Windows notification service (WpnUserService). The
  battery was 78% and charging at the start (the Oct 29 runs were at 100%).
  OneDrive was running; whether its sync was paused was not recorded.
- **"AES-NI off" means OpenSSL's best software path** (bit-sliced SSSE3 AES +
  4-bit table GHASH), not plain C and not a CPU without AES hardware.
- **SIMD stays enabled for ChaCha20** (AVX-512), by design. On a CPU without
  AVX2/AVX-512, ChaCha20 would be slower. Our `chacha_fullmask` diagnostic
  shows about 2.4×.
- **1 GB trials run only 1–7 encryptions** (aesni_off: 1). They are long in
  wall time (≥ 1 s), so timer resolution is fine, but each trial is a single
  large sample.
- **Peak memory is per process**, measured at the end of the trial, so it
  includes buffers and OpenSSL, not just the cipher.
- **CPU time resolution** is about 15.6 ms (Windows timer), about 1.6% of a
  1-second trial.
