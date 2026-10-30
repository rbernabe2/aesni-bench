// kat.cpp - Known-Answer Tests (KATs) for the two ciphers we benchmark.
//
// A known-answer test feeds a cipher inputs published by a standards body and
// checks that we get exactly the published outputs. If these pass, we know we
// are calling OpenSSL correctly BEFORE we start timing it.
//
// What is checked:
//   1. AES-256-GCM: every vector in NIST CAVP gcmEncryptExtIV256.rsp
//        - encrypt: our ciphertext and tag must equal NIST's, byte for byte
//        - decrypt: the original plaintext must come back and the tag must verify
//   2. ChaCha20-Poly1305: the RFC 8439 section 2.8.2 vector (same two checks)
//   3. Tamper tests: flip one bit in the ciphertext, the tag, or the AAD and
//      decryption must be REJECTED. An AEAD cipher that accepts modified data
//      is broken, so this is just as important as the "happy path".
//
// ctest runs this program twice: normally, and with
// OPENSSL_ia32cap=~0x200000200000000, which hides AES-NI from OpenSSL so its
// pure-software AES code is used. Both runs must pass.
//
// Usage: kat [vector_directory]     (default: the repo's testvectors/ folder)

#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/evp.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

using Bytes = std::vector<unsigned char>;

// unique_ptr wrappers so OpenSSL objects are always freed, even on early return.
struct CtxFree    { void operator()(EVP_CIPHER_CTX* p) const { EVP_CIPHER_CTX_free(p); } };
struct CipherFree { void operator()(EVP_CIPHER* p) const { EVP_CIPHER_free(p); } };
using CtxPtr    = std::unique_ptr<EVP_CIPHER_CTX, CtxFree>;
using CipherPtr = std::unique_ptr<EVP_CIPHER, CipherFree>;

// One AEAD test case: inputs (key, iv, aad, pt) and expected outputs (ct, tag).
struct AeadVector {
    std::string name;
    Bytes key, iv, aad, pt, ct, tag;
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

static std::string trim(const std::string& s) {
    const char* ws = " \t\r\n";
    size_t b = s.find_first_not_of(ws);
    if (b == std::string::npos) return "";
    size_t e = s.find_last_not_of(ws);
    return s.substr(b, e - b + 1);
}

static Bytes from_hex(const std::string& hex) {
    if (hex.size() % 2 != 0) {
        std::fprintf(stderr, "Bad hex string (odd length): %s\n", hex.c_str());
        std::exit(2);
    }
    Bytes out(hex.size() / 2);
    for (size_t i = 0; i < out.size(); ++i)
        out[i] = static_cast<unsigned char>(std::stoul(hex.substr(2 * i, 2), nullptr, 16));
    return out;
}

static std::string to_hex(const Bytes& b) {
    static const char* digits = "0123456789abcdef";
    std::string s;
    for (unsigned char c : b) { s += digits[c >> 4]; s += digits[c & 15]; }
    return s;
}

// Read "Name = hexvalue" lines. Each time a "Tag" line is seen, one test case
// is complete. This format is used by both NIST .rsp files and our RFC file.
static std::vector<AeadVector> load_vectors(const std::string& path, const std::string& label) {
    std::ifstream in(path);
    if (!in) {
        std::fprintf(stderr, "ERROR: cannot open test vector file %s\n", path.c_str());
        std::exit(2);
    }
    std::vector<AeadVector> out;
    AeadVector cur;
    std::string count, line;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        if (line[0] == '[') continue;                 // section headers like [IVlen = 96]
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string name = trim(line.substr(0, eq));
        std::string val  = trim(line.substr(eq + 1));
        if (name == "Count") {
            count = val;
        } else if (name == "Key") {
            cur.key = from_hex(val);
        } else if (name == "IV" || name == "Nonce") {
            cur.iv = from_hex(val);
        } else if (name == "AAD") {
            cur.aad = from_hex(val);
        } else if (name == "PT") {
            cur.pt = from_hex(val);
        } else if (name == "CT") {
            cur.ct = from_hex(val);
        } else if (name == "Tag") {
            cur.tag = from_hex(val);
            cur.name = label + (count.empty() ? "" : " Count=" + count) +
                       " (IV " + std::to_string(cur.iv.size()) + "B, PT " +
                       std::to_string(cur.pt.size()) + "B, AAD " +
                       std::to_string(cur.aad.size()) + "B, tag " +
                       std::to_string(cur.tag.size()) + "B)";
            out.push_back(cur);
            cur = AeadVector{};
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// The two operations under test
// ---------------------------------------------------------------------------

// Set up a context for encryption or decryption with the vector's key and IV.
// The IV length must be set BEFORE the IV itself (NIST uses lengths other
// than the default 12 bytes).
static bool init_ctx(EVP_CIPHER_CTX* ctx, const EVP_CIPHER* cipher, bool encrypt,
                     const Bytes& key, const Bytes& iv) {
    int enc = encrypt ? 1 : 0;
    if (EVP_CipherInit_ex2(ctx, cipher, nullptr, nullptr, enc, nullptr) != 1) return false;
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, static_cast<int>(iv.size()), nullptr) != 1) return false;
    return EVP_CipherInit_ex2(ctx, nullptr, key.data(), iv.data(), enc, nullptr) == 1;
}

// Encrypt v.pt with v.aad. Produces ciphertext and a tag of v.tag.size() bytes.
static bool aead_encrypt(const EVP_CIPHER* cipher, const AeadVector& v, Bytes& ct, Bytes& tag) {
    CtxPtr ctx(EVP_CIPHER_CTX_new());
    if (!ctx || !init_ctx(ctx.get(), cipher, true, v.key, v.iv)) return false;
    int len = 0;
    // AAD = "additional authenticated data": protected by the tag but not encrypted.
    // Passing a null output pointer tells OpenSSL "this is AAD".
    if (!v.aad.empty() &&
        EVP_EncryptUpdate(ctx.get(), nullptr, &len, v.aad.data(), static_cast<int>(v.aad.size())) != 1)
        return false;
    Bytes buf(v.pt.size() + 16);                 // never empty, so buf.data() is never null
    int total = 0;
    if (!v.pt.empty()) {
        if (EVP_EncryptUpdate(ctx.get(), buf.data(), &len, v.pt.data(), static_cast<int>(v.pt.size())) != 1)
            return false;
        total = len;
    }
    if (EVP_EncryptFinal_ex(ctx.get(), buf.data() + total, &len) != 1) return false;
    total += len;
    buf.resize(total);
    ct = buf;
    tag.assign(v.tag.size(), 0);
    return EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_GET_TAG, static_cast<int>(tag.size()), tag.data()) == 1;
}

