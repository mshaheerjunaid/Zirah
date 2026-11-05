/*
 * Native (PC) tests for the firmware's ML-DSA code, built from the exact
 * firmware sources (firmware/src/zirah_mldsa.c and the reference code in
 * firmware/mldsa) for ML-DSA-65.
 *
 *   1. zirah_keypair() is byte-identical to the reference crypto_sign_keypair()
 *   2. masked signing is byte-identical to the unmasked reference signer for
 *      the same key, message, context and rnd, across share refreshes
 *   3. zirah_verify_ctx() accepts every valid signature and rejects tampered
 *      signatures, other messages, other contexts, other keys and bad
 *      lengths, agreeing with the reference verifier in every case
 *   4. the boot self-test procedure of main.c reproduces the known answers in
 *      kat_vectors.h, which were computed with dilithium-py
 *   5. zirah_ct_equal() and zirah_memzero() behave
 *   6. the shuffling: the coefficient order is a uniform random permutation,
 *      a refresh draws a new order, and no shuffle seed changes a signature
 *
 * Deterministic: all randomness comes from SHAKE256 streams with fixed
 * seeds, so a failure always reproduces. Exit status 0 means every check
 * passed; the count of checks is printed.
 *
 * Every key and signature produced is also absorbed into a transcript hash,
 * printed before the summary. tests/qemu builds this same file for a
 * Cortex-M4 with the firmware's assembly NTT and runs it in QEMU; the two
 * transcripts must be equal, which shows the assembly gives byte-identical
 * keys and signatures to the C code in every case tested here.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "api.h"
#include "fips202.h"
#include "params.h"
#include "randombytes.h"
#include "sign.h"

#include "kat_vectors.h"
#include "secure.h"
#include "zirah_mldsa.h"

#if DILITHIUM_MODE != 3
#error "these tests and the known answers are for ML-DSA-65 (DILITHIUM_MODE=3)"
#endif

static unsigned long g_checks, g_failed;
static keccak_state g_transcript;

static void note(const uint8_t *x, size_t n)
{
    shake256_absorb(&g_transcript, x, n);
}

#define CHECK(cond, ...)                                  \
    do {                                                  \
        g_checks++;                                       \
        if (!(cond)) {                                    \
            g_failed++;                                   \
            fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
            fprintf(stderr, __VA_ARGS__);                 \
            fputc('\n', stderr);                          \
        }                                                 \
    } while (0)

/* ------------------------------------------------------------------------ */
/* Deterministic randomness                                                  */
/* ------------------------------------------------------------------------ */

static keccak_state g_rng;       /* masks, rnd, messages   */
static uint8_t g_seed[32];       /* next key-generation seed */

static void rng_reset(const char *label, unsigned i)
{
    char buf[64];
    int n = snprintf(buf, sizeof(buf), "%s-%u", label, i);
    shake256_init(&g_rng);
    shake256_absorb(&g_rng, (const uint8_t *)buf, (size_t)n);
    shake256_finalize(&g_rng);
}

static void rnd_bytes(uint8_t *out, size_t n)
{
    shake256_squeeze(out, n, &g_rng);
}

static int test_rng(uint8_t *out, size_t n)
{
    rnd_bytes(out, n);
    return 0;
}

/* The reference crypto_sign_keypair() draws its seed through randombytes(). */
void randombytes(uint8_t *out, size_t outlen)
{
    if (outlen != SEEDBYTES) {
        fprintf(stderr, "unexpected randombytes(%zu)\n", outlen);
        exit(2);
    }
    memcpy(out, g_seed, SEEDBYTES);
}

static int seed_rng(uint8_t *out, size_t n)
{
    if (n != SEEDBYTES) {
        return -1;
    }
    memcpy(out, g_seed, SEEDBYTES);
    return 0;
}

static int failing_rng(uint8_t *out, size_t n)
{
    (void)out;
    (void)n;
    return -1;
}

/* ------------------------------------------------------------------------ */

static uint8_t pk_ref[CRYPTO_PUBLICKEYBYTES], sk_ref[CRYPTO_SECRETKEYBYTES];
static uint8_t pk[CRYPTO_PUBLICKEYBYTES], sk[CRYPTO_SECRETKEYBYTES];
static uint8_t pk_other[CRYPTO_PUBLICKEYBYTES];
static uint8_t sig[CRYPTO_BYTES + 1], sig_ref[CRYPTO_BYTES], bad[CRYPTO_BYTES + 1];
static zirah_work_t g_work;
static zirah_msk_t g_msk;

