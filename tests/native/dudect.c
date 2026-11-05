/*
 * Timing-leak test in the style of dudect (Reparaz, Balasch, Verbauwhede,
 * "Dude, is my code constant time?", DATE 2017) for the firmware's
 * secret-handling kernels, built for the PC from the firmware sources.
 *
 * For each kernel the secret input comes from one of two classes, chosen at
 * random for every measurement: a fixed value, or a fresh random value. The
 * time of every call is read with the x86 time-stamp counter, and Welch's
 * t-test compares the two classes, on all measurements and again after
 * cutting off the slowest ones at several percentiles (interrupts and other
 * noise only ever make a call slower). If the running time depended on the
 * secret, |t| would grow with the number of measurements; dudect reads
 * |t| > 10 as a definite leak, and so does this test.
 *
 * A deliberately leaky comparison (it stops at the first differing byte) is
 * measured the same way and must be caught, so a quiet result cannot come
 * from a test too blunt to see anything.
 *
 * What this shows: the C code of these kernels, as compiled for this PC, has
 * no timing difference between the classes that this many measurements can
 * see, so it has no secret-dependent branch or memory access the compiler
 * kept. What it does not show: anything about the Cortex-M4 binary, whose
 * timing is a different matter (no cache, fixed-time multiplies, but a
 * variable-time divider; tests/run_tests.py checks the ARM objects for
 * division instructions separately).
 *
 * Exit status: 0 no leak and the control caught, 1 a leak or a missed
 * control, 77 no cycle counter on this machine (the runner reports SKIP).
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fips202.h"
#include "ntt.h"
#include "params.h"
#include "poly.h"
#include "reduce.h"
#include "secure.h"
#include "zirah_mldsa.h"

#if defined(__x86_64__) || defined(__i386__)
#include <x86intrin.h>
static inline uint64_t cycles(void)
{
    _mm_lfence();
    uint64_t t = __rdtsc();
    _mm_lfence();
    return t;
}
#define HAVE_CYCLES 1
#else
#define HAVE_CYCLES 0
static inline uint64_t cycles(void) { return 0; }
#endif

/* ------------------------------------------------------------------------ */
/* Randomness (deterministic, so a run can be repeated)                      */
/* ------------------------------------------------------------------------ */

static keccak_state g_rng;

static void rnd(void *out, size_t n)
{
    shake256_squeeze((uint8_t *)out, n, &g_rng);
}

static int rng_cb(uint8_t *out, size_t n)
{
    rnd(out, n);
    return 0;
}

/* Linked in by the reference sign.c, never called here. */
void randombytes(uint8_t *out, size_t outlen);
void randombytes(uint8_t *out, size_t outlen)
{
    (void)out;
    (void)outlen;
    abort();
}

static int32_t rnd_mod_q(void)
{
    for (;;) {
        uint32_t t;
        rnd(&t, sizeof(t));
        t &= 0x7FFFFF;
        if (t < Q) {
            return (int32_t)t;
        }
    }
}

/* ------------------------------------------------------------------------ */
/* Welch's t-test with percentile crops                                      */
/* ------------------------------------------------------------------------ */

#define CROPS 5
static const double k_crop_pct[CROPS] = {100.0, 99.0, 90.0, 75.0, 50.0};

typedef struct {
    double n[2], mean[2], m2[2];
} welch_t;

static void welch_add(welch_t *w, int cls, double x)
{
    w->n[cls] += 1;
    double d = x - w->mean[cls];
    w->mean[cls] += d / w->n[cls];
    w->m2[cls] += d * (x - w->mean[cls]);
}

static double welch(const welch_t *w)
{
    if (w->n[0] < 2 || w->n[1] < 2) {
        return 0;
    }
    double v0 = w->m2[0] / (w->n[0] - 1), v1 = w->m2[1] / (w->n[1] - 1);
    double den = sqrt(v0 / w->n[0] + v1 / w->n[1]);
    return den > 0 ? (w->mean[0] - w->mean[1]) / den : 0;
}

static int cmp_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return x < y ? -1 : x > y;
}

/* ------------------------------------------------------------------------ */
/* Kernels                                                                   */
/* ------------------------------------------------------------------------ */

typedef struct {
    const char *name;
    size_t in_bytes;               /* size of one prepared input            */
    unsigned batches, per_batch;   /* measurements = batches * per_batch    */
    void (*prepare)(uint8_t *in, int cls);
    void (*run)(const uint8_t *in);
    int control;                   /* 1: must be detected as leaking        */
} kernel_t;