// Decrypt ct with aad and check the tag. Returns true ONLY if the tag verifies.
static bool aead_decrypt(const EVP_CIPHER* cipher, const Bytes& key, const Bytes& iv,
                         const Bytes& aad, const Bytes& ct, const Bytes& tag, Bytes& pt) {
    CtxPtr ctx(EVP_CIPHER_CTX_new());
    if (!ctx || !init_ctx(ctx.get(), cipher, false, key, iv)) return false;
    int len = 0;
    if (!aad.empty() &&
        EVP_DecryptUpdate(ctx.get(), nullptr, &len, aad.data(), static_cast<int>(aad.size())) != 1)
        return false;
    Bytes buf(ct.size() + 16);
    int total = 0;
    if (!ct.empty()) {
        if (EVP_DecryptUpdate(ctx.get(), buf.data(), &len, ct.data(), static_cast<int>(ct.size())) != 1)
            return false;
        total = len;
    }
    // Give OpenSSL the tag we received; DecryptFinal compares it with the tag it computes.
    Bytes tag_copy = tag;
    if (EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_AEAD_SET_TAG, static_cast<int>(tag_copy.size()), tag_copy.data()) != 1)
        return false;
    int ok = EVP_DecryptFinal_ex(ctx.get(), buf.data() + total, &len);
    if (ok <= 0) { ERR_clear_error(); return false; }    // tag mismatch = authentication failure
    total += len;
    buf.resize(total);
    pt = buf;
    return true;
}

// ---------------------------------------------------------------------------
// Test runners
// ---------------------------------------------------------------------------

struct Tally { int pass = 0, fail = 0; };

static void report_fail(Tally& t, const std::string& what) {
    ++t.fail;
    if (t.fail <= 10) std::printf("  FAIL: %s\n", what.c_str());   // don't flood the screen
}

