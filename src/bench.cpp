// bench.cpp - benchmark harness for authenticated encryption with OpenSSL 3.
//
// Measures how fast one cipher encrypts one data size, repeated for several
// trials, and appends one CSV row per trial.
//
// Example:
//   bench --cipher aes-256-gcm --size 16KB --trials 10 \
//         --out results/run.csv --label aesni_on
//
// Options:
//   --cipher  aes-256-gcm | chacha20-poly1305
//   --size    1KB 16KB 256KB 1MB 64MB 1GB  (or a plain byte count; 1 KB = 1024 bytes)
//   --trials  number of measured trials (default 10)
//   --out     CSV file to append to (header is written if the file is new)
//   --label   name of the configuration, e.g. aesni_on, aesni_off
//   --min-seconds  target length of one trial (default 0.5)
//   --pin N   pin this program to logical processor N and raise its priority
//   --info    print OpenSSL / CPU information and exit
//   --allow-debug  allow running a Debug build (results would be meaningless)
//
// AES-NI on/off is NOT a flag of this program. It is controlled from outside
// with the OPENSSL_ia32cap environment variable, which OpenSSL reads at
// start-up (see scripts/run_all.ps1). This program only reports whether it is set.
//
// Units: throughput "MB/s" uses 1 MB = 1,000,000 bytes (the same as
// `openssl speed`), so the two tools can be compared directly. Memory
// "peak_mem_MB" uses 1 MB = 1,048,576 bytes (the same as Task Manager).

#include <windows.h>
#include <psapi.h>
#include <intrin.h>

#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>

#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <memory>
#include <random>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
using Bytes = std::vector<unsigned char>;

// Fixed seed for the plaintext generator. Every configuration therefore
// encrypts exactly the same bytes. (std::mt19937_64 is fully specified by the
// C++ standard, so the same seed gives the same bytes on every compiler.)
constexpr std::uint64_t kDataSeed = 0x5EEDC0FFEE2026ULL;

constexpr int kKeyBytes   = 32;   // AES-256 and ChaCha20 both use 256-bit keys
constexpr int kNonceBytes = 12;   // 96-bit nonce for both ciphers
constexpr int kTagBytes   = 16;   // 128-bit authentication tag for both

// ---------------------------------------------------------------------------
// Command-line options
// ---------------------------------------------------------------------------

struct Options {
    std::string cipher = "aes-256-gcm";
    std::string size_text = "16KB";
    std::size_t size = 16 * 1024;
    int trials = 10;
    std::string out = "results/bench.csv";
    std::string label;
    double min_seconds = 0.5;
    double warmup_seconds = 0.25;
    int pin = -1;            // -1 = don't pin
    bool info_only = false;
    bool allow_debug = false;
};

[[noreturn]] static void die(const std::string& msg) {
    std::fprintf(stderr, "ERROR: %s\n", msg.c_str());
    std::exit(2);
}

// "16KB" -> 16384, "1MB" -> 1048576, "1GB" -> 1073741824, "5000" -> 5000.
static std::size_t parse_size(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    std::size_t mult = 1;
    auto ends_with = [&](const char* suf) {
        size_t n = std::strlen(suf);
        return s.size() > n && s.compare(s.size() - n, n, suf) == 0;
    };
    if (ends_with("KB"))      { mult = 1ULL << 10; s.resize(s.size() - 2); }
    else if (ends_with("MB")) { mult = 1ULL << 20; s.resize(s.size() - 2); }
    else if (ends_with("GB")) { mult = 1ULL << 30; s.resize(s.size() - 2); }
    char* end = nullptr;
    unsigned long long v = std::strtoull(s.c_str(), &end, 10);
    if (end == s.c_str() || *end != '\0' || v == 0) die("bad --size value");
    return static_cast<std::size_t>(v * mult);
}

