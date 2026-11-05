#include "zirah_mldsa.h"

#include <string.h>

#include "packing.h"
#include "poly.h"
#include "reduce.h"
#include "sign.h"
#include "symmetric.h"
#include "fips202.h"
#include "secure.h"

/* ------------------------------------------------------------------------ */
/* Uniform values mod q from a SHAKE256 stream (fresh seed from `rng`)       */
/* ------------------------------------------------------------------------ */

typedef struct {
    keccak_state st;
    uint8_t buf[SHAKE256_RATE];
    unsigned pos;
} prg_t;

static void prg_seed(prg_t *p, const char label[13], const uint8_t seed[32])
{
    shake256_init(&p->st);
    shake256_absorb(&p->st, (const uint8_t *)label, 13);
    shake256_absorb(&p->st, seed, 32);
    shake256_finalize(&p->st);
    p->pos = SHAKE256_RATE;
}

static int prg_init(prg_t *p, zirah_rng_fn rng)
{
    uint8_t seed[32];
    if (rng(seed, sizeof(seed)) != 0) {
        return -1;
    }
    prg_seed(p, "ZIRAH-MASK-v1", seed);
    zirah_memzero(seed, sizeof(seed));
    return 0;
}

static uint8_t prg_byte(prg_t *p)
{
    if (p->pos == SHAKE256_RATE) {
        shake256_squeeze(p->buf, SHAKE256_RATE, &p->st);
        p->pos = 0;
    }
    return p->buf[p->pos++];
}

static int32_t prg_mod_q(prg_t *p)
{
    for (;;) {
        uint32_t t = prg_byte(p);
        t |= (uint32_t)prg_byte(p) << 8;
        t |= (uint32_t)prg_byte(p) << 16;
        t &= 0x7FFFFF;
        if (t < Q) {
            return (int32_t)t;
        }
    }
}

static void prg_wipe(prg_t *p)
{
    zirah_memzero(p, sizeof(*p));
}

/*
 * A uniformly random permutation of 0..N-1 (Fisher-Yates). Each index j in
 * [0, i] comes from one byte masked to the next power of two above i, with
 * rejection, so there is no modulo bias. The number of rejections depends
 * only on the random stream, never on secret data.
 */
_Static_assert(N == 256, "permutation indices are stored in bytes");

static void prg_perm(prg_t *p, uint8_t perm[N])
{
    for (unsigned i = 0; i < N; i++) {
        perm[i] = (uint8_t)i;
    }
    for (unsigned i = N - 1; i > 0; i--) {
        unsigned m = i;
        m |= m >> 1;
        m |= m >> 2;
        m |= m >> 4;
        unsigned j;
        do {
            j = prg_byte(p) & m;
        } while (j > i);
        uint8_t t = perm[i];
        perm[i] = perm[j];
        perm[j] = t;
    }
}

/* Canonical representative in [0, q). */
static int32_t canon(int32_t a)
{
    return caddq(reduce32(a));
}

/* ------------------------------------------------------------------------ */
/* Share handling                                                            */
/* ------------------------------------------------------------------------ */

static int32_t g_r[N];     /* one polynomial of fresh mask values */
static uint8_t g_perm[N];  /* the coefficient order of the current pass */

/*
 * Refresh one polynomial: share0 -= r, then (separate pass) share1 += r.
 * Keeping the two shares in separate loops avoids processing share0[i] and
 * share1[i] back to back, which on many cores leaks through register and bus
 * transitions (the classic distance-leakage pitfall that ELMO models).
 * Each pass visits the coefficients in its own random order, so the instant
 * at which a given coefficient is handled changes from pass to pass and from
 * signature to signature (shuffling, on top of the masking).
 */
static void refresh_poly(poly *a0, poly *a1, prg_t *p)
{
    for (unsigned i = 0; i < N; i++) {
        g_r[i] = prg_mod_q(p);
    }
    prg_perm(p, g_perm);
    for (unsigned k = 0; k < N; k++) {
        unsigned i = g_perm[k];
        int32_t v = a0->coeffs[i] - g_r[i];
        v += (v >> 31) & Q;
        a0->coeffs[i] = v;
    }
    prg_perm(p, g_perm);
    for (unsigned k = 0; k < N; k++) {
        unsigned i = g_perm[k];
        int32_t v = a1->coeffs[i] + g_r[i] - Q;
        v += (v >> 31) & Q;
        a1->coeffs[i] = v;
    }
    zirah_memzero(g_r, sizeof(g_r));
    zirah_memzero(g_perm, sizeof(g_perm));
}

