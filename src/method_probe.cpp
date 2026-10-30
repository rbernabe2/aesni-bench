// method_probe.cpp - DIAGNOSTIC tool, not part of the experiment.
//
// Question it answers: why do our 1 KB numbers differ from `openssl speed`?
// Reading OpenSSL 3.6.5's apps/speed.c shows the two tools time different
// amounts of work per message. This program times each way of doing it
// side by side, in one process, on the same data, so the cost of each step
// can be seen directly.
//
// Methods timed (per message):
//   harness       - what bench.exe does: new nonce, Update, Final, get tag
//   harness_rekey - same, but the key is loaded again every message
//                   (isolates the cost of re-running the key schedule)
//   speed_evp     - what `openssl speed -evp X` does:
//                     AES-GCM: set IV length, load key + IV, Update, Final
//                     ChaCha20-Poly1305: Update ONLY (speed.c only uses its
//                     per-message loop for GCM/CCM/OCB/SIV modes, and
//                     ChaCha20-Poly1305 reports mode 0)
//   speed_aead    - `openssl speed -evp X -aead`: like speed_evp, plus 13
//                   bytes of AAD for AES-GCM (ChaCha20: identical to speed_evp)
//   update_only   - EncryptUpdate only on one long-running context: raw
//                   streaming speed, no per-message work at all
//
// NOTE: like speed.c, the speed_* methods reuse one nonce for every message.
// That is insecure and only acceptable because nothing here is ever used.
//
// Usage: method_probe [--size 1024] [--seconds 1] [--reps 5] [--out file.csv]

#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/rand.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <memory>
#include <random>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;

struct CtxFree    { void operator()(EVP_CIPHER_CTX* p) const { EVP_CIPHER_CTX_free(p); } };
struct CipherFree { void operator()(EVP_CIPHER* p) const { EVP_CIPHER_free(p); } };
using CtxPtr    = std::unique_ptr<EVP_CIPHER_CTX, CtxFree>;
using CipherPtr = std::unique_ptr<EVP_CIPHER, CipherFree>;

[[noreturn]] static void die(const char* msg) {
    std::fprintf(stderr, "ERROR: %s\n", msg);
    std::exit(2);
}