static uint8_t g_fixed[4096];      /* the fixed-class secret for each kernel */
static volatile int g_sink;

/* zirah_ct_equal: secret buffer against a guess. Fixed class: the guess is
 * right; random class: a random guess. */
static void prep_cmp(uint8_t *in, int cls)
{
    memcpy(in, g_fixed, 32);
    if (cls == 0) {
        memcpy(in + 32, g_fixed, 32);
    } else {
        rnd(in + 32, 32);
    }
}

static void run_ct_equal(const uint8_t *in)
{
    g_sink = zirah_ct_equal(in, in + 32, 32);
}

static int leaky_equal(const uint8_t *a, const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (((const volatile uint8_t *)a)[i] != b[i]) {
            return 0;
        }
    }
    return 1;
}

static void run_leaky(const uint8_t *in)
{
    g_sink = leaky_equal(in, in + 32, 32);
}

/* One polynomial of secret coefficients in [0, q) (a key share) and, for the
 * product, a public random challenge in front of it. */
static void prep_poly(uint8_t *in, int cls)
{
    int32_t *c = (int32_t *)in, *s = c + N;
    for (unsigned i = 0; i < N; i++) {
        c[i] = rnd_mod_q();
    }
    if (cls == 0) {
        memcpy(s, g_fixed, N * sizeof(int32_t));
    } else {
        for (unsigned i = 0; i < N; i++) {
            s[i] = rnd_mod_q();
        }
    }
}

static poly g_out;

static void run_pointwise(const uint8_t *in)
{
    const poly *c = (const poly *)in, *s = c + 1;
    poly_pointwise_montgomery(&g_out, c, s);
}

static void run_ntt(const uint8_t *in)
{
    memcpy(&g_out, in + sizeof(poly), sizeof(poly));
    poly_ntt(&g_out);
}

static void run_invntt(const uint8_t *in)
{
    memcpy(&g_out, in + sizeof(poly), sizeof(poly));
    poly_invntt_tomont(&g_out);
}

static void run_reduce(const uint8_t *in)
{
    memcpy(&g_out, in + sizeof(poly), sizeof(poly));
    for (unsigned i = 0; i < N; i++) {
        g_out.coeffs[i] -= Q / 2; /* signed values, as reduce32 sees them */
    }
    poly_reduce(&g_out);
    poly_caddq(&g_out);
}

/* The whole mask refresh of a loaded key: fixed class uses one fixed secret
 * key, random class a fresh key each time (the masks are random in both). */
static uint8_t g_sk_fixed[CRYPTO_SECRETKEYBYTES];
static zirah_msk_t g_msk;
static zirah_work_t g_work;

static void keypair_from(uint8_t *sk, const uint8_t seed[32])
{
    static uint8_t pk[CRYPTO_PUBLICKEYBYTES];
    keccak_state saved = g_rng;
    shake256_init(&g_rng);
    shake256_absorb(&g_rng, seed, 32);
    shake256_finalize(&g_rng);
    (void)zirah_keypair(pk, sk, &g_work, rng_cb);
    g_rng = saved;
}

static void prep_refresh(uint8_t *in, int cls)
{
    if (cls == 0) {
        memcpy(in, g_sk_fixed, CRYPTO_SECRETKEYBYTES);
    } else {
        uint8_t seed[32];
        rnd(seed, sizeof(seed));
        keypair_from(in, seed);
    }
}

static void run_refresh(const uint8_t *in)
{
    (void)in;
    (void)zirah_msk_refresh(&g_msk, rng_cb);
}

/* The refresh needs the key loaded before timing, outside the timed call. */
static void load_for_refresh(const uint8_t *in)
{
    (void)zirah_msk_load(&g_msk, in, rng_cb);
}

static const kernel_t k_kernels[] = {
    {"zirah_ct_equal (32 bytes)", 64, 50, 20000, prep_cmp, run_ct_equal, 0},
    {"pointwise Montgomery product of a share", 2 * sizeof(poly), 50, 4000, prep_poly, run_pointwise, 0},
    {"forward NTT of a share", 2 * sizeof(poly), 50, 2000, prep_poly, run_ntt, 0},
    {"inverse NTT of a share", 2 * sizeof(poly), 50, 2000, prep_poly, run_invntt, 0},
    {"reduce32 and caddq of a polynomial", 2 * sizeof(poly), 50, 4000, prep_poly, run_reduce, 0},
    {"mask refresh of the whole key", CRYPTO_SECRETKEYBYTES, 20, 250, prep_refresh, run_refresh, 0},
    {"control: early-exit compare (must leak)", 64, 50, 20000, prep_cmp, run_leaky, 1},
};