/* Split a polynomial given in [0, q) into (a - r, r). */
static void split_poly(poly *a0, poly *a1, const poly *a, prg_t *p)
{
    for (unsigned i = 0; i < N; i++) {
        g_r[i] = prg_mod_q(p);
    }
    prg_perm(p, g_perm);
    for (unsigned k = 0; k < N; k++) {
        unsigned i = g_perm[k];
        int32_t v = a->coeffs[i] - g_r[i];
        v += (v >> 31) & Q;
        a0->coeffs[i] = v;
    }
    for (unsigned i = 0; i < N; i++) {
        a1->coeffs[i] = g_r[i];
    }
    zirah_memzero(g_r, sizeof(g_r));
    zirah_memzero(g_perm, sizeof(g_perm));
}

#ifdef ZIRAH_NATIVE_TEST
/* Test hook (PC build only): the coefficient order a given shuffle seed
 * produces first, so tests/native can check it is a uniform permutation. */
void zirah_test_shuffle_order(const uint8_t seed[32], uint8_t perm[N]);
void zirah_test_shuffle_order(const uint8_t seed[32], uint8_t perm[N])
{
    prg_t p;
    prg_seed(&p, "ZIRAH-SHUF-v1", seed);
    prg_perm(&p, perm);
    prg_wipe(&p);
}
#endif

/* A fresh seed for the order of the share-wise products in the next
 * signatures, drawn from the same masking stream. */
static void new_shuffle_seed(zirah_msk_t *msk, prg_t *p)
{
    for (unsigned i = 0; i < sizeof(msk->shuffle_seed); i++) {
        msk->shuffle_seed[i] = prg_byte(p);
    }
}

/*
 * One share of c * s, coefficient by coefficient in a random order. The
 * arithmetic is exactly poly_pointwise_montgomery() of the reference code,
 * and each output coefficient depends only on its own inputs, so the order
 * cannot change the result.
 */
static void poly_pointwise_shuffled(poly *r, const poly *c, const poly *s, prg_t *order)
{
    prg_perm(order, g_perm);
    for (unsigned k = 0; k < N; k++) {
        unsigned i = g_perm[k];
        r->coeffs[i] = montgomery_reduce((int64_t)c->coeffs[i] * s->coeffs[i]);
    }
    zirah_memzero(g_perm, sizeof(g_perm));
}

int zirah_msk_load(zirah_msk_t *msk, const uint8_t *sk, zirah_rng_fn rng)
{
    polyvecl s1;
    polyveck s2, t0;
    prg_t p;

    unpack_sk(msk->rho, msk->tr, msk->key, &t0, &s1, &s2, sk);
    polyvecl_ntt(&s1);
    polyveck_ntt(&s2);
    polyveck_ntt(&t0);

    for (unsigned j = 0; j < L; j++) {
        for (unsigned i = 0; i < N; i++) {
            s1.vec[j].coeffs[i] = canon(s1.vec[j].coeffs[i]);
        }
    }
    for (unsigned j = 0; j < K; j++) {
        for (unsigned i = 0; i < N; i++) {
            s2.vec[j].coeffs[i] = canon(s2.vec[j].coeffs[i]);
        }
    }
    msk->t0hat = t0;
    zirah_memzero(&t0, sizeof(t0));

    if (prg_init(&p, rng) != 0) {
        zirah_memzero(&s1, sizeof(s1));
        zirah_memzero(&s2, sizeof(s2));
        return -1;
    }
    for (unsigned j = 0; j < L; j++) {
        split_poly(&msk->s1hat[0].vec[j], &msk->s1hat[1].vec[j], &s1.vec[j], &p);
    }
    for (unsigned j = 0; j < K; j++) {
        split_poly(&msk->s2hat[0].vec[j], &msk->s2hat[1].vec[j], &s2.vec[j], &p);
    }
    new_shuffle_seed(msk, &p);
    prg_wipe(&p);
    zirah_memzero(&s1, sizeof(s1));
    zirah_memzero(&s2, sizeof(s2));
    msk->refreshes = 0;
    return 0;
}

int zirah_msk_refresh(zirah_msk_t *msk, zirah_rng_fn rng)
{
    prg_t p;
    if (prg_init(&p, rng) != 0) {
        return -1;
    }
    for (unsigned j = 0; j < L; j++) {
        refresh_poly(&msk->s1hat[0].vec[j], &msk->s1hat[1].vec[j], &p);
    }
    for (unsigned j = 0; j < K; j++) {
        refresh_poly(&msk->s2hat[0].vec[j], &msk->s2hat[1].vec[j], &p);
    }
    new_shuffle_seed(msk, &p);
    prg_wipe(&p);
    msk->refreshes++;
    return 0;
}

