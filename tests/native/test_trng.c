/*
 * Native (PC) tests for firmware/src/trng_logic.c, the hardware-free part of
 * the entropy source, driven with synthetic noise:
 *
 *   1. the SP 800-90B cutoffs match the formulas in the standard
 *   2. healthy noise passes both continuous tests for millions of symbols,
 *      a stuck source fails the Repetition Count Test exactly at its cutoff,
 *      and a source stuck on one value half of the time fails the Adaptive
 *      Proportion Test
 *   3. the integer mean and standard deviation agree with double precision
 *   4. the A-B correlation is near 0 for independent sources (never near
 *      the firmware's fault limit in thousands of 1024-sample tests), and
 *      far above the limit when both sources carry a common signal
 *   5. the test for a live second source accepts real noise at the 1.5 V
 *      reference and rejects a silent amplifier, a bare pin that creeps up
 *      or drifts, and a bare pin picking up mains hum
 *
 * Deterministic (fixed seeds). Exit status 0 means every check passed.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "trng_logic.h"

#ifndef ZIRAH_MAX_CORR_X1000
#define ZIRAH_MAX_CORR_X1000 300 /* the firmware default, zirah_config.h */
#endif

static unsigned long g_checks, g_failed;
static int32_t g_worst_independent;

#define CHECK(cond, ...)                                       \
    do {                                                       \
        g_checks++;                                            \
        if (!(cond)) {                                         \
            g_failed++;                                        \
            fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
            fprintf(stderr, __VA_ARGS__);                      \
            fputc('\n', stderr);                               \
        }                                                      \
    } while (0)

static uint64_t g_x = 0x2545F4914F6CDD1Dull;

static uint32_t rnd32(void)
{
    g_x ^= g_x << 13;
    g_x ^= g_x >> 7;
    g_x ^= g_x << 17;
    return (uint32_t)(g_x >> 16);
}

static double uniform(void)
{
    return (rnd32() + 0.5) / 4294967296.0;
}

static double gauss(void)
{
    return sqrt(-2.0 * log(uniform())) * cos(6.283185307179586 * uniform());
}

/* A 12-bit ADC reading of value v, clipped at the rails. */
static uint16_t adc12(double v)
{
    long x = lround(v);
    return (uint16_t)(x < 0 ? 0 : x > 4095 ? 4095 : x);
}

static void test_cutoffs(void)
{
    for (uint32_t h = 50; h <= 800; h += 10) {
        uint32_t want = 1u + (uint32_t)ceil(20.0 / (h / 100.0));
        CHECK(trng_rct_cutoff(h) == want, "RCT cutoff for H=%u.%02u: %u, want %u", h / 100, h % 100,
              trng_rct_cutoff(h), want);
    }
    /* The values the firmware uses at its default H = 1.00 and at H = 8. */
    CHECK(trng_rct_cutoff(100) == 21, "RCT cutoff at H=1");
    CHECK(trng_apt_cutoff(100) == 311, "APT cutoff at H=1");
    CHECK(trng_apt_cutoff(800) == 13, "APT cutoff at H=8");
}

static void test_health(void)
{
    health_state_t h;
    health_init(&h, 100);
    unsigned fails = 0;
    for (unsigned i = 0; i < 4000000; i++) {
        fails += health_symbol(&h, (uint8_t)(adc12(2048 + 210 * gauss()) & 0xFF)) != HEALTH_PASS;
    }
    CHECK(fails == 0, "healthy noise failed the continuous tests %u times", fails);
    CHECK(h.pub.symbols == 4000000, "symbol count");
    CHECK(h.pub.rct_max_run < h.pub.rct_cutoff && h.pub.apt_max < h.pub.apt_cutoff, "maxima below cutoffs");

    /* Stuck: the RCT fires on exactly the cutoff-th equal symbol. */
    health_init(&h, 100);
    unsigned first = 0;
    for (unsigned i = 1; i <= 40 && !first; i++) {
        if (health_symbol(&h, 0x5A) == HEALTH_RCT) {
            first = i;
        }
    }
    CHECK(first == h.pub.rct_cutoff, "stuck source caught after %u symbols, cutoff %u", first,
          h.pub.rct_cutoff);

    /* Two thirds of the symbols on one value, never more than twice in a row:
     * only the APT can see it (341 of a 512-symbol window, cutoff 311). */
    health_init(&h, 100);
    unsigned apt = 0, rct = 0;
    for (unsigned i = 0; i < 4096; i++) {
        uint8_t s = (i % 3u != 2u) ? 0x33 : (uint8_t)(rnd32() | 0x80);
        health_result_t r = health_symbol(&h, s);
        apt += r == HEALTH_APT;
        rct += r == HEALTH_RCT;
    }
    CHECK(apt > 0 && rct == 0, "biased source: APT fired %u times, RCT %u times", apt, rct);
}

