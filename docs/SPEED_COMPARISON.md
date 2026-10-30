# Why our 1 KB numbers differ from `openssl speed`

**Short answer:** the two tools time different work per message. Our harness
is not slower or faster than it should be. At 16 KB and above, where
per-message work is small next to the data, the tools agree within 8%.

Data: `results/method_probe_20261029_181211.csv` (made with `src/method_probe.cpp`)
and the preliminary run `results/run_20261029_172841_t3`. Everything was measured
on 2026-10-29 with OpenSSL 3.6.5, on AC power, in Armoury Crate "Performance" mode.

## What each tool does per message (from OpenSSL 3.6.5 `apps/speed.c`)

| | `bench.exe` (ours) | `openssl speed -evp X` | `openssl speed -evp X -aead` |
|---|---|---|---|
| AES-256-GCM | new nonce → Update → Final → get tag. Key schedule done once. | set IV length → **load key again** → Update → Final | same, plus 13 bytes of AAD |
| ChaCha20-Poly1305 | new nonce → Update → Final → get tag | **Update only**, one long stream: no nonce, no Final, no tag | same as `-evp` |

Why ChaCha20 is streamed: `speed.c` uses its per-message AEAD loop only for
ciphers whose mode is GCM, CCM, OCB, SIV or GCM-SIV. OpenSSL reports
ChaCha20-Poly1305's mode as `0` (`providers/implementations/ciphers/cipher_chacha20_poly1305.c.in`),
so it falls through to the plain streaming loop even with `-aead`.

## Reproducing both tools inside one program (1 KB, medians of 5 × 1 s)

`method_probe` times each tool's method with the same data and library:

| Cipher | Our method | `speed -evp` method | `speed -aead` method | Streaming Update only |
|---|---|---|---|---|
| AES-256-GCM | 2,744 MB/s | 2,202 MB/s | 2,097 MB/s | 3,646 MB/s |
| ChaCha20-Poly1305 | 1,035 MB/s | 1,767 MB/s | 1,770 MB/s | 1,764 MB/s |

Compare with the real tools at 1 KB: `bench` 2,686 (AES) / 1,026 (ChaCha);
`openssl speed -evp` 2,090 / 1,866; `-aead` 2,030 / 1,787. Each method lands
within about 6% of the tool it imitates, so the methods explain the gap.

## What costs what

- **AES-GCM re-keying:** our method plus loading the key again each message
  (`harness_rekey`) drops from 2,744 to 2,069 MB/s, 373 → 495 ns per 1 KB
  message. **Re-keying costs about 120 ns**, which `openssl speed` pays and we don't.
  That's why `openssl speed` looks *slower* for AES-GCM at 1 KB.
- **ChaCha20-Poly1305 per-message work:** a complete message (new nonce,
  Poly1305 setup, finish, tag) costs about **0.39–0.44 µs more** than streaming
  the same bytes, roughly constant from 256 B to 8 KB:

  | Size | Complete message | Streaming only | Difference |
  |---|---|---|---|
  | 256 B | 649 ns | 257 ns | 392 ns |
  | 1 KB | 989 ns | 581 ns | 409 ns |
  | 2 KB | 1,493 ns | 1,061 ns | 432 ns |
  | 4 KB | 2,516 ns | 2,078 ns | 438 ns |
  | 8 KB | 4,523 ns | 4,088 ns | 435 ns |
  | 16 KB | 8,599 ns | 8,584 ns | 15 ns |

  `openssl speed` skips this work, so it looks *faster* for ChaCha20 at 1 KB.
  We have not identified which internal step costs the ~0.4 µs.
- **The 16 KB row:** the difference nearly disappears because streaming
  itself is slower at 16 KB (2,004 MB/s at 8 KB → 1,909 at 16 KB), not because
  the per-message work went away. We did not test the cause. One untested
  guess: 16 KB in + 16 KB out equals Zen 4's 32 KB L1 data cache.
- **With hardware AES masked**, AES-GCM takes ~6.4 µs per 1 KB message, so the
  ~0.3 µs re-key difference is under 5%. That's why `aesni_off` matched
  `openssl speed` at 1 KB (ratio 1.03).

## What this means for our project

- **No harness change.** Our experiment is about encrypting *complete
  messages*: each one gets its own nonce and tag, and the key is reused, as
  in a TLS connection. That's what `bench.exe` times.
- Use `openssl speed` as a cross-check **at 16 KB**, where both tools
  agree within 8%. At small sizes, explain the difference using the tables above.
- Small-message results are a real finding: per-message overhead matters
  far more for ChaCha20-Poly1305 than for hardware AES-GCM. At 1 KB, ChaCha20
  spends about 40% of its time on per-message work (409 of 989 ns). AES-GCM
  spends about 25% (373 vs 281 ns streaming).
