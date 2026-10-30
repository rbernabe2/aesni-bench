# aesni-bench

Class project (Ryan Bernabe, Van Vong): how much does the CPU's AES-NI
hardware speed up encryption, and how does AES compare with a cipher designed
for software?

| Config label | Cipher | `OPENSSL_ia32cap` | Role |
|---|---|---|---|
| `aesni_on` | AES-256-GCM | unset | Baseline (hardware AES allowed) |
| `aesni_off` | AES-256-GCM | `~0x200000200000000:~0x60000000000:~0x0` | Comparison 1: all hardware AES hidden, so OpenSSL uses software AES and software GHASH |
| `chacha_on` | ChaCha20-Poly1305 | unset | Comparison 2: software-oriented cipher |
| `chacha_masked_control` | ChaCha20-Poly1305 | `~0x200000200000000:~0x60000000000:~0x0` | Control: should match `chacha_on`, since ChaCha20 doesn't use AES instructions |
| `aesni_off_fullmask` | AES-256-GCM | `~0x200000200000000` | Diagnostic: the proposal's original mask |
| `chacha_fullmask` | ChaCha20-Poly1305 | `~0x200000200000000` | Diagnostic: the proposal's original mask |

`OPENSSL_ia32cap` is read by OpenSSL when a program starts. It holds several
64-bit "capability words" separated by `:`, and `~X` means "clear the bits in X".

- Word 1: bit 57 = AES-NI, bit 33 = PCLMULQDQ (the carry-less multiply GCM uses for its tag).
- Word 2: bit 41 = VAES, bit 42 = VPCLMULQDQ (the wide AVX-512 versions of those two).
  This word also holds AVX2, AVX-512, SHA and more.

**Why not the proposal's `~0x200000200000000`?** We checked with `openssl version -a`
(its CPUINFO line shows the bits OpenSSL really uses) and `openssl speed`:

1. If a word is left out of the variable, OpenSSL 3.6.5 sets it to **zero**. The
   original mask therefore also disabled AVX2/AVX-512, which made ChaCha20 (the
   control) about 2.3× slower.
2. On this Zen 4 CPU, OpenSSL's fastest AES-GCM code uses VAES/VPCLMULQDQ and
   checks only word 2. Hiding just AES-NI + PCLMULQDQ (`~0x200000200000000:~0x0:~0x0`)
   left AES-GCM exactly as fast.

`~0x200000200000000:~0x60000000000:~0x0` clears exactly those four bits and
nothing else. The original mask is kept as the two `*_fullmask` diagnostic configs.

## Requirements

- Visual Studio 2022 with "Desktop development with C++" (provides cl, CMake, Ninja).
- Git, and Python 3 with pandas + matplotlib (run with `py`).
- **A standalone vcpkg.** The copy bundled with VS 2022 17.14 can no longer
  build OpenSSL, because it downloads an MSYS2 package that has been removed. One-time setup:

  ```powershell
  git clone https://github.com/microsoft/vcpkg C:\vcpkg
  C:\vcpkg\bootstrap-vcpkg.bat -disableMetrics
  [Environment]::SetEnvironmentVariable('VCPKG_ROOT', 'C:\vcpkg', 'User')
  ```

  The scripts restore this `VCPKG_ROOT` after loading the VS developer shell,
  which would otherwise replace it with the bundled copy.

## Build (Release x64)

```powershell
.\scripts\build.ps1
```

This loads the Visual Studio developer shell, has vcpkg build OpenSSL (first
time only, several minutes), builds `bench.exe` and `kat.exe`, and runs the
correctness tests twice (with and without the AES-NI mask).

Build output goes to `%LOCALAPPDATA%\aesni-bench\build\x64-release`, **not**
inside the repo, because the repo lives in OneDrive and syncing hundreds of MB
of build files would slow everything down and can lock files mid-build.

Visual Studio: *File > Open > Folder*, choose this folder, and pick the
"x64 Release" preset in the toolbar. Never benchmark the Debug preset;
`bench.exe` refuses to run if it was built as Debug. (Not yet tested in the
IDE. If CMake warns that it is using Visual Studio's bundled vcpkg, the IDE
has overridden `VCPKG_ROOT`. Build with `scripts\build.ps1` instead.)

## Before every benchmark run

1. Plug in the charger (`run_all.ps1` refuses to run on battery).
2. Use the same Armoury Crate mode every time, and pass it with `-ArmouryMode`.
3. Close background apps, including pausing OneDrive sync.
4. Restart if the laptop has been on for days.

## Run

```powershell
.\scripts\openssl_speed.ps1                                 # cross-check with openssl speed
.\scripts\run_all.ps1 -Trials 3 -ArmouryMode Performance    # preliminary (4 experiment configs)
.\scripts\run_all.ps1 -Trials 3 -ArmouryMode Performance -Configs aesni_on,aesni_off,chacha_on,chacha_masked_control,aesni_off_fullmask,chacha_fullmask   # + diagnostics
.\scripts\run_all.ps1 -Trials 10 -ArmouryMode Performance   # full
py analysis\plot.py                                         # graphs + tables for newest run
.\scripts\collect_env.ps1 -ArmouryMode Performance          # refresh docs/ENVIRONMENT.md
```

One configuration by hand:

```powershell
$env:OPENSSL_ia32cap = "~0x200000200000000:~0x60000000000:~0x0"     # omit for AES-NI on
& "$env:LOCALAPPDATA\aesni-bench\build\x64-release\bench.exe" --cipher aes-256-gcm --size 16KB --trials 3 --out results\manual.csv --label aesni_off
Remove-Item Env:\OPENSSL_ia32cap
```

## Units

- Data sizes are binary: 1 KB = 1,024 bytes, 1 MB = 1,048,576 bytes, 1 GB = 2^30 bytes.
  (So our 1 KB and 16 KB tests use exactly the same block sizes as `openssl speed`'s 1024 and 16384.)
- Throughput MB/s uses 1 MB = 1,000,000 bytes, the same as `openssl speed`.
- Peak memory MB uses 1 MB = 1,048,576 bytes, the same as Task Manager.

## Layout

| Path | What it is |
|---|---|
| `src/bench.cpp` | Benchmark harness (one cipher, one size, N trials, CSV rows) |
| `src/kat.cpp` | Correctness tests: all NIST GCM-256 vectors, RFC 8439 vector, tamper tests |
| `src/method_probe.cpp` | Diagnostic: our per-message method vs `openssl speed`'s, side by side (see `docs/SPEED_COMPARISON.md`) |
| `testvectors/` | Official test vector files plus where they came from |
| `scripts/build.ps1` | Configure + build + test |
| `scripts/run_all.ps1` | All configs × sizes × trials into a timestamped CSV |
| `scripts/openssl_speed.ps1` | `openssl speed` cross-check, mask on/off |
| `scripts/collect_env.ps1` | Writes the hardware/software environment record |
| `scripts/common.ps1` | Shared paths and helpers |
| `scripts/extract_rfc8439_vector.ps1` | Regenerates the RFC 8439 vector file from the RFC text |
| `analysis/plot.py` | Graphs and summary tables |
| `results/` | Raw CSVs, logs and per-run environment snapshots (never overwritten) |
| `docs/ENVIRONMENT.md` | Test environment |
| `docs/PROGRESS.md` | Weekly progress log |
| `docs/SPEED_COMPARISON.md` | Why small-size results differ from `openssl speed` |