int zirah_msk_check(const zirah_msk_t *msk, const uint8_t *sk)
{
    uint8_t rho[SEEDBYTES], tr[TRBYTES], key[SEEDBYTES];
    polyvecl s1;
    polyveck s2, t0;
    int bad = 0;

    unpack_sk(rho, tr, key, &t0, &s1, &s2, sk);
    polyvecl_ntt(&s1);
    polyveck_ntt(&s2);
    for (unsigned j = 0; j < L; j++) {
        for (unsigned i = 0; i < N; i++) {
            int32_t want = canon(s1.vec[j].coeffs[i]);
            int32_t got = canon(msk->s1hat[0].vec[j].coeffs[i] +
                                msk->s1hat[1].vec[j].coeffs[i]);
            bad |= want != got;
        }
    }
    for (unsigned j = 0; j < K; j++) {
        for (unsigned i = 0; i < N; i++) {
            int32_t want = canon(s2.vec[j].coeffs[i]);
            int32_t got = canon(msk->s2hat[0].vec[j].coeffs[i] +
                                msk->s2hat[1].vec[j].coeffs[i]);
            bad |= want != got;
        }
    }
    bad |= memcmp(rho, msk->rho, SEEDBYTES) != 0;
    bad |= memcmp(tr, msk->tr, TRBYTES) != 0;
    bad |= memcmp(key, msk->key, SEEDBYTES) != 0;
    zirah_memzero(&s1, sizeof(s1));
    zirah_memzero(&s2, sizeof(s2));
    zirah_memzero(&t0, sizeof(t0));
    zirah_memzero(key, sizeof(key));
    return bad ? -1 : 0;
}

void zirah_msk_wipe(zirah_msk_t *msk)
{
    zirah_memzero(msk, sizeof(*msk));
}

/* ------------------------------------------------------------------------ */
/* Signing (mirrors crypto_sign_signature_internal of the reference code)    */
/* ------------------------------------------------------------------------ */

/* Large temporaries are static to keep the stack small on the Cortex-M4. */
static polyvecl g_mat[K];
static polyvecl g_tmpl;
static polyveck g_tmpk;