// Known-answer check: encrypt must reproduce ct+tag; decrypt must reproduce pt.
static Tally run_kat(const EVP_CIPHER* cipher, const std::vector<AeadVector>& vectors) {
    Tally t;
    for (const auto& v : vectors) {
        Bytes ct, tag, pt;
        if (!aead_encrypt(cipher, v, ct, tag)) { report_fail(t, v.name + ": encrypt error"); continue; }
        if (ct != v.ct)   { report_fail(t, v.name + ": ciphertext mismatch, got " + to_hex(ct)); continue; }
        if (tag != v.tag) { report_fail(t, v.name + ": tag mismatch, got " + to_hex(tag)); continue; }
        if (!aead_decrypt(cipher, v.key, v.iv, v.aad, v.ct, v.tag, pt)) {
            report_fail(t, v.name + ": decrypt rejected a valid tag"); continue;
        }
        if (pt != v.pt) { report_fail(t, v.name + ": decrypted plaintext mismatch"); continue; }
        ++t.pass;
    }
    return t;
}

// Tamper check: one flipped bit anywhere must make decryption fail.
static Tally run_tamper(const EVP_CIPHER* cipher, const AeadVector& v) {
    Tally t;
    Bytes pt;
    auto expect_reject = [&](const char* what, const Bytes& aad, const Bytes& ct, const Bytes& tag) {
        if (aead_decrypt(cipher, v.key, v.iv, aad, ct, tag, pt))
            report_fail(t, v.name + ": " + what + " was ACCEPTED (should be rejected)");
        else
            ++t.pass;
    };
    Bytes bad_ct = v.ct;   bad_ct[0] ^= 0x01;
    Bytes bad_tag = v.tag; bad_tag[0] ^= 0x01;
    Bytes bad_aad = v.aad; bad_aad[0] ^= 0x01;
    expect_reject("tampered ciphertext", v.aad, bad_ct, v.tag);
    expect_reject("tampered tag",        v.aad, v.ct, bad_tag);
    expect_reject("tampered AAD",        bad_aad, v.ct, v.tag);
    return t;
}

static bool print_result(const char* title, const Tally& t) {
    std::printf("%-46s %5d passed, %d failed  -> %s\n", title, t.pass, t.fail,
                (t.fail == 0 && t.pass > 0) ? "OK" : "*** FAILED ***");
    return t.fail == 0 && t.pass > 0;
}

int main(int argc, char** argv) {
    std::string dir = (argc > 1) ? argv[1] : KAT_VECTOR_DIR;
    const char* mask = std::getenv("OPENSSL_ia32cap");

    std::printf("OpenSSL version : %s\n", OpenSSL_version(OPENSSL_VERSION));
    std::printf("OPENSSL_ia32cap : %s\n", mask ? mask : "(not set - hardware acceleration allowed)");
    std::printf("Vector directory: %s\n\n", dir.c_str());

    CipherPtr gcm(EVP_CIPHER_fetch(nullptr, "AES-256-GCM", nullptr));
    CipherPtr chacha(EVP_CIPHER_fetch(nullptr, "ChaCha20-Poly1305", nullptr));
    if (!gcm || !chacha) { std::fprintf(stderr, "ERROR: could not load ciphers from OpenSSL\n"); return 2; }

    auto nist = load_vectors(dir + "/gcmEncryptExtIV256.rsp", "NIST GCM-256");
    auto rfc  = load_vectors(dir + "/rfc8439_sec2.8.2.txt", "RFC 8439 2.8.2");
    if (nist.empty() || rfc.size() != 1) { std::fprintf(stderr, "ERROR: test vector files look wrong\n"); return 2; }

    // For the GCM tamper test, use the first NIST vector that has plaintext, AAD and a full 16-byte tag.
    const AeadVector* gcm_tamper = nullptr;
    for (const auto& v : nist)
        if (!v.pt.empty() && !v.aad.empty() && v.tag.size() == 16) { gcm_tamper = &v; break; }
    if (!gcm_tamper) { std::fprintf(stderr, "ERROR: no suitable vector for GCM tamper test\n"); return 2; }

    bool ok = true;
    ok &= print_result("AES-256-GCM known answers (NIST CAVP)", run_kat(gcm.get(), nist));
    ok &= print_result("ChaCha20-Poly1305 known answer (RFC 8439)", run_kat(chacha.get(), rfc));
    ok &= print_result("AES-256-GCM tamper rejection", run_tamper(gcm.get(), *gcm_tamper));
    ok &= print_result("ChaCha20-Poly1305 tamper rejection", run_tamper(chacha.get(), rfc[0]));

    if (ok) {
        std::printf("\nALL CORRECTNESS TESTS PASSED\n");
        return 0;
    }
    std::printf("\n**************************************************\n"
                "*** CORRECTNESS TESTS FAILED - DO NOT BENCHMARK ***\n"
                "**************************************************\n");
    return 1;
}