static void test_mean_std(void)
{
    static uint16_t buf[4096];
    for (unsigned t = 0; t < 50; t++) {
        double sd = 20.0 + 30.0 * t;
        size_t n = 64 + (size_t)(rnd32() % 4033);
        double s = 0, s2 = 0;
        for (size_t i = 0; i < n; i++) {
            buf[i] = adc12(2048 + sd * gauss());
            s += buf[i];
            s2 += (double)buf[i] * buf[i];
        }
        double mean = s / n, var = s2 / n - mean * mean;
        int32_t m10, s10;
        trng_mean_std_x10(buf, n, &m10, &s10);
        CHECK(fabs(m10 - 10.0 * mean) <= 1.0, "mean x10 %d vs %.2f", m10, 10.0 * mean);
        CHECK(fabs(s10 - 10.0 * sqrt(var > 0 ? var : 0)) <= 1.0, "std x10 %d vs %.2f", s10,
              10.0 * sqrt(var));
    }
    for (size_t i = 0; i < 100; i++) {
        buf[i] = 1234;
    }
    int32_t m10, s10;
    trng_mean_std_x10(buf, 100, &m10, &s10);
    CHECK(m10 == 12340 && s10 == 0, "constant block: mean %d std %d", m10, s10);
}

static void test_correlation(void)
{
    static uint16_t a[1024], b[1024];
    int32_t worst = 0;
    for (unsigned t = 0; t < 5000; t++) {
        for (unsigned i = 0; i < 1024; i++) {
            a[i] = adc12(2048 + 210 * gauss());
            b[i] = adc12(1860 + 180 * gauss());
        }
        int32_t r = trng_corr_x1000(a, b, 1024);
        worst = abs(r) > worst ? abs(r) : worst;
    }
    /* Independent sources: about 31 thousandths of spread, so a few times
     * that at most over 5000 tests, and nowhere near the limit. */
    CHECK(worst < 160, "independent sources reached |r| = 0.%03d", worst);
    CHECK(worst < ZIRAH_MAX_CORR_X1000 / 2, "independent sources came near the fault limit");
    g_worst_independent = worst;

    /* A common tone (supply ripple, an injected signal) as strong as the noise. */
    for (unsigned i = 0; i < 1024; i++) {
        double tone = 210 * sin(6.283185307179586 * i / 37.0);
        a[i] = adc12(2048 + 210 * gauss() + tone);
        b[i] = adc12(1860 + 180 * gauss() + tone);
    }
    int32_t r = trng_corr_x1000(a, b, 1024);
    CHECK(r > ZIRAH_MAX_CORR_X1000, "common tone not caught: r = %d", r);

    /* A common signal at 30 % of the variance sits at the limit; 45 % is above it. */
    for (unsigned i = 0; i < 1024; i++) {
        double c = gauss();
        a[i] = adc12(2048 + 200 * (sqrt(0.45) * c + sqrt(0.55) * gauss()));
        b[i] = adc12(2048 + 200 * (sqrt(0.45) * c + sqrt(0.55) * gauss()));
    }
    r = trng_corr_x1000(a, b, 1024);
    CHECK(r > ZIRAH_MAX_CORR_X1000, "45 %% common variance not caught: r = %d", r);

    /* Exact cases. */
    for (unsigned i = 0; i < 1024; i++) {
        a[i] = adc12(2048 + 210 * gauss());
        b[i] = (uint16_t)(4095 - a[i]);
    }
    CHECK(trng_corr_x1000(a, a, 1024) == 1000, "r(a, a)");
    CHECK(trng_corr_x1000(a, b, 1024) == -1000, "r(a, -a)");
    for (unsigned i = 0; i < 1024; i++) {
        b[i] = 77;
    }
    CHECK(trng_corr_x1000(a, b, 1024) == 0, "constant block gives 0");
}