int zirah_sign_masked_internal(uint8_t *sig, size_t *siglen,
                               const uint8_t *m, size_t mlen,
                               const uint8_t *pre, size_t prelen,
                               const uint8_t rnd[RNDBYTES],
                               const zirah_msk_t *msk,
                               uint32_t *attempts)
{
    unsigned int n;
    uint8_t mu[CRHBYTES], rhoprime[CRHBYTES];
    uint16_t nonce = 0;
    polyvecl y, z;
    polyveck w1, w0, h;
    poly cp;
    keccak_state state;
    prg_t order;
    unsigned first;

    prg_seed(&order, "ZIRAH-SHUF-v1", msk->shuffle_seed);

    /* mu = CRH(tr, pre, msg) */
    shake256_init(&state);
    shake256_absorb(&state, msk->tr, TRBYTES);
    shake256_absorb(&state, pre, prelen);
    shake256_absorb(&state, m, mlen);
    shake256_finalize(&state);
    shake256_squeeze(mu, CRHBYTES, &state);

    /* rhoprime = CRH(key, rnd, mu) */
    shake256_init(&state);
    shake256_absorb(&state, msk->key, SEEDBYTES);
    shake256_absorb(&state, rnd, RNDBYTES);
    shake256_absorb(&state, mu, CRHBYTES);
    shake256_finalize(&state);
    shake256_squeeze(rhoprime, CRHBYTES, &state);

    polyvec_matrix_expand(g_mat, msk->rho);

rej:
    polyvecl_uniform_gamma1(&y, rhoprime, nonce++);

    z = y;
    polyvecl_ntt(&z);
    polyvec_matrix_pointwise_montgomery(&w1, g_mat, &z);
    polyveck_reduce(&w1);
    polyveck_invntt_tomont(&w1);

    polyveck_caddq(&w1);
    polyveck_decompose(&w1, &w0, &w1);
    polyveck_pack_w1(sig, &w1);

    shake256_init(&state);
    shake256_absorb(&state, mu, CRHBYTES);
    shake256_absorb(&state, sig, K * POLYW1_PACKEDBYTES);
    shake256_finalize(&state);
    shake256_squeeze(sig, CTILDEBYTES, &state);
    poly_challenge(&cp, sig);
    poly_ntt(&cp);

    /* The share that goes first and the coefficient order inside every
     * share-wise product are random (from the seed the last refresh drew).
     * Integer addition is exact here, so the order does not change z or r0. */
    first = prg_byte(&order) & 1u;

    /* ---- masked: z = y + c*s1, one share at a time ---- */
    z = y;
    for (unsigned sh = 0; sh < 2; sh++) {
        const polyvecl *s1 = &msk->s1hat[sh ^ first];
        for (unsigned j = 0; j < L; j++) {
            poly_pointwise_shuffled(&g_tmpl.vec[j], &cp, &s1->vec[j], &order);
        }
        polyvecl_invntt_tomont(&g_tmpl);
        polyvecl_add(&z, &z, &g_tmpl);
    }
    polyvecl_reduce(&z);
    if (polyvecl_chknorm(&z, GAMMA1 - BETA)) {
        goto rej;
    }

    /* ---- masked: r0 = w0 - c*s2, one share at a time ---- */
    for (unsigned sh = 0; sh < 2; sh++) {
        const polyveck *s2 = &msk->s2hat[sh ^ first];
        for (unsigned j = 0; j < K; j++) {
            poly_pointwise_shuffled(&g_tmpk.vec[j], &cp, &s2->vec[j], &order);
        }
        polyveck_invntt_tomont(&g_tmpk);
        polyveck_sub(&w0, &w0, &g_tmpk);
    }
    polyveck_reduce(&w0);
    if (polyveck_chknorm(&w0, GAMMA2 - BETA)) {
        goto rej;
    }

    /* ---- unmasked: hints from c*t0 ---- */
    polyveck_pointwise_poly_montgomery(&h, &cp, &msk->t0hat);
    polyveck_invntt_tomont(&h);
    polyveck_reduce(&h);
    if (polyveck_chknorm(&h, GAMMA2)) {
        goto rej;
    }

    polyveck_add(&w0, &w0, &h);
    n = polyveck_make_hint(&h, &w0, &w1);
    if (n > OMEGA) {
        goto rej;
    }

    pack_sig(sig, sig, &z, &h);
    *siglen = CRYPTO_BYTES;
    if (attempts) {
        *attempts = nonce;
    }

    /* y, the share products and c*t0 are secret-dependent: wipe them all. */
    zirah_memzero(&y, sizeof(y));
    zirah_memzero(&w0, sizeof(w0));
    zirah_memzero(&h, sizeof(h));
    zirah_memzero(&g_tmpl, sizeof(g_tmpl));
    zirah_memzero(&g_tmpk, sizeof(g_tmpk));
    zirah_memzero(rhoprime, sizeof(rhoprime));
    zirah_memzero(&state, sizeof(state));
    prg_wipe(&order);
    return 0;
}

static size_t make_pre(uint8_t pre[257], const uint8_t *ctx, size_t ctxlen)
{
    pre[0] = 0;
    pre[1] = (uint8_t)ctxlen;
    for (size_t i = 0; i < ctxlen; i++) {
        pre[2 + i] = ctx[i];
    }
    return 2 + ctxlen;
}

int zirah_sign_ctx(uint8_t *sig, size_t *siglen,
                   const uint8_t *m, size_t mlen,
                   const uint8_t *ctx, size_t ctxlen,
                   const uint8_t rnd[RNDBYTES],
                   const zirah_msk_t *msk, uint32_t *attempts)
{
    uint8_t pre[257];
    if (ctxlen > 255) {
        return -1;
    }
    size_t prelen = make_pre(pre, ctx, ctxlen);
    return zirah_sign_masked_internal(sig, siglen, m, mlen, pre, prelen, rnd, msk, attempts);
}

int zirah_sign_ref_ctx(uint8_t *sig, size_t *siglen,
                       const uint8_t *m, size_t mlen,
                       const uint8_t *ctx, size_t ctxlen,
                       const uint8_t rnd[RNDBYTES],
                       const uint8_t *sk)
{
    uint8_t pre[257];
    if (ctxlen > 255) {
        return -1;
    }
    size_t prelen = make_pre(pre, ctx, ctxlen);
    return crypto_sign_signature_internal(sig, siglen, m, mlen, pre, prelen, rnd, sk);
}

/* ------------------------------------------------------------------------ */
/* Key generation and verification without the reference code's large stack */
/* frames (about 58 KB each at ML-DSA-65). The public matrix uses the static */
/* g_mat; the remaining vectors live in caller-provided work memory, which   */
/* is CCM on the device. The arithmetic is the reference code step for step, */
/* so outputs are byte-identical (checked by tests/native).                  */
/* ------------------------------------------------------------------------ */

