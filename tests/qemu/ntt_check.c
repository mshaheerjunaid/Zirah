/*
 * Cortex-M4 check of the firmware's assembly NTT against the reference C NTT,
 * run on QEMU's mps2-an386 by tests/run_tests.py (suite "qemu").
 *
 * The C transform is linked in a second time under the names cref_ntt and
 * cref_invntt_tomont (objcopy renames the symbols of mldsa/ntt.c). For
 * thousands of random inputs from every range the signer feeds in, both
 * outputs must agree modulo q, and the inverse transform's output must stay
 * inside (-q, q), which the later caddq and reduce32 steps rely on. The run
 * also counts the instructions each version executes, from QEMU's virtual
 * clock (-icount): instructions, not cycles, so it shows the size of the
 * gain but is not a timing of the real chip.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "params.h"

void pqcrystals_dilithium_ntt(int32_t a[N]);
void pqcrystals_dilithium_invntt_tomont(int32_t a[N]);
void cref_ntt(int32_t a[N]);
void cref_invntt_tomont(int32_t a[N]);

static unsigned long g_checks, g_failed;

#define CHECK(cond, ...)                         \
    do {                                         \
        g_checks++;                              \
        if (!(cond)) {                           \
            if (g_failed++ < 10) {               \
                printf("FAIL line %d: ", __LINE__); \
                printf(__VA_ARGS__);             \
                printf("\n");                    \
            }                                    \
        }                                        \
    } while (0)

static uint64_t g_x = 0x9E3779B97F4A7C15ull;

static uint32_t rnd32(void)
{
    g_x ^= g_x << 13;
    g_x ^= g_x >> 7;
    g_x ^= g_x << 17;
    return (uint32_t)(g_x >> 16);
}

/* Uniform in [lo, hi]. */
static int32_t rnd_in(int32_t lo, int32_t hi)
{
    uint32_t span = (uint32_t)(hi - lo) + 1u;
    return lo + (int32_t)(rnd32() % span);
}

static int32_t mod_q(int32_t a)
{
    int32_t r = a % Q;
    return r < 0 ? r + Q : r;
}

/* The CMSDK TIMER0 of the MPS2 board runs on QEMU's virtual clock, which
 * -icount ties to the number of instructions executed. */
#define T0_CTRL (*(volatile uint32_t *)0x40000000u)
#define T0_VAL  (*(volatile uint32_t *)0x40000004u)
#define T0_RLD  (*(volatile uint32_t *)0x40000008u)

static void ticks_start(void)
{
    T0_CTRL = 0;
    T0_RLD = 0xFFFFFFFFu;
    T0_VAL = 0xFFFFFFFFu;
    T0_CTRL = 1u; /* enable, no interrupt */
}

static uint32_t ticks(void)
{
    return 0xFFFFFFFFu - T0_VAL;
}

/* A loop of exactly 2 instructions per pass converts ticks to instructions. */
static double insns_per_tick(void)
{
    uint32_t n = 400000;
    ticks_start();
    uint32_t t0 = ticks();
    __asm volatile("1: subs %0, %0, #1\n bne 1b" : "+r"(n) : : "cc");
    uint32_t t1 = ticks();
    return 800000.0 / (double)(t1 - t0);
}

typedef void (*xform_t)(int32_t *);

static double count(xform_t f, int32_t *buf, double ipt)
{
    enum { RUNS = 200 };
    static int32_t work[N];
    ticks_start();
    uint32_t t0 = ticks();
    for (unsigned r = 0; r < RUNS; r++) {
        memcpy(work, buf, sizeof(work));
        f(work);
    }
    uint32_t t1 = ticks();
    ticks_start();
    uint32_t c0 = ticks();
    for (unsigned r = 0; r < RUNS; r++) {
        memcpy(work, buf, sizeof(work));
    }
    uint32_t c1 = ticks();
    return ((double)(t1 - t0) - (double)(c1 - c0)) * ipt / RUNS;
}

int main(void)
{
    static int32_t in[N], a[N], c[N];
    struct {
        const char *name;
        int forward;
        int32_t lo, hi;
    } ranges[] = {
        {"forward, canonical [0, q)", 1, 0, Q - 1},
        {"forward, signed (-q, q)", 1, -(Q - 1), Q - 1},
        {"forward, y range", 1, -(1 << 19) + 1, 1 << 19},
        {"forward, eta range", 1, -4, 4},
        {"inverse, Montgomery products (-q, q)", 0, -(Q - 1), Q - 1},
        {"inverse, after reduce32", 0, -6283008, 6283008},
    };
    int32_t worst_inv = 0;
    unsigned exact = 0, total = 0;

    for (unsigned k = 0; k < sizeof(ranges) / sizeof(ranges[0]); k++) {
        for (unsigned t = 0; t < 500; t++) {
            for (unsigned i = 0; i < N; i++) {
                in[i] = rnd_in(ranges[k].lo, ranges[k].hi);
            }
            if (t == 0) { /* the extremes of the range */
                for (unsigned i = 0; i < N; i++) {
                    in[i] = (i & 1) ? ranges[k].hi : ranges[k].lo;
                }
            }
            memcpy(a, in, sizeof(a));
            memcpy(c, in, sizeof(c));
            if (ranges[k].forward) {
                pqcrystals_dilithium_ntt(a);
                cref_ntt(c);
            } else {
                pqcrystals_dilithium_invntt_tomont(a);
                cref_invntt_tomont(c);
            }
            unsigned bad = 0, same = 1;
            for (unsigned i = 0; i < N; i++) {
                bad += mod_q(a[i]) != mod_q(c[i]);
                same &= a[i] == c[i];
                if (!ranges[k].forward) {
                    int32_t m = a[i] < 0 ? -a[i] : a[i];
                    worst_inv = m > worst_inv ? m : worst_inv;
                }
            }
            exact += same;
            total++;
            CHECK(bad == 0, "%s, input %u: %u coefficients differ mod q", ranges[k].name, t, bad);
        }
    }
    CHECK(worst_inv < Q, "inverse NTT output %ld is outside (-q, q)", (long)worst_inv);
    printf("ntt: %lu checks, %lu failed; %u of %u outputs also equal as integers; "
           "largest inverse output %ld (q = %d)\n",
           g_checks, g_failed, exact, total, (long)worst_inv, Q);

    for (unsigned i = 0; i < N; i++) {
        in[i] = rnd_in(-(Q - 1), Q - 1);
    }
    double ipt = insns_per_tick();
    double fa = count(pqcrystals_dilithium_ntt, in, ipt), fc = count(cref_ntt, in, ipt);
    double ia = count(pqcrystals_dilithium_invntt_tomont, in, ipt), ic = count(cref_invntt_tomont, in, ipt);
    printf("instructions per transform (QEMU, not cycles): NTT %.0f asm vs %.0f C, "
           "inverse NTT %.0f asm vs %.0f C\n", fa, fc, ia, ic);
    printf("ntt summary: %lu checks, %lu failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