static int live(const uint16_t *buf, size_t n)
{
    int32_t m10, s10;
    trng_mean_std_x10(buf, n, &m10, &s10);
    return trng_source_live(m10, s10, trng_autocorr1_x1000(buf, n));
}

static int live_a(const uint16_t *buf, size_t n)
{
    int32_t m10, s10;
    trng_mean_std_x10(buf, n, &m10, &s10);
    return trng_source_a_live(m10, s10, trng_autocorr1_x1000(buf, n));
}

static void test_live_a(void)
{
    static uint16_t buf[1024];
    const size_t n = sizeof(buf) / sizeof(buf[0]);
    /* The first bench board: centred near 2036 with an STD of about 28. */
    for (size_t i = 0; i < n; i++) buf[i] = adc12(2036 + 28 * gauss());
    CHECK(live_a(buf, n), "the measured live source A was refused");
    /* A higher gain narrows the band: lag-1 correlation about 0.25 still passes. */
    double x = 0;
    for (size_t i = 0; i < n; i++) {
        x = 0.25 * x + 0.97 * gauss();
        buf[i] = adc12(2048 + 120 * x);
    }
    CHECK(live_a(buf, n), "a band-limited live source A was refused (ac1 %d)", trng_autocorr1_x1000(buf, n));
    /* The edges of the centre window. */
    for (size_t i = 0; i < n; i++) buf[i] = adc12(1360 + 40 * gauss());
    CHECK(live_a(buf, n), "a source 690 steps below the centre was refused");
    for (size_t i = 0; i < n; i++) buf[i] = adc12(1330 + 40 * gauss());
    CHECK(!live_a(buf, n), "a source 720 steps below the centre was accepted");
    /* The unwired PA4 measured on the DevEBox: mean 1022, STD 15.5, lag-1 0.46. */
    x = 0;
    for (size_t i = 0; i < n; i++) {
        x = 0.46 * x + 0.89 * gauss();
        buf[i] = adc12(1022 + 15.5 * x);
    }
    CHECK(!live_a(buf, n), "the unwired DevEBox pin was taken for source A");
    /* The same smoothness at the right centre still fails, on the correlation alone. */
    x = 0;
    for (size_t i = 0; i < n; i++) {
        x = 0.6 * x + 0.8 * gauss();
        buf[i] = adc12(2048 + 30 * x);
    }
    CHECK(!live_a(buf, n), "a smooth signal at the centre was taken for source A (ac1 %d)",
          trng_autocorr1_x1000(buf, n));
    /* Mains hum around the centre, a pin creeping up after its discharge, a grounded pin. */
    for (size_t i = 0; i < n; i++) buf[i] = adc12(2048 + 300 * sin(6.283185307179586 * 50.0 * i / 20000.0) + 3 * gauss());
    CHECK(!live_a(buf, n), "hum around the centre was taken for source A");
    for (size_t i = 0; i < n; i++) buf[i] = adc12(3 + i * 0.5 + 2 * gauss());
    CHECK(!live_a(buf, n), "a creeping bare pin was taken for source A");
    for (size_t i = 0; i < n; i++) buf[i] = adc12(2 * gauss() + 2);
    CHECK(!live_a(buf, n), "a grounded pin was taken for source A");
}