int zirah_keypair(uint8_t *pk, uint8_t *sk, zirah_work_t *w, zirah_rng_fn rng)
{
    uint8_t seedbuf[2 * SEEDBYTES + CRHBYTES];
    uint8_t tr[TRBYTES];
    const uint8_t *rho, *rhoprime, *key;
    polyvecl *s1 = &w->c;
    polyvecl *s1hat = &g_tmpl;
    polyveck *s2 = &w->a;
    polyveck *t1 = &g_tmpk;
    polyveck *t0 = &w->b;

    if (rng(seedbuf, SEEDBYTES) != 0) {
        zirah_memzero(seedbuf, sizeof(seedbuf));
        return -1;
    }
    seedbuf[SEEDBYTES + 0] = K;
    seedbuf[SEEDBYTES + 1] = L;
    shake256(seedbuf, 2 * SEEDBYTES + CRHBYTES, seedbuf, SEEDBYTES + 2);
    rho = seedbuf;
    rhoprime = rho + SEEDBYTES;
    key = rhoprime + CRHBYTES;

    polyvec_matrix_expand(g_mat, rho);
    polyvecl_uniform_eta(s1, rhoprime, 0);
    polyveck_uniform_eta(s2, rhoprime, L);

    *s1hat = *s1;
    polyvecl_ntt(s1hat);
    polyvec_matrix_pointwise_montgomery(t1, g_mat, s1hat);
    polyveck_reduce(t1);
    polyveck_invntt_tomont(t1);
    polyveck_add(t1, t1, s2);
    polyveck_caddq(t1);
    polyveck_power2round(t1, t0, t1);
    pack_pk(pk, rho, t1);

    shake256(tr, TRBYTES, pk, CRYPTO_PUBLICKEYBYTES);
    pack_sk(sk, rho, tr, key, t0, s1, s2);

    zirah_memzero(seedbuf, sizeof(seedbuf));
    zirah_memzero(w, sizeof(*w));
    zirah_memzero(s1hat, sizeof(*s1hat));
    zirah_memzero(t1, sizeof(*t1));
    return 0;
}

int zirah_verify_ctx(const uint8_t *sig, size_t siglen,
                     const uint8_t *m, size_t mlen,
                     const uint8_t *ctx, size_t ctxlen,
                     const uint8_t *pk, zirah_work_t *w)
{
    uint8_t pre[257];
    uint8_t buf[K * POLYW1_PACKEDBYTES];
    uint8_t rho[SEEDBYTES];
    uint8_t mu[CRHBYTES];
    uint8_t c[CTILDEBYTES];
    uint8_t c2[CTILDEBYTES];
    poly cp;
    polyvecl *z = &g_tmpl;
    polyveck *t1 = &w->a;
    polyveck *w1 = &g_tmpk;
    polyveck *h = &w->b;
    keccak_state state;

    if (ctxlen > 255 || siglen != CRYPTO_BYTES) {
        return -1;
    }
    size_t prelen = make_pre(pre, ctx, ctxlen);

    unpack_pk(rho, t1, pk);
    if (unpack_sig(c, z, h, sig)) {
        return -1;
    }
    if (polyvecl_chknorm(z, GAMMA1 - BETA)) {
        return -1;
    }

    /* mu = CRH(H(rho, t1), pre, msg) */
    shake256(mu, TRBYTES, pk, CRYPTO_PUBLICKEYBYTES);
    shake256_init(&state);
    shake256_absorb(&state, mu, TRBYTES);
    shake256_absorb(&state, pre, prelen);
    shake256_absorb(&state, m, mlen);
    shake256_finalize(&state);
    shake256_squeeze(mu, CRHBYTES, &state);

    /* A*z - c*2^d*t1, with A re-expanded from the public key (independent of
     * whatever the signer left in g_mat). */
    poly_challenge(&cp, c);
    polyvec_matrix_expand(g_mat, rho);

    polyvecl_ntt(z);
    polyvec_matrix_pointwise_montgomery(w1, g_mat, z);

    poly_ntt(&cp);
    polyveck_shiftl(t1);
    polyveck_ntt(t1);
    polyveck_pointwise_poly_montgomery(t1, &cp, t1);

    polyveck_sub(w1, w1, t1);
    polyveck_reduce(w1);
    polyveck_invntt_tomont(w1);

    polyveck_caddq(w1);
    polyveck_use_hint(w1, w1, h);
    polyveck_pack_w1(buf, w1);

    shake256_init(&state);
    shake256_absorb(&state, mu, CRHBYTES);
    shake256_absorb(&state, buf, K * POLYW1_PACKEDBYTES);
    shake256_finalize(&state);
    shake256_squeeze(c2, CTILDEBYTES, &state);
    return zirah_ct_equal(c, c2, CTILDEBYTES) ? 0 : -1;
}
