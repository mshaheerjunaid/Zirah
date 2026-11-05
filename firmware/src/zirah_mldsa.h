/*
 * Zirah masked ML-DSA signing (ML-DSA-65 in the shipped build).
 *
 * What is masked (first-order, two arithmetic shares mod q):
 *   the secret vectors s1 and s2, kept permanently in the NTT domain as
 *   (share0, share1) with share0 + share1 = NTT(s) mod q, and the products
 *   c*s1 and c*s2, which are computed share by share. Shares are refreshed
 *   with fresh randomness before every signature.
 *
 * Shuffled (on top of the masking): every pass over a share, in the refresh
 *   and in the products c*s1 and c*s2, visits the 256 coefficients in a
 *   fresh random order, and which share goes first is random too.
 *
 * What is NOT masked (stated plainly so nobody over-reads the design):
 *   the masking vector y and its SHAKE256 expansion, the matrix-vector
 *   product, Decompose/HighBits/LowBits, the rejection checks, hint
 *   generation, t0, and the key K. Recombination happens when z = y + c*s1
 *   and r0 = w0 - c*s2 are formed. A fully masked ML-DSA needs masked Keccak
 *   and mask conversions for the non-linear steps; that is out of scope here.
 *
 * The signatures are bit-identical to the unmasked reference implementation
 * for the same key, message, context and rnd.
 */
#ifndef ZIRAH_MLDSA_H
#define ZIRAH_MLDSA_H

#include <stdint.h>
#include <stddef.h>

#include "params.h"
#include "polyvec.h"

/* Callback that fills `out` with `n` random bytes; returns 0 on success. */
typedef int (*zirah_rng_fn)(uint8_t *out, size_t n);

typedef struct {
    uint8_t rho[SEEDBYTES];
    uint8_t tr[TRBYTES];
    uint8_t key[SEEDBYTES];
    polyveck t0hat;     /* unmasked (see header comment) */
    polyvecl s1hat[2];  /* arithmetic shares of NTT(s1), each in [0, q) */
    polyveck s2hat[2];  /* arithmetic shares of NTT(s2), each in [0, q) */
    uint8_t shuffle_seed[32]; /* order of the share-wise products, new at each refresh */
    uint32_t refreshes;
} zirah_msk_t;

/* Unpack a packed ML-DSA secret key into the shared representation. */
int zirah_msk_load(zirah_msk_t *msk, const uint8_t *sk, zirah_rng_fn rng);

/* Re-randomise the shares (two passes: share 0 first, then share 1). */
int zirah_msk_refresh(zirah_msk_t *msk, zirah_rng_fn rng);

/* Recombine shares and compare with a packed key (debug / self-test only). */
int zirah_msk_check(const zirah_msk_t *msk, const uint8_t *sk);

void zirah_msk_wipe(zirah_msk_t *msk);

/* Large temporaries for key generation and verification. */
typedef struct {
    polyveck a;
    polyveck b;
    polyvecl c;
} zirah_work_t;

/* FIPS 204 ML-DSA.KeyGen, byte-identical to the reference, but using static
 * and caller-provided memory instead of ~58 KB of stack. 0 on success. */
int zirah_keypair(uint8_t *pk, uint8_t *sk, zirah_work_t *w, zirah_rng_fn rng);

/* FIPS 204 ML-DSA.Verify with a context string. 0 if valid, -1 otherwise.
 * Used on the device to check every signature before it is released. */
int zirah_verify_ctx(const uint8_t *sig, size_t siglen,
                     const uint8_t *m, size_t mlen,
                     const uint8_t *ctx, size_t ctxlen,
                     const uint8_t *pk, zirah_work_t *w);

/*
 * Masked signature. `pre` is (0, ctxlen, ctx) as in FIPS 204; use
 * zirah_sign_ctx() for the normal entry point.
 * *attempts receives the number of rejection-loop iterations used.
 */
int zirah_sign_masked_internal(uint8_t *sig, size_t *siglen,
                               const uint8_t *m, size_t mlen,
                               const uint8_t *pre, size_t prelen,
                               const uint8_t rnd[RNDBYTES],
                               const zirah_msk_t *msk,
                               uint32_t *attempts);

int zirah_sign_ctx(uint8_t *sig, size_t *siglen,
                   const uint8_t *m, size_t mlen,
                   const uint8_t *ctx, size_t ctxlen,
                   const uint8_t rnd[RNDBYTES],
                   const zirah_msk_t *msk, uint32_t *attempts);

/* Unmasked reference signature with the same interface (baseline). */
int zirah_sign_ref_ctx(uint8_t *sig, size_t *siglen,
                       const uint8_t *m, size_t mlen,
                       const uint8_t *ctx, size_t ctxlen,
                       const uint8_t rnd[RNDBYTES],
                       const uint8_t *sk);

#endif