static void test_live(void)
{
    static uint16_t buf[256];
    const size_t n = sizeof(buf) / sizeof(buf[0]);
    /* An amplifier at its 1.5 V reference (about 1860 of 4095 at 3.0 V) with live noise. */
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(1860 + 210 * gauss());
    }
    CHECK(live(buf, n), "a live source B was not seen");
    /* Live noise that is partly smoothed by the amplifier (lag-1 correlation about 0.6). */
    double x = 0;
    for (size_t i = 0; i < n; i++) {
        x = 0.6 * x + 0.8 * gauss();
        buf[i] = adc12(1860 + 210 * x);
    }
    CHECK(live(buf, n), "a band-limited live source B was not seen (ac1 %d)", trng_autocorr1_x1000(buf, n));
    /* None of these may pass for a source: a wired but silent amplifier, a pin
     * pulled to ground that creeps up, a bare pin that drifts a long way, and
     * a bare pin picking up 50 Hz mains hum. */
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(1860 + 1.5 * gauss());
    }
    CHECK(!live(buf, n), "a silent amplifier was taken for a live source");
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(3 + i * 0.5 + 2 * gauss());
    }
    CHECK(!live(buf, n), "a creeping bare pin was taken for a live source");
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(10.0 * i + 2 * gauss());
    }
    CHECK(!live(buf, n), "a strongly drifting bare pin was taken for a live source");
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(1500 + 900 * sin(6.283185307179586 * 50.0 * i / 20000.0) + 3 * gauss());
    }
    CHECK(!live(buf, n), "a bare pin with mains hum was taken for a live source (ac1 %d)",
          trng_autocorr1_x1000(buf, n));
    for (size_t i = 0; i < n; i++) {
        buf[i] = adc12(2 * gauss() + 2);
    }
    CHECK(!live(buf, n), "a grounded pin was taken for a live source");
}

static void test_jitter(void)
{
    static uint16_t d[256];
    uint32_t distinct, spread, mode;
    /* An RC oscillator near 32 kHz with a few ticks of jitter. */
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(2625 + (int)(rnd32() % 9) - 4);
    CHECK(trng_jitter_ok(d, 256, &distinct, &spread, &mode), "a jittery LSI was refused");
    CHECK(distinct == 9 && spread == 8, "measured %u distinct, spread %u", (unsigned)distinct, (unsigned)spread);
    /* Jitter of only one tick still passes if four values appear. */
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(2625 + (int)(i % 4));
    CHECK(trng_jitter_ok(d, 256, NULL, NULL, NULL), "four evenly used values were refused");
    /* A locked clock: one value, or two in alternation. */
    for (size_t i = 0; i < 256; i++) d[i] = 2625;
    CHECK(!trng_jitter_ok(d, 256, NULL, NULL, &mode), "a fixed period was taken for jitter");
    CHECK(mode == 256, "the mode of a fixed period was %u", (unsigned)mode);
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(2625 + (i & 1));
    CHECK(!trng_jitter_ok(d, 256, NULL, NULL, NULL), "two alternating periods were taken for jitter");
    /* One value in more than three quarters of the periods. */
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(i % 5 == 0 ? 2620 + (i % 7) : 2625);
    CHECK(!trng_jitter_ok(d, 256, NULL, NULL, NULL), "a mostly fixed period was taken for jitter");
    /* Periods outside the LSI's range: a missed edge, a wrong clock. */
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(2625 + (int)(rnd32() % 9) - 4);
    d[100] = 5250;
    CHECK(trng_jitter_ok(d, 256, NULL, NULL, NULL), "a double period inside the bounds was refused");
    d[100] = 6001;
    CHECK(!trng_jitter_ok(d, 256, NULL, NULL, NULL), "a period above the bounds was accepted");
    d[100] = 1499;
    CHECK(!trng_jitter_ok(d, 256, NULL, NULL, NULL), "a period below the bounds was accepted");
    /* A spread wider than the table is varied by definition. */
    for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(1600 + (rnd32() % 4000));
    CHECK(trng_jitter_ok(d, 256, &distinct, NULL, NULL) && distinct == 256, "a wide spread was refused");
    CHECK(!trng_jitter_ok(d, 8, NULL, NULL, NULL), "eight periods were accepted");
    for (int t = 0; t < 2000; t++) {
        for (size_t i = 0; i < 256; i++) d[i] = (uint16_t)(2625 + (int)(rnd32() % 5) - 2);
        CHECK(trng_jitter_ok(d, 256, NULL, NULL, NULL), "a jittery run was refused (trial %d)", t);
    }
}