static Options parse_args(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) die("missing value after " + a);
            return argv[++i];
        };
        if (a == "--cipher")            o.cipher = next();
        else if (a == "--size")         { o.size_text = next(); o.size = parse_size(o.size_text); }
        else if (a == "--trials")       o.trials = std::stoi(next());
        else if (a == "--out")          o.out = next();
        else if (a == "--label")        o.label = next();
        else if (a == "--min-seconds")  o.min_seconds = std::stod(next());
        else if (a == "--pin")          o.pin = std::stoi(next());
        else if (a == "--info")         o.info_only = true;
        else if (a == "--allow-debug")  o.allow_debug = true;
        else die("unknown option " + a);
    }
    if (o.cipher != "aes-256-gcm" && o.cipher != "chacha20-poly1305")
        die("--cipher must be aes-256-gcm or chacha20-poly1305");
    if (o.trials < 1) die("--trials must be >= 1");
    // EVP_EncryptUpdate takes an int length, so one message must fit in an int.
    if (o.size > static_cast<std::size_t>(INT_MAX)) die("--size too large (max 2 GB - 1)");
    if (!o.info_only && o.label.empty()) die("--label is required (e.g. aesni_on)");
    if (o.label.find(',') != std::string::npos) die("--label must not contain commas");
    return o;
}

// ---------------------------------------------------------------------------
// Environment reporting
// ---------------------------------------------------------------------------

static std::string ia32cap_value() {
    const char* v = std::getenv("OPENSSL_ia32cap");
    return v ? std::string(v) : std::string("unset");
}

// Ask the CPU itself (CPUID instruction) which crypto features it has. This is
// what the hardware supports; OPENSSL_ia32cap can tell OpenSSL to ignore them.
static void print_cpu_features() {
    int r[4];                                   // EAX, EBX, ECX, EDX
    __cpuid(r, 1);
    bool aesni = (r[2] >> 25) & 1, pclmul = (r[2] >> 1) & 1;
    __cpuidex(r, 7, 0);
    bool avx512f = (r[1] >> 16) & 1, vaes = (r[2] >> 9) & 1, vpclmul = (r[2] >> 10) & 1;
    std::printf("CPU features    : AES-NI=%s PCLMULQDQ=%s VAES=%s VPCLMULQDQ=%s AVX-512F=%s\n",
                aesni ? "yes" : "no", pclmul ? "yes" : "no", vaes ? "yes" : "no",
                vpclmul ? "yes" : "no", avx512f ? "yes" : "no");
}

static void print_environment() {
    std::printf("OpenSSL version : %s\n", OpenSSL_version(OPENSSL_VERSION));
    std::printf("Build type      : %s\n", BENCH_BUILD_TYPE);
    print_cpu_features();
    std::string cap = ia32cap_value();
    std::printf("OPENSSL_ia32cap : %s\n", cap == "unset"
        ? "unset (OpenSSL may use AES-NI)"
        : (cap + "  (OpenSSL told to ignore the masked CPU features)").c_str());
}

// ---------------------------------------------------------------------------
// Measurement helpers (Windows)
// ---------------------------------------------------------------------------

// Total CPU time this process has used so far = user time + kernel time.
// Windows reports it in 100-nanosecond units. Note: Windows only updates
// these counters on each scheduler tick (usually every ~15.6 ms), so a single
// reading can be off by up to one tick. That is why each trial runs for at
// least --min-seconds (0.5 s): one tick is then ~3% at worst, and less for
// longer trials.
static double process_cpu_seconds() {
    FILETIME created, exited, kernel, user;
    GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user);
    auto to_u64 = [](FILETIME f) {
        return (static_cast<std::uint64_t>(f.dwHighDateTime) << 32) | f.dwLowDateTime;
    };
    return static_cast<double>(to_u64(kernel) + to_u64(user)) * 1e-7;
}

// Largest amount of physical RAM this process has held at any moment since
// it started ("peak working set"), in MB (1 MB = 1,048,576 bytes).
static double peak_working_set_mb() {
    PROCESS_MEMORY_COUNTERS pmc{};
    GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc));
    return static_cast<double>(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0);
}

// Optional noise reduction (--pin N):
//  * Affinity: the OS may only run this thread on logical processor N, so it
//    is never moved between cores mid-measurement (a move empties the caches).
//  * Priority: HIGH priority class + highest thread priority, so ordinary
//    background programs are less likely to interrupt us.
// Caveat: processor N shares its physical core with its SMT "twin" (N^1 on
// this CPU), and other programs can still run there.
static void pin_and_raise_priority(int cpu) {
    if (cpu < 0 || cpu >= 64) die("--pin must be between 0 and 63");
    DWORD_PTR mask = DWORD_PTR(1) << cpu;
    if (!SetThreadAffinityMask(GetCurrentThread(), mask)) die("SetThreadAffinityMask failed");
    if (!SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS)) die("SetPriorityClass failed");
    if (!SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST)) die("SetThreadPriority failed");
    std::printf("Pinned to logical processor %d, HIGH priority\n", cpu);
}