static void make_key(unsigned i)
{
    char label[32];
    int n = snprintf(label, sizeof(label), "zirah-test-seed-%u", i);
    shake256(g_seed, sizeof(g_seed), (const uint8_t *)label, (size_t)n);
}

static void test_keypair(void)
{
    for (unsigned i = 0; i < 6; i++) {
        make_key(i);
        crypto_sign_keypair(pk_ref, sk_ref);
        int rc = zirah_keypair(pk, sk, &g_work, seed_rng);
        CHECK(rc == 0, "zirah_keypair returned %d", rc);
        CHECK(memcmp(pk, pk_ref, sizeof(pk)) == 0, "key %u: public key differs", i);
        CHECK(memcmp(sk, sk_ref, sizeof(sk)) == 0, "key %u: secret key differs", i);
        note(pk, sizeof(pk));
        note(sk, sizeof(sk));
    }
    CHECK(zirah_keypair(pk, sk, &g_work, failing_rng) == -1,
          "zirah_keypair must fail when the RNG fails");
}

static int ref_verify(const uint8_t *s, size_t slen, const uint8_t *m, size_t mlen,
                      const uint8_t *ctx, size_t ctxlen, const uint8_t *key)
{
    return crypto_sign_verify(s, slen, m, mlen, ctx, ctxlen, key);
}

static int zv(const uint8_t *s, size_t slen, const uint8_t *m, size_t mlen, const uint8_t *ctx,
              size_t ctxlen, const uint8_t *key)
{
    return zirah_verify_ctx(s, slen, m, mlen, ctx, ctxlen, key, &g_work);
}