/* ------------------------------------------------------------------------ */

static double measure(const kernel_t *k)
{
    uint8_t *in = malloc(k->in_bytes * k->per_batch);
    int *cls = malloc(sizeof(int) * k->per_batch);
    uint64_t *t = malloc(sizeof(uint64_t) * k->per_batch);
    uint64_t *sorted = malloc(sizeof(uint64_t) * k->per_batch);
    double cut[CROPS];
    welch_t w[CROPS];
    memset(w, 0, sizeof(w));
    if (!in || !cls || !t || !sorted) {
        fprintf(stderr, "out of memory\n");
        exit(2);
    }
    rnd(g_fixed, sizeof(g_fixed));
    for (unsigned i = 0; i < N; i++) { /* a valid fixed share for the poly kernels */
        ((int32_t *)g_fixed)[i] = rnd_mod_q();
    }

    for (unsigned b = 0; b <= k->batches; b++) {
        for (unsigned i = 0; i < k->per_batch; i++) {
            uint8_t c;
            rnd(&c, 1);
            cls[i] = c & 1;
            k->prepare(in + i * k->in_bytes, cls[i]);
        }
        for (unsigned i = 0; i < k->per_batch; i++) {
            const uint8_t *x = in + i * k->in_bytes;
            if (k->run == run_refresh) {
                load_for_refresh(x);
            }
            uint64_t t0 = cycles();
            k->run(x);
            uint64_t t1 = cycles();
            t[i] = t1 - t0;
        }
        if (b == 0) { /* the first batch only sets the crop thresholds */
            memcpy(sorted, t, sizeof(uint64_t) * k->per_batch);
            qsort(sorted, k->per_batch, sizeof(uint64_t), cmp_u64);
            for (unsigned j = 0; j < CROPS; j++) {
                size_t idx = (size_t)((k_crop_pct[j] / 100.0) * (k->per_batch - 1));
                cut[j] = j == 0 ? INFINITY : (double)sorted[idx];
            }
            continue;
        }
        for (unsigned i = 0; i < k->per_batch; i++) {
            for (unsigned j = 0; j < CROPS; j++) {
                if ((double)t[i] <= cut[j]) {
                    welch_add(&w[j], cls[i], (double)t[i]);
                }
            }
        }
    }
    double worst = 0;
    for (unsigned j = 0; j < CROPS; j++) {
        double v = fabs(welch(&w[j]));
        worst = v > worst ? v : worst;
    }
    free(in);
    free(cls);
    free(t);
    free(sorted);
    return worst;
}

int main(void)
{
    if (!HAVE_CYCLES) {
        printf("dudect: no cycle counter on this machine, skipped\n");
        return 77;
    }
    shake256_init(&g_rng);
    shake256_absorb(&g_rng, (const uint8_t *)"zirah-dudect-v1", 15);
    shake256_finalize(&g_rng);
    uint8_t seed[32];
    rnd(seed, sizeof(seed));
    keypair_from(g_sk_fixed, seed);

    const double limit = 10.0;
    unsigned leaks = 0, missed = 0;
    double worst_real = 0, control = 0;
    for (size_t i = 0; i < sizeof(k_kernels) / sizeof(k_kernels[0]); i++) {
        const kernel_t *k = &k_kernels[i];
        double t = measure(k);
        printf("  %-42s %7u measurements, max |t| %6.2f\n", k->name, k->batches * k->per_batch, t);
        if (k->control) {
            control = t;
            missed += t <= limit;
        } else {
            worst_real = t > worst_real ? t : worst_real;
            leaks += t > limit;
        }
    }
    if (missed) {
        printf("FAIL the leaky control was not detected (|t| %.1f), so this machine is too noisy to judge\n",
               control);
    }
    printf("dudect: %u of %zu kernels over |t| %.0f, largest %.2f; control caught at |t| %.1f\n", leaks,
           sizeof(k_kernels) / sizeof(k_kernels[0]) - 1, limit, worst_real, control);
    zirah_msk_wipe(&g_msk);
    return (leaks || missed) ? 1 : 0;
}