// Run `op` repeatedly for `seconds`; return messages per second.
static double ops_per_second(const std::function<void()>& op, double seconds) {
    long long n = 0;
    const auto t0 = Clock::now();
    double elapsed = 0;
    do {
        for (int i = 0; i < 256; ++i) op();      // check the clock only every 256 ops
        n += 256;
        elapsed = std::chrono::duration<double>(Clock::now() - t0).count();
    } while (elapsed < seconds);
    return static_cast<double>(n) / elapsed;
}

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    int size = 1024, reps = 5;
    double seconds = 1.0;
    std::string out;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--size") size = std::atoi(argv[i + 1]);
        else if (a == "--seconds") seconds = std::atof(argv[i + 1]);
        else if (a == "--reps") reps = std::atoi(argv[i + 1]);
        else if (a == "--out") out = argv[i + 1];
        else die("unknown option");
    }
    const char* mask = std::getenv("OPENSSL_ia32cap");
    std::printf("OpenSSL %s | OPENSSL_ia32cap=%s | size=%d bytes | %d reps x %.1f s\n\n",
                OpenSSL_version(OPENSSL_VERSION), mask ? mask : "unset", size, reps, seconds);

    // Same fixed-seed data idea as bench.exe.
    std::vector<unsigned char> pt(size), ct(size + 32);
    std::mt19937_64 rng(0x5EEDC0FFEE2026ULL);
    for (auto& b : pt) b = static_cast<unsigned char>(rng());
    unsigned char key[32], nonce[12] = {0}, tag[16], aad[13];
    std::memset(aad, 0xcc, sizeof(aad));                   // speed.c uses 13 bytes of 0xcc
    RAND_bytes(key, sizeof(key));

    std::FILE* csv = nullptr;
    if (!out.empty()) {
        csv = std::fopen(out.c_str(), "a");
        if (!csv) die("cannot open --out file");
        std::fseek(csv, 0, SEEK_END);
        if (std::ftell(csv) == 0)
            std::fprintf(csv, "cipher,method,openssl_ia32cap,size_bytes,rep,throughput_MBps,ns_per_message\n");
    }

    for (const char* name : {"AES-256-GCM", "ChaCha20-Poly1305"}) {
        const bool gcm = std::strcmp(name, "AES-256-GCM") == 0;
        CipherPtr cipher(EVP_CIPHER_fetch(nullptr, name, nullptr));
        CtxPtr ctx(EVP_CIPHER_CTX_new());
        if (!cipher || !ctx) die("cipher setup failed");
        int outl = 0;

        auto next_nonce = [&] { for (int i = 11; i >= 0 && ++nonce[i] == 0; --i) {} };

        // Each method assumes the context was freshly set up with cipher + key + nonce.
        auto setup = [&] {
            if (EVP_EncryptInit_ex2(ctx.get(), cipher.get(), key, nonce, nullptr) != 1) die("init failed");
        };
        struct Method { const char* name; std::function<void()> op; };
        std::vector<Method> methods = {
            {"harness", [&] {
                next_nonce();
                EVP_EncryptInit_ex2(ctx.get(), nullptr, nullptr, nonce, nullptr);
                EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                EVP_EncryptFinal_ex(ctx.get(), ct.data() + outl, &outl);
                EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_GET_TAG, 16, tag);
            }},
            {"harness_rekey", [&] {
                next_nonce();
                EVP_EncryptInit_ex2(ctx.get(), nullptr, key, nonce, nullptr);
                EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                EVP_EncryptFinal_ex(ctx.get(), ct.data() + outl, &outl);
                EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_GET_TAG, 16, tag);
            }},
            {"speed_evp", [&] {
                if (gcm) {                                     // speed.c EVP_Update_loop_aead_enc
                    EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_SET_IVLEN, 12, nullptr);
                    EVP_CipherInit_ex(ctx.get(), nullptr, nullptr, key, nonce, -1);
                    EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                    EVP_EncryptFinal_ex(ctx.get(), ct.data() + outl, &outl);
                } else {                                       // speed.c EVP_Update_loop
                    EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                }
            }},
            {"speed_aead", [&] {
                if (gcm) {
                    EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_SET_IVLEN, 12, nullptr);
                    EVP_CipherInit_ex(ctx.get(), nullptr, nullptr, key, nonce, -1);
                    EVP_EncryptUpdate(ctx.get(), nullptr, &outl, aad, sizeof(aad));
                    EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                    EVP_EncryptFinal_ex(ctx.get(), ct.data() + outl, &outl);
                } else {
                    EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
                }
            }},
            {"update_only", [&] {
                EVP_EncryptUpdate(ctx.get(), ct.data(), &outl, pt.data(), size);
            }},
        };

        for (auto& m : methods) {
            setup();
            ops_per_second(m.op, 0.2);                         // warm-up, discarded
            std::vector<double> mbps;
            for (int r = 1; r <= reps; ++r) {
                // Fresh context state each rep (untimed). Streaming methods must
                // not exceed GCM's ~64 GB-per-nonce limit, where Update starts failing.
                setup();
                double ops = ops_per_second(m.op, seconds);
                double v = ops * size / 1e6;
                mbps.push_back(v);
                if (csv) std::fprintf(csv, "%s,%s,%s,%d,%d,%.2f,%.1f\n", name, m.name,
                                      mask ? mask : "unset", size, r, v, 1e9 / ops);
            }
            std::sort(mbps.begin(), mbps.end());
            double med = mbps[mbps.size() / 2];
            std::printf("%-18s %-14s median %9.1f MB/s  (%7.1f ns/message)  min %9.1f  max %9.1f\n",
                        name, m.name, med, size / med * 1e3, mbps.front(), mbps.back());
        }
        std::printf("\n");
    }
    if (csv) std::fclose(csv);
    return 0;
}