static void test_sign_and_verify(void)
{
    static uint8_t msg[1500], ctx255[255];
    const size_t mlens[] = {0, 1, 32, 1500};
    const char *doc_ctx = "zirah-doc-v1";

    for (unsigned k = 0; k < 4; k++) {
        make_key(100 + k);
        CHECK(zirah_keypair(pk, sk, &g_work, seed_rng) == 0, "keygen");
        make_key(200 + k);
        CHECK(zirah_keypair(pk_other, sk_ref, &g_work, seed_rng) == 0, "keygen other");

        rng_reset("masks", k);
        CHECK(zirah_msk_load(&g_msk, sk, test_rng) == 0, "msk_load");
        CHECK(zirah_msk_check(&g_msk, sk) == 0, "msk_check after load");
        CHECK(zirah_msk_load(&g_msk, sk, failing_rng) != 0, "msk_load must fail without RNG");
        CHECK(zirah_msk_load(&g_msk, sk, test_rng) == 0, "msk_load again");

        for (unsigned r = 0; r < 3; r++) {
            CHECK(zirah_msk_refresh(&g_msk, test_rng) == 0, "refresh");
            CHECK(zirah_msk_check(&g_msk, sk) == 0, "shares must still recombine to sk");

            for (unsigned mi = 0; mi < sizeof(mlens) / sizeof(mlens[0]); mi++) {
                size_t mlen = mlens[mi];
                uint8_t rnd[RNDBYTES];
                rnd_bytes(msg, mlen);
                rnd_bytes(ctx255, sizeof(ctx255));
                if (mi == 0) {
                    memset(rnd, 0, sizeof(rnd)); /* deterministic variant */
                } else {
                    rnd_bytes(rnd, sizeof(rnd));
                }
                const uint8_t *ctx = (const uint8_t *)doc_ctx;
                size_t ctxlen = strlen(doc_ctx);
                if (mi == 1) {
                    ctx = NULL;
                    ctxlen = 0;
                } else if (mi == 3) {
                    ctx = ctx255;
                    ctxlen = 255;
                }

                size_t l1 = 0, l2 = 0;
                uint32_t attempts = 0;
                int rc = zirah_sign_ctx(sig, &l1, msg, mlen, ctx, ctxlen, rnd, &g_msk, &attempts);
                CHECK(rc == 0, "masked sign rc=%d", rc);
                rc = zirah_sign_ref_ctx(sig_ref, &l2, msg, mlen, ctx, ctxlen, rnd, sk);
                CHECK(rc == 0, "reference sign rc=%d", rc);
                CHECK(l1 == CRYPTO_BYTES && l2 == CRYPTO_BYTES, "signature lengths %zu %zu", l1, l2);
                CHECK(memcmp(sig, sig_ref, CRYPTO_BYTES) == 0,
                      "key %u refresh %u msg %u: masked signature differs from reference", k, r, mi);
                note(sig, l1);
                CHECK(attempts >= 1, "attempts counter");

                /* Valid signature: both verifiers accept. */
                CHECK(zv(sig, l1, msg, mlen, ctx, ctxlen, pk) == 0, "zirah_verify_ctx rejects valid");
                CHECK(ref_verify(sig, l1, msg, mlen, ctx, ctxlen, pk) == 0, "reference rejects valid");

                /* Tampered signature bits: both reject. */
                for (unsigned t = 0; t < 12; t++) {
                    uint8_t pos[4];
                    rnd_bytes(pos, sizeof(pos));
                    size_t byte = ((size_t)pos[0] << 8 | pos[1]) % CRYPTO_BYTES;
                    memcpy(bad, sig, CRYPTO_BYTES);
                    bad[byte] ^= (uint8_t)(1u << (pos[2] & 7));
                    int a = zv(bad, CRYPTO_BYTES, msg, mlen, ctx, ctxlen, pk);
                    int b = ref_verify(bad, CRYPTO_BYTES, msg, mlen, ctx, ctxlen, pk);
                    CHECK(a != 0, "tampered byte %zu accepted", byte);
                    CHECK((a == 0) == (b == 0), "verifiers disagree on tampered byte %zu", byte);
                }

                /* Other message, other context, other key, bad lengths. */
                if (mlen > 0) {
                    msg[mlen / 2] ^= 0x01;
                    CHECK(zv(sig, l1, msg, mlen, ctx, ctxlen, pk) != 0, "other message accepted");
                    CHECK(ref_verify(sig, l1, msg, mlen, ctx, ctxlen, pk) != 0, "reference: other message");
                    msg[mlen / 2] ^= 0x01;
                }
                const uint8_t *other_ctx = (const uint8_t *)"zirah-attest-v1";
                CHECK(zv(sig, l1, msg, mlen, other_ctx, 15, pk) != 0, "other context accepted");
                CHECK(zv(sig, l1, msg, mlen, ctx, ctxlen, pk_other) != 0, "other key accepted");
                CHECK(ref_verify(sig, l1, msg, mlen, ctx, ctxlen, pk_other) != 0, "reference: other key");
                CHECK(zv(sig, l1 - 1, msg, mlen, ctx, ctxlen, pk) != 0, "short signature accepted");
                memcpy(bad, sig, CRYPTO_BYTES);
                bad[CRYPTO_BYTES] = 0;
                CHECK(zv(bad, CRYPTO_BYTES + 1, msg, mlen, ctx, ctxlen, pk) != 0,
                      "long signature accepted");
                CHECK(zv(sig, l1, msg, mlen, ctx255, 256, pk) != 0, "context over 255 bytes accepted");
            }
        }
        zirah_msk_wipe(&g_msk);
        int all_zero = 1;
        for (size_t i = 0; i < sizeof(g_msk); i++) {
            all_zero &= ((const uint8_t *)&g_msk)[i] == 0;
        }
        CHECK(all_zero, "zirah_msk_wipe left data behind");
    }

    static uint8_t ctx256[256];
    uint8_t rnd0[RNDBYTES] = {0};
    size_t l = 0;
    CHECK(zirah_sign_ctx(sig, &l, msg, 1, ctx256, sizeof(ctx256), rnd0, &g_msk, NULL) != 0,
          "sign with a 256-byte context must fail");
}

/* Same steps as boot_kat() in firmware/src/main.c. */
static keccak_state g_kat_prg;
static int kat_seed_rng(uint8_t *out, size_t n)
{
    if (n != 32) {
        return -1;
    }
    memcpy(out, k_kat_xi, 32);
    return 0;
}
static int kat_mask_rng(uint8_t *out, size_t n)
{
    shake256_squeeze(out, n, &g_kat_prg);
    return 0;
}

