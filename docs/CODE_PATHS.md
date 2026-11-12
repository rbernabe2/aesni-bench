# Which OpenSSL code runs in each configuration

Checked against the OpenSSL **3.6.5** source that vcpkg compiled for us
(`C:\vcpkg\buildtrees\openssl\src\nssl-3.6.5-c2a478abe6.clean\`). Line numbers
refer to that tree. The compiler flags come from the build's own `Makefile`
(`C:\vcpkg\buildtrees\openssl\x64-windows-rel\Makefile`), which defines
`AES_ASM BSAES_ASM VPAES_ASM GHASH_ASM POLY1305_ASM` (among others). So every
fallback below is hand-written **assembly**, not plain C.

## How OpenSSL decides

OpenSSL reads CPUID once at start-up into `OPENSSL_ia32cap_P[]`, after applying
`OPENSSL_ia32cap`. Each algorithm then checks bits in that array:

| Bit (as we write it in the mask) | Feature | Where it is checked |
|---|---|---|
| word 1, bit 57 | AES-NI | `AESNI_CAPABLE`, `include/crypto/aes_platform.h:185` |
| word 1, bit 33 | PCLMULQDQ | `gcm_get_funcs`, `crypto/modes/gcm128.c:446` |
| word 1, bit 41 | **SSSE3** | `VPAES_CAPABLE` and `BSAES_CAPABLE`, `aes_platform.h:187,190` |
| word 2, bits 41/42 + AVX-512 bits | VAES / VPCLMULQDQ / AVX-512F,DQ,BW,VL | `ossl_vaes_vpclmulqdq_capable`, `crypto/modes/asm/aes-gcm-avx512.pl:94-102` |

## AES-256-GCM

The provider chooses an implementation in
`providers/implementations/ciphers/cipher_aes_gcm_hw_aesni.inc:36-46`:

1. `vaes_gcm` (AVX-512 VAES + VPCLMULQDQ) if `ossl_vaes_vpclmulqdq_capable()`,
2. otherwise `aesni_gcm` if `AESNI_CAPABLE`,
3. otherwise the generic `aes_gcm`.

`ossl_vaes_vpclmulqdq_capable` reads only the second capability word
(`OPENSSL_ia32cap_P+8`). It **does not check the AES-NI bit**, which is why
hiding only AES-NI + PCLMULQDQ left AES-GCM at full speed (see
`results/README.md`). Our `aesni_off` mask therefore also hides VAES and
VPCLMULQDQ.

### `aesni_on`: what runs

On the Ryzen 9 8945HS all the AVX-512/VAES bits are set (`bench --info`
shows `VAES=yes VPCLMULQDQ=yes AVX-512F=yes`), so step 1 is taken:
**`vaes_gcm`**, the AVX-512 assembly in `crypto/modes/asm/aes-gcm-avx512.pl`.
It encrypts several AES blocks per instruction and computes GHASH with
VPCLMULQDQ. Strictly speaking, our baseline is "**AES-NI + VAES**", not
classic AES-NI only.

### `aesni_off`: what runs (`~0x200000200000000:~0x60000000000:~0x0`)

With all four bits hidden, step 3 is taken, the generic `aes_gcm`.
Its key setup (`providers/implementations/ciphers/cipher_aes_gcm_hw.c:20-58`)
tries, in order: `HWAES` (not defined on x86-64), **`BSAES_CAPABLE`**,
`VPAES_CAPABLE`, then plain table AES. SSSE3 is still enabled, so the
**BSAES** branch wins:

- **Counter-mode encryption** (`ossl_bsaes_ctr32_encrypt_blocks`,
  `crypto/aes/asm/bsaes-x86_64.pl:1923`) is **bit-sliced AES** by Käsper and
  Schwabe. It uses SSSE3 vector registers to run 8 AES blocks at once
  with logic operations instead of table lookups. For fewer than 8 blocks it
  jumps to `.Lctr_enc_short` (`:2106`), which calls `asm_AES_encrypt` one
  block at a time.
- **Single blocks and the key schedule** use `AES_encrypt` /
  `AES_set_encrypt_key` from `crypto/aes/asm/aes-x86_64.pl`, the
  table-driven AES in assembly.
- **GHASH**: without PCLMULQDQ, `gcm_get_funcs` (`gcm128.c:428-486`)
  falls through to the x86-64 defaults **`gcm_gmult_4bit` /
  `gcm_ghash_4bit`**. That is the "4-bit" table-driven GF(2^128)
  multiplication in `crypto/modes/asm/ghash-x86_64.pl` (`:19-20`, `:249`, `:297`).

So "AES-NI off" means: **bit-sliced SSSE3 AES + table-driven 4-bit GHASH, in
assembly**. That is OpenSSL's best software path, not a naïve C implementation,
and not VPAES (VPAES would only be used if BSAES were not compiled in).

### Original proposal mask (`~0x200000200000000`, the `*_fullmask` diagnostic)

It zeroes capability words 2-3, but SSSE3 is in word 1 and stays on. AES-GCM
takes the **same** BSAES + 4-bit GHASH path as `aesni_off`, which matches our
measurements (both ≈ 171 MB/s at 1 GB in `run_20261029_172841_t3`).

## ChaCha20-Poly1305

- **ChaCha20** (`crypto/chacha/asm/chacha-x86_64.pl:268-280`) checks AVX-512F
  first, then AVX-512VL, then SSSE3. On our CPU it uses **`ChaCha20_avx512`**.
- **Poly1305** (`crypto/poly1305/asm/poly1305-x86_64.pl:189-203`, plus the
  AVX-512 check at `:1696`) picks **AVX, then AVX2, then AVX-512** code.

All of those bits are in capability words our `aesni_off` mask leaves alone,
so **ChaCha20-Poly1305 keeps its full SIMD speed under the mask**. This is the
point of the `chacha_masked_control` configuration. It is not a flaw in the
comparison: ChaCha20 was designed to be fast in software using plain
add/rotate/xor (ARX) operations, and SIMD vector units are general-purpose
hardware every modern x86 CPU has. What the mask removes is the
**AES-specific** hardware (AES-NI, VAES) and the carry-less multipliers
(PCLMULQDQ, VPCLMULQDQ) that GCM relies on.

The original proposal mask *does* zero word 2 (AVX2/AVX-512). ChaCha20 then
drops to its SSSE3 code, which is why `chacha_fullmask` is about 2.4× slower.

## How we verified this (not just read it)

- `openssl version -a` prints the `CPUINFO` capability words OpenSSL actually
  uses. Each `results/openssl_speed_*.txt` file records them for its mask.
- `openssl speed` with three masks (`results/openssl_speed_20261029_172506`):
  all on / only VAES+VPCLMULQDQ hidden / all hardware AES hidden. This gives
  three distinct speeds, one per code path above.
- `kat.exe` passes all 7,875 NIST vectors under each mask (`ctest`), so every
  path we time produces correct output.