// ---------------------------------------------------------------------------
// Encryption
// ---------------------------------------------------------------------------

struct CtxFree    { void operator()(EVP_CIPHER_CTX* p) const { EVP_CIPHER_CTX_free(p); } };
struct CipherFree { void operator()(EVP_CIPHER* p) const { EVP_CIPHER_free(p); } };
using CtxPtr    = std::unique_ptr<EVP_CIPHER_CTX, CtxFree>;
using CipherPtr = std::unique_ptr<EVP_CIPHER, CipherFree>;

// A 96-bit counter used as the nonce. Every encryption gets the next value,
// so no nonce is ever used twice with the same key. (Reusing a nonce with
// GCM or ChaCha20-Poly1305 is a serious security bug, so we model the correct
// practice even in a benchmark.)
struct NonceCounter {
    unsigned char bytes[kNonceBytes] = {0};
    void next() {                               // big-endian increment with carry
        for (int i = kNonceBytes - 1; i >= 0; --i)
            if (++bytes[i] != 0) break;
    }
};

// ONE complete authenticated encryption = the operation we time.
//   1. load a fresh nonce into the context (the key is already set up and is
//      reused - re-running the key schedule every time would be wasteful)
//   2. EncryptUpdate: encrypt all the plaintext into the output buffer
//   3. EncryptFinal: finish the encryption (and the authentication math)
//   4. GET_TAG: copy out the 16-byte authentication tag
static inline bool encrypt_once(EVP_CIPHER_CTX* ctx, const unsigned char* nonce,
                                const unsigned char* in, unsigned char* out, int len,
                                unsigned char* tag) {
    int outl = 0, finl = 0;
    if (EVP_EncryptInit_ex2(ctx, nullptr, nullptr, nonce, nullptr) != 1) return false;
    if (EVP_EncryptUpdate(ctx, out, &outl, in, len) != 1) return false;
    if (EVP_EncryptFinal_ex(ctx, out + outl, &finl) != 1) return false;
    return EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_GET_TAG, kTagBytes, tag) == 1;
}

// After a trial: decrypt what we produced and make sure we get the original
// plaintext back AND the tag verifies. Decrypts in 1 MB pieces into a small
// buffer so the 1 GB test does not need a third 1 GB buffer.
static bool verify(EVP_CIPHER_CTX* dctx, const EVP_CIPHER* cipher, const unsigned char* key,
                   const unsigned char* nonce, const Bytes& plaintext,
                   const Bytes& ciphertext, std::size_t len, const unsigned char* tag) {
    if (EVP_DecryptInit_ex2(dctx, cipher, key, nonce, nullptr) != 1) return false;
    const std::size_t chunk = 1 << 20;
    Bytes buf(chunk + 16);
    int outl = 0;
    for (std::size_t off = 0; off < len; off += chunk) {
        int n = static_cast<int>(std::min(chunk, len - off));
        if (EVP_DecryptUpdate(dctx, buf.data(), &outl, ciphertext.data() + off, n) != 1) return false;
        if (outl != n || std::memcmp(buf.data(), plaintext.data() + off, n) != 0) return false;
    }
    unsigned char tag_copy[kTagBytes];
    std::memcpy(tag_copy, tag, kTagBytes);
    if (EVP_CIPHER_CTX_ctrl(dctx, EVP_CTRL_AEAD_SET_TAG, kTagBytes, tag_copy) != 1) return false;
    int ok = EVP_DecryptFinal_ex(dctx, buf.data(), &outl);   // checks the tag
    if (ok <= 0) { ERR_clear_error(); return false; }
    return true;
}

// ---------------------------------------------------------------------------
// CSV output
// ---------------------------------------------------------------------------

static const char* kCsvHeader =
    "timestamp,config_label,cipher,openssl_ia32cap,size_bytes,iterations,trial,"
    "wall_seconds,cpu_seconds,throughput_MBps,latency_us_per_op,"
    "cpu_util_pct_one_core,peak_mem_MB,verified";

static std::string utc_timestamp() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
    gmtime_s(&tm, &t);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

