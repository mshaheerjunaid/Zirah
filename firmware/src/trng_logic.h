/*
 * The hardware-free part of the entropy source: the SP 800-90B continuous
 * health tests, block statistics, the cross-correlation between two noise
 * sources and the test that decides whether the second source is wired.
 *
 * Nothing here touches a peripheral, so tests/native/test_trng.c compiles
 * this file for the PC and drives it with synthetic noise.
 */
#ifndef ZIRAH_TRNG_LOGIC_H
#define ZIRAH_TRNG_LOGIC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t symbols;     /* symbols seen by the health tests since startup */
    uint16_t rct_cutoff;
    uint16_t apt_cutoff;
    uint32_t rct_max_run; /* longest run observed */
    uint32_t apt_max;     /* highest window count observed */
    uint32_t failures;
} trng_health_t;

/* Which test a symbol failed. */
typedef enum { HEALTH_PASS = 0, HEALTH_RCT, HEALTH_APT } health_result_t;

/* State of the two continuous tests for one noise source. */
typedef struct {
    trng_health_t pub;
    uint8_t rct_last;
    uint32_t rct_run;
    uint8_t apt_ref;
    uint32_t apt_count;
    uint32_t apt_pos;
} health_state_t;

#define TRNG_APT_WINDOW 512u

/* Cutoffs for a false-alarm probability of about 2^-20 per test, from the
 * SP 800-90B 4.4 formulas, for an assumed entropy of h_x100 / 100 bits. */
uint16_t trng_rct_cutoff(uint32_t h_x100);
uint16_t trng_apt_cutoff(uint32_t h_x100);

void health_init(health_state_t *h, uint32_t h_x100);
health_result_t health_symbol(health_state_t *h, uint8_t s);

typedef struct {
    uint32_t n;
    uint16_t min, max;
    float mean, stddev;
    uint32_t clipped;          /* samples at 0 or 4095 */
    uint32_t distinct12;       /* distinct 12-bit values */
    uint32_t distinct8;        /* distinct 8-bit symbols */
    float mcv_entropy;         /* SP 800-90B most-common-value estimate, bits/symbol */
    float autocorr1;           /* lag-1 autocorrelation of raw samples */
    uint32_t hist[16];
    uint16_t hist_lo, hist_step;
} trng_stats_t;

void trng_analyze(const uint16_t *buf, size_t n, trng_stats_t *st);

/* Mean and standard deviation (both x10, in 12-bit ADC steps) of n samples,
 * in integer arithmetic. */
void trng_mean_std_x10(const uint16_t *buf, size_t n, int32_t *mean_x10, int32_t *std_x10);

/* Pearson correlation of two equally long sample blocks, times 1000, rounded
 * towards zero. 0 when either block has no variation. */
int32_t trng_corr_x1000(const uint16_t *a, const uint16_t *b, size_t n);

/* Lag-1 autocorrelation of a block, times 1000 (0 without variation). */
int32_t trng_autocorr1_x1000(const uint16_t *buf, size_t n);

/* After the pin has been pulled to ground and released: is a live second
 * noise source on it? A wired source B sits at its 1.5 V reference with
 * fast, nearly white noise on top. An unwired pin stays near 0 V, or creeps
 * up slowly, or picks up mains hum; all of those are either low or smooth
 * from one sample to the next. So the test asks for all three: a mean above
 * about 0.75 V, at least the minimum noise of a live source, and a lag-1
 * autocorrelation below 0.9 (hum and drift sampled at 20 kHz sit at 0.99 or
 * more). A source B that is wired but silent is therefore not seen at
 * power-up, rather than blocking the device. */
#define TRNG_B_PRESENT_MIN_X10 10000 /* mean: 1000 ADC steps, about 0.75 V */
#define TRNG_B_ALIVE_STD_X10 80      /* the same minimum as source A      */
#define TRNG_B_MAX_AC1_X1000 900
bool trng_source_live(int32_t mean_x10, int32_t std_x10, int32_t ac1_x1000);

/* Is source A, sampled right after its pin was pulled to ground and
 * released, the amplified noise it was built to be? The amplifier centres
 * its output on half the supply, which the ADC reads as 2048, and its noise
 * is close to white at 20 kHz. A pin with no wire on it starts near 0 V and
 * creeps up, or hovers wherever stray coupling puts it, smoothly from one
 * sample to the next. So the test asks for a mean within 700 steps of 2048
 * and a lag-1 autocorrelation below 0.4, on top of the minimum noise. On the
 * first board measured the wired source gave a mean of 2036 and 0.06 in
 * magnitude; an unwired pin gave 1022 and 0.46, and on another evening
 * mains hum at 0.999. */
#define TRNG_A_CENTRE_X10 20480
#define TRNG_A_MAX_OFFSET_X10 7000
#define TRNG_A_MAX_AC1_X1000 400
bool trng_source_a_live(int32_t mean_x10, int32_t std_x10, int32_t ac1_x1000);

/* Is a run of LSI periods, counted in TIM5 ticks at 84 MHz, a free-running
 * oscillator with jitter? The LSI is specified between 17 and 47 kHz, so a
 * period lies between about 1790 and 4940 ticks; the bounds are wider. A
 * stopped or missing clock times out before this test, a period counted
 * from a second clock locked to the first would repeat one or two values,
 * so the test asks for every period inside the bounds, at least 4 distinct
 * values, and no single value in more than three quarters of the periods.
 * distinct, spread (largest minus smallest) and mode (the count of the most
 * common value) report what was measured. */
#define TRNG_JIT_MIN_TICKS 1500u
#define TRNG_JIT_MAX_TICKS 6000u
#define TRNG_JIT_MIN_DISTINCT 4u
bool trng_jitter_ok(const uint16_t *d, size_t n, uint32_t *distinct, uint32_t *spread, uint32_t *mode);

/* Is a run of accelerometer samples (x, y, z interleaved) sensor noise? A
 * board at rest still shows noise in the low bits of every axis; a chip
 * that does not answer reads one fixed value. So the test asks for at least
 * 8 samples, at least 2 distinct values on every axis, and not every value
 * equal to 0 or -1 (a silent bus). least reports the smallest number of
 * distinct values seen on any axis. */
#define TRNG_ACC_MIN_SAMPLES 8u
#define TRNG_ACC_MIN_DISTINCT 2u
bool trng_accel_ok(const int16_t *xyz, size_t samples, uint32_t *least);

#endif