static void test_boot_kat(void)
{
    uint8_t out[32], f[16];
    shake256(out, 32, (const uint8_t *)ZIRAH_KAT_SHAKE_MSG, sizeof(ZIRAH_KAT_SHAKE_MSG) - 1);
    CHECK(memcmp(out, k_kat_shake, 32) == 0, "SHAKE256 known answer");

    CHECK(zirah_keypair(pk, sk, &g_work, kat_seed_rng) == 0, "KAT keygen");
    shake256(f, 16, pk, CRYPTO_PUBLICKEYBYTES);
    CHECK(memcmp(f, k_kat_pk_fp, 16) == 0, "KAT public key fingerprint");

    shake256_init(&g_kat_prg);
    shake256_absorb(&g_kat_prg, (const uint8_t *)ZIRAH_KAT_MASK_SEED, sizeof(ZIRAH_KAT_MASK_SEED) - 1);
    shake256_finalize(&g_kat_prg);
    CHECK(zirah_msk_load(&g_msk, sk, kat_mask_rng) == 0, "KAT mask");
    uint8_t rnd[32] = {0};
    size_t siglen = 0;
    zirah_sign_ctx(sig, &siglen, (const uint8_t *)ZIRAH_KAT_MSG, sizeof(ZIRAH_KAT_MSG) - 1,
                   (const uint8_t *)ZIRAH_KAT_CTX, sizeof(ZIRAH_KAT_CTX) - 1, rnd, &g_msk, NULL);
    shake256(f, 16, sig, siglen);
    CHECK(siglen == CRYPTO_BYTES && memcmp(f, k_kat_sig_fp, 16) == 0, "KAT signature fingerprint");
    note(sig, siglen);
    CHECK(zv(sig, siglen, (const uint8_t *)ZIRAH_KAT_MSG, sizeof(ZIRAH_KAT_MSG) - 1,
             (const uint8_t *)ZIRAH_KAT_CTX, sizeof(ZIRAH_KAT_CTX) - 1, pk) == 0,
          "KAT signature verifies");

    /* The masks must not matter: a different mask stream, same signature. */
    rng_reset("other-masks", 0);
    CHECK(zirah_msk_load(&g_msk, sk, test_rng) == 0, "remask");
    uint8_t sig2[CRYPTO_BYTES];
    size_t l2 = 0;
    zirah_sign_ctx(sig2, &l2, (const uint8_t *)ZIRAH_KAT_MSG, sizeof(ZIRAH_KAT_MSG) - 1,
                   (const uint8_t *)ZIRAH_KAT_CTX, sizeof(ZIRAH_KAT_CTX) - 1, rnd, &g_msk, NULL);
    CHECK(l2 == siglen && memcmp(sig, sig2, siglen) == 0, "signature depends on the masks");
    zirah_msk_wipe(&g_msk);
}

void zirah_test_shuffle_order(const uint8_t seed[32], uint8_t perm[N]); /* ZIRAH_NATIVE_TEST */