// Append one row. The file is opened and closed per row, AFTER timing, so
// file I/O never happens inside the measured region.
static void append_csv(const std::string& path, const std::string& row) {
    namespace fs = std::filesystem;
    fs::path p(path);
    if (p.has_parent_path()) fs::create_directories(p.parent_path());
    bool needs_header = !fs::exists(p) || fs::file_size(p) == 0;
    std::ofstream f(p, std::ios::app);
    if (!f) die("cannot open CSV file " + path);
    if (needs_header) f << kCsvHeader << "\n";
    f << row << "\n";
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

static int run(int argc, char** argv) {
    Options opt = parse_args(argc, argv);

    print_environment();
    if (opt.info_only) return 0;

    // Debug builds are unoptimized; timing them would measure the compiler
    // settings, not the cipher.
    if (std::string(BENCH_BUILD_TYPE) != "Release" && !opt.allow_debug)
        die(std::string("this is a ") + BENCH_BUILD_TYPE +
            " build. Benchmark only the Release x64 build (CMake preset x64-release).");

    if (opt.pin >= 0) pin_and_raise_priority(opt.pin);

    const int len = static_cast<int>(opt.size);
    std::printf("Config          : label=%s cipher=%s size=%s (%d bytes) trials=%d\n",
                opt.label.c_str(), opt.cipher.c_str(), opt.size_text.c_str(), len, opt.trials);

    // ---- 1. Set-up (NOT timed) ------------------------------------------
    // All memory is allocated and filled here, before any timing starts.
    // std::vector zero-fills the ciphertext buffer, which also makes Windows
    // actually hand us the physical memory now rather than mid-measurement.
    Bytes plaintext(opt.size);
    Bytes ciphertext(opt.size + 16);            // +16 spare bytes for EncryptFinal
    {
        std::mt19937_64 rng(kDataSeed);
        std::size_t i = 0;
        for (; i + 8 <= plaintext.size(); i += 8) {
            std::uint64_t r = rng();
            std::memcpy(&plaintext[i], &r, 8);
        }
        std::uint64_t r = rng();
        std::memcpy(&plaintext[i], &r, plaintext.size() - i);
    }
    std::printf("Plaintext start : %02x%02x%02x%02x%02x%02x%02x%02x... (same in every config)\n",
                plaintext[0], plaintext[1], plaintext[2], plaintext[3],
                plaintext[4], plaintext[5], plaintext[6], plaintext[7]);

    // Random key from OpenSSL's random generator, new for every run of this
    // program. Cipher speed does not depend on the key's value, and a fresh
    // key means (key, nonce) pairs never repeat even across separate runs.
    unsigned char key[kKeyBytes];
    if (RAND_bytes(key, kKeyBytes) != 1) die("RAND_bytes failed");

    CipherPtr cipher(EVP_CIPHER_fetch(nullptr,
        opt.cipher == "aes-256-gcm" ? "AES-256-GCM" : "ChaCha20-Poly1305", nullptr));
    CtxPtr ctx(EVP_CIPHER_CTX_new());    // ONE context, reused for every encryption
    CtxPtr dctx(EVP_CIPHER_CTX_new());   // separate context for verification
    if (!cipher || !ctx || !dctx) die("OpenSSL could not create the cipher");
    // Load the cipher and key once (this runs the key schedule once).
    if (EVP_EncryptInit_ex2(ctx.get(), cipher.get(), key, nullptr, nullptr) != 1)
        die("EVP_EncryptInit_ex2 failed");

    NonceCounter nonce;
    unsigned char tag[kTagBytes];

    // ---- 2. Warm-up + calibration (NOT recorded) -------------------------
    // The first runs are slower: caches are cold, the CPU may still be
    // ramping up its clock speed, and OpenSSL does one-time set-up. So we run
    // for a while first and throw those timings away. We also use the
    // warm-up to measure roughly how long one encryption takes, and from that
    // pick how many encryptions ("iterations") make one trial last at least
    // --min-seconds. A 1 KB encryption takes about a microsecond, far below
    // what a clock can measure reliably on its own, so small sizes need
    // hundreds of thousands of iterations per trial; 1 GB needs just a few.
    long long warm_ops = 0;
    auto w0 = Clock::now();
    double warm_elapsed = 0.0;
    do {
        nonce.next();
        if (!encrypt_once(ctx.get(), nonce.bytes, plaintext.data(), ciphertext.data(), len, tag))
            die("encryption failed during warm-up");
        ++warm_ops;
        warm_elapsed = std::chrono::duration<double>(Clock::now() - w0).count();
    } while (warm_elapsed < opt.warmup_seconds);
    double seconds_per_op = warm_elapsed / static_cast<double>(warm_ops);
    long long iterations = std::max(1LL,
        static_cast<long long>(std::ceil(opt.min_seconds / seconds_per_op)));
    std::printf("Warm-up         : %lld ops in %.3f s -> %lld iterations per trial\n",
                warm_ops, warm_elapsed, iterations);

    // ---- 3. Measured trials ---------------------------------------------
    int failures = 0;
    for (int trial = 1; trial <= opt.trials; ++trial) {
        // >>> TIMED REGION START. Only encryption happens in here: no memory
        // allocation, no printing, no file access.
        const double cpu0 = process_cpu_seconds();
        const auto t0 = Clock::now();
        bool ok = true;
        for (long long i = 0; i < iterations; ++i) {
            nonce.next();
            ok &= encrypt_once(ctx.get(), nonce.bytes, plaintext.data(), ciphertext.data(), len, tag);
        }
        const auto t1 = Clock::now();
        const double cpu1 = process_cpu_seconds();
        // <<< TIMED REGION END.
        if (!ok) die("an encryption call failed during the timed loop");

        const double wall = std::chrono::duration<double>(t1 - t0).count();
        const double cpu  = cpu1 - cpu0;
        const double bytes = static_cast<double>(opt.size) * static_cast<double>(iterations);
        const double mbps = bytes / wall / 1e6;                          // 1 MB = 10^6 bytes
        const double latency_us = wall / static_cast<double>(iterations) * 1e6;
        // CPU utilization as % of ONE core = CPU time / wall time.
        // This program is single-threaded, so while the loop runs it keeps
        // one logical processor 100% busy: expect ~100% here. Values slightly
        // below 100% mean we were briefly descheduled (another program ran);
        // slightly above can come from the ~15.6 ms resolution of the CPU
        // timer. Task Manager instead shows % of the WHOLE CPU (all 16
        // logical processors on our Ryzen 9 8945HS), so the same load
        // appears there as about 100/16 = ~6%.
        const double cpu_util = cpu / wall * 100.0;
        const double peak_mb = peak_working_set_mb();

        // Check the LAST encryption of this trial: it must decrypt back to the
        // plaintext and its tag must verify. (Every iteration encrypts the same
        // plaintext with a different nonce, so checking one proves the
        // pipeline is producing real, correct output.)
        const bool verified = verify(dctx.get(), cipher.get(), key, nonce.bytes,
                                     plaintext, ciphertext, opt.size, tag);
        if (!verified) {
            ++failures;
            std::fprintf(stderr,
                "\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "!!! VERIFICATION FAILED: trial %d (%s, %s, %d bytes)\n"
                "!!! Decryption did not return the plaintext or the tag was rejected.\n"
                "!!! This trial's numbers are NOT valid.\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n",
                trial, opt.label.c_str(), opt.cipher.c_str(), len);
        }

        char row[512];
        std::snprintf(row, sizeof(row), "%s,%s,%s,%s,%d,%lld,%d,%.6f,%.6f,%.2f,%.3f,%.1f,%.1f,%s",
                      utc_timestamp().c_str(), opt.label.c_str(), opt.cipher.c_str(),
                      ia32cap_value().c_str(), len, iterations, trial, wall, cpu, mbps,
                      latency_us, cpu_util, peak_mb, verified ? "PASS" : "FAIL");
        append_csv(opt.out, row);

        std::printf("  trial %2d/%d: %10.2f MB/s  %12.3f us/op  cpu %5.1f%%  peak %8.1f MB  verify %s\n",
                    trial, opt.trials, mbps, latency_us, cpu_util, peak_mb,
                    verified ? "PASS" : "FAIL");
    }

    if (failures > 0) {
        std::fprintf(stderr, "*** %d of %d trials FAILED verification ***\n", failures, opt.trials);
        return 3;
    }
    return 0;
}

int main(int argc, char** argv) {
    // Print immediately instead of buffering, so progress shows up live when
    // the output is piped (run_all.ps1 does that) and is not lost on a crash.
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    try {
        return run(argc, argv);
    } catch (const std::exception& e) {        // e.g. a bad CSV path, bad number in an option
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        return 2;
    }
}