static void test_accel(void)
{
    static int16_t xyz[16 * 3];
    uint32_t least;
    /* At rest: gravity on z, a few counts of noise on every axis. */
    for (size_t i = 0; i < 16; i++) {
        xyz[3 * i] = (int16_t)(40 + (int)(rnd32() % 7) - 3);
        xyz[3 * i + 1] = (int16_t)(-25 + (int)(rnd32() % 7) - 3);
        xyz[3 * i + 2] = (int16_t)(16384 + (int)(rnd32() % 7) - 3);
    }
    CHECK(trng_accel_ok(xyz, 16, &least), "noisy readings were refused");
    CHECK(least >= 2, "least distinct was %u", (unsigned)least);
    /* One axis frozen. */
    for (size_t i = 0; i < 16; i++) xyz[3 * i + 1] = -25;
    CHECK(!trng_accel_ok(xyz, 16, &least), "a frozen axis was accepted");
    CHECK(least == 1, "a frozen axis showed %u values", (unsigned)least);
    /* A silent bus: all zeros, or all ones. */
    memset(xyz, 0, sizeof(xyz));
    CHECK(!trng_accel_ok(xyz, 16, NULL), "all-zero readings were accepted");
    memset(xyz, 0xFF, sizeof(xyz));
    CHECK(!trng_accel_ok(xyz, 16, NULL), "all-ones readings were accepted");
    for (size_t i = 0; i < 16; i++) {
        xyz[3 * i] = (int16_t)(i & 1 ? 0 : -1);
        xyz[3 * i + 1] = (int16_t)(i & 1 ? -1 : 0);
        xyz[3 * i + 2] = (int16_t)(i & 2 ? 0 : -1);
    }
    CHECK(!trng_accel_ok(xyz, 16, NULL), "a bus toggling between 0 and -1 was accepted");
    for (size_t i = 0; i < 16; i++) {
        xyz[3 * i] = (int16_t)(i % 3);
        xyz[3 * i + 1] = (int16_t)(5 + i % 2);
        xyz[3 * i + 2] = (int16_t)(60 + i % 2);
    }
    CHECK(trng_accel_ok(xyz, 16, NULL), "8-bit readings with one count of noise were refused");
    CHECK(!trng_accel_ok(xyz, 4, NULL), "four readings were accepted");
}

static void test_analyze(void)
{
    static uint16_t buf[4096];
    for (unsigned i = 0; i < 4096; i++) {
        buf[i] = adc12(2048 + 210 * gauss());
    }
    trng_stats_t st;
    trng_analyze(buf, 4096, &st);
    CHECK(st.n == 4096 && st.min < st.max && st.clipped == 0, "basic statistics");
    CHECK(fabs(st.stddev - 210) < 15 && fabs(st.mean - 2048) < 15, "mean %.1f std %.1f", st.mean,
          st.stddev);
    CHECK(fabs(st.autocorr1) < 0.08f, "white noise has no lag-1 correlation (%.3f)", st.autocorr1);
    CHECK(st.mcv_entropy > 6.0f, "MCV estimate %.2f for near-uniform 8-bit symbols", st.mcv_entropy);
    uint32_t total = 0;
    for (int i = 0; i < 16; i++) {
        total += st.hist[i];
    }
    CHECK(total == 4096, "histogram holds every sample");
}

int main(void)
{
    test_cutoffs();
    test_health();
    test_mean_std();
    test_correlation();
    test_live();
    test_live_a();
    test_jitter();
    test_accel();
    test_analyze();
    printf("entropy logic tests: %lu checks, %lu failed; independent sources reached at most "
           "|r| = 0.%03d in 5000 tests (fault limit 0.%03d)\n",
           g_checks, g_failed, g_worst_independent, ZIRAH_MAX_CORR_X1000);
    return g_failed ? 1 : 0;
}