static void test_shuffling(void)
{
    /* Every order is a permutation, and over many seeds the first and last
     * positions take every value about equally often (chi-square with 255
     * degrees of freedom, mean 255; 400 is far beyond any plausible chance
     * value, and the seeds are fixed, so the test cannot flake). */
    enum { PERMS = 25600 };
    static unsigned first[N], last[N];
    unsigned not_perm = 0, identity = 0, same_as_prev = 0;
    uint8_t seed[32], perm[N], prev[N];
    memset(prev, 0, sizeof(prev));
    rng_reset("shuffle", 0);
    for (unsigned t = 0; t < PERMS; t++) {
        rnd_bytes(seed, sizeof(seed));
        zirah_test_shuffle_order(seed, perm);
        uint8_t seen[N];
        unsigned ident = 1;
        memset(seen, 0, sizeof(seen));
        for (unsigned i = 0; i < N; i++) {
            seen[perm[i]]++;
            ident &= perm[i] == i;
        }
        for (unsigned i = 0; i < N; i++) {
            not_perm += seen[i] != 1;
        }
        identity += ident;
        same_as_prev += memcmp(perm, prev, N) == 0;
        memcpy(prev, perm, N);
        first[perm[0]]++;
        last[perm[N - 1]]++;
    }
    CHECK(not_perm == 0, "shuffle order is not a permutation (%u bad entries)", not_perm);
    CHECK(identity == 0, "shuffle order was the identity %u times", identity);
    CHECK(same_as_prev == 0, "two seeds in a row gave the same order");
    double e = (double)PERMS / N, c0 = 0, c1 = 0;
    for (unsigned v = 0; v < N; v++) {
        c0 += (first[v] - e) * (first[v] - e) / e;
        c1 += (last[v] - e) * (last[v] - e) / e;
    }
    CHECK(c0 < 400.0, "first position not uniform, chi-square %.1f", c0);
    CHECK(c1 < 400.0, "last position not uniform, chi-square %.1f", c1);

    /* The order must never change a signature: one key and one set of masks,
     * many different shuffle seeds, and every signature equal to the
     * reference signer's. A refresh must also draw a new seed. */
    make_key(300);
    CHECK(zirah_keypair(pk, sk, &g_work, seed_rng) == 0, "keygen for shuffling");
    rng_reset("shuffle-masks", 0);
    CHECK(zirah_msk_load(&g_msk, sk, test_rng) == 0, "msk_load for shuffling");
    uint8_t before[32];
    memcpy(before, g_msk.shuffle_seed, sizeof(before));
    CHECK(zirah_msk_refresh(&g_msk, test_rng) == 0, "refresh for shuffling");
    CHECK(memcmp(before, g_msk.shuffle_seed, sizeof(before)) != 0, "refresh kept the shuffle seed");
    CHECK(zirah_msk_check(&g_msk, sk) == 0, "shuffled refresh must still recombine to sk");
    uint8_t m[48], rnd[RNDBYTES];
    rnd_bytes(m, sizeof(m));
    rnd_bytes(rnd, sizeof(rnd));
    size_t lr = 0;
    CHECK(zirah_sign_ref_ctx(sig_ref, &lr, m, sizeof(m), (const uint8_t *)"zirah-doc-v1", 12, rnd,
                             sk) == 0,
          "reference sign for shuffling");
    for (unsigned t = 0; t < 12; t++) {
        if (t == 0) {
            memset(g_msk.shuffle_seed, 0x00, sizeof(g_msk.shuffle_seed));
        } else if (t == 1) {
            memset(g_msk.shuffle_seed, 0xFF, sizeof(g_msk.shuffle_seed));
        } else {
            rnd_bytes(g_msk.shuffle_seed, sizeof(g_msk.shuffle_seed));
        }
        size_t l = 0;
        CHECK(zirah_sign_ctx(sig, &l, m, sizeof(m), (const uint8_t *)"zirah-doc-v1", 12, rnd, &g_msk,
                             NULL) == 0,
              "masked sign for shuffling");
        CHECK(l == lr && memcmp(sig, sig_ref, CRYPTO_BYTES) == 0,
              "shuffle seed %u changed the signature", t);
        note(sig, l);
    }
    zirah_msk_wipe(&g_msk);
}

static void test_secure_helpers(void)
{
    uint8_t a[64], b[64];
    rng_reset("helpers", 0);
    rnd_bytes(a, sizeof(a));
    memcpy(b, a, sizeof(b));
    CHECK(zirah_ct_equal(a, b, sizeof(a)), "ct_equal equal");
    for (size_t i = 0; i < sizeof(a); i += 7) {
        b[i] ^= 0x80;
        CHECK(!zirah_ct_equal(a, b, sizeof(a)), "ct_equal misses a difference at %zu", i);
        b[i] ^= 0x80;
    }
    CHECK(zirah_ct_equal(a, b, 0), "ct_equal of zero bytes");
    zirah_memzero(a, sizeof(a));
    int z = 1;
    for (size_t i = 0; i < sizeof(a); i++) {
        z &= a[i] == 0;
    }
    CHECK(z, "memzero");
}

int main(void)
{
    shake256_init(&g_transcript);
    test_secure_helpers();
    test_keypair();
    test_sign_and_verify();
    test_boot_kat();
    test_shuffling();
    uint8_t t[16];
    shake256_finalize(&g_transcript);
    shake256_squeeze(t, sizeof(t), &g_transcript);
    printf("transcript ");
    for (size_t i = 0; i < sizeof(t); i++) {
        printf("%02x", t[i]);
    }
    printf("\n");
    printf("ML-DSA-65 native tests: %lu checks, %lu failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
