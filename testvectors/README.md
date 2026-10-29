# Test vectors (official sources, not typed by hand)

| File | Source | Notes |
|---|---|---|
| `gcmEncryptExtIV256.rsp` | NIST CAVP, `gcmtestvectors.zip` from https://csrc.nist.gov/CSRC/media/Projects/Cryptographic-Algorithm-Validation-Program/documents/mac/gcmtestvectors.zip | Copied unmodified out of the zip. 7,875 AES-256-GCM encryption vectors. |
| `rfc8439.txt` | https://www.rfc-editor.org/rfc/rfc8439.txt | Full RFC text, unmodified. |
| `rfc8439_sec2.8.2.txt` | Generated from `rfc8439.txt` by `scripts/extract_rfc8439_vector.ps1` | The ChaCha20-Poly1305 AEAD vector from RFC 8439 §2.8.2. Nonce = 32-bit fixed part `07000000` followed by the 64-bit IV, as §2.8.2 describes. |

Downloaded 2026-10-29. SHA-256 of the downloads:

- `gcmtestvectors.zip`: `F9FC479E134CDE2980B3BB7CDDBCB567B2CD96FD753835243ED067699F26A023`
- `rfc8439.txt`: `25BEF70FBF7A07FF45C2FE4CB7C6CE954EAC687413D8610603268B4E4415324C`

`kat.exe` reads these files at run time, so every one of the NIST vectors is
checked, not just a hand-picked few.
