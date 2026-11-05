/*
 * Physical entropy source: the reverse-biased base-emitter junction of a
 * 2N3904 in avalanche breakdown, amplified by an LM358 and sampled by ADC1
 * on PA1 (source A). An optional second, identical circuit on the other half
 * of the LM358 is sampled by ADC2 on PB1 at the same instants (source B).
 *
 * Pipeline:
 *   raw 12-bit ADC sample -> symbol = low 8 bits
 *   -> SP 800-90B continuous health tests (Repetition Count + Adaptive
 *      Proportion), separately for each source
 *   -> SHAKE256 conditioning (oversampled by ZIRAH_OVERSAMPLE)
 *   -> 32-byte output blocks
 *
 * Entropy is credited to source A only. Source B, the ATECC608 and the
 * STM32's own generator are absorbed as well but credited zero, so none of
 * them can stand in for source A. What source B adds: a second physical
 * source in every block, and a correlation check between A and B at every
 * startup test, which catches a signal that reaches both (supply ripple, an
 * injected tone) and that the per-source health tests cannot see.
 *
 * Honest scope: the conditioning stage is SHAKE256-based and is not a
 * validated SP 800-90A DRBG; the min-entropy claim comes only from your own
 * SP 800-90B assessment of raw data.
 */
#ifndef ZIRAH_TRNG_H
#define ZIRAH_TRNG_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "trng_logic.h"

typedef enum {
    TRNG_NOT_STARTED = 0,
    TRNG_OK,
    TRNG_FAULT_BIAS,  /* boost / bias voltage outside the allowed window */
    TRNG_FAULT_STUCK, /* almost no variation: the noise source is dead   */
    TRNG_FAULT_RCT,   /* Repetition Count Test failure                   */
    TRNG_FAULT_APT,   /* Adaptive Proportion Test failure                */
    TRNG_FAULT_B,     /* source B failed a test, or vanished after boot  */
    TRNG_FAULT_CORR,  /* sources A and B move together                   */
    TRNG_FAULT_SOURCE, /* source A is off-centre or too smooth: unwired  */
} trng_state_t;

/* Which source a capture or analysis reads. */
typedef enum { TRNG_SRC_A = 0, TRNG_SRC_B = 1 } trng_src_t;

void adc_setup(void);
uint32_t adc_vdd_mv(void);
uint32_t adc_vbias_mv(void);        /* measures (about 65 ms) */
uint32_t adc_vbias_last_mv(void);   /* last measured value, no I/O */
int32_t adc_temp_c10(void);         /* die temperature, tenths of a degree C */

/* Bias check + 1024-symbol startup health test (+ correlation with B). */
trng_state_t trng_startup(void);
trng_state_t trng_state(void);
const char *trng_state_str(trng_state_t s);
void trng_health_get(trng_health_t *h);

/* Source B: wired at boot? Its health counters, standard deviation (x10)
 * and the A-B correlation (x1000) of the last startup test. */
bool trng_b_present(void);
void trng_health_get_b(trng_health_t *h);
int32_t trng_b_std_x10(void);
int32_t trng_corr_x1000_last(void);

/* Conditioned random bytes. Returns 0 on success, nonzero on a health fault. */
int trng_get(uint8_t *out, size_t n);

/* Additional input mixed into every block (credited zero entropy). */
#define TRNG_EXTRA_MAX 64u
void trng_set_extra_input(const uint8_t *x, size_t n);
void trng_clear_extra_input(void);

/* Idle monitor: sample one block at the default rate (both sources when B
 * is present, buf_b may then be filled) and feed it through the continuous
 * health tests. Returns false if the source is now in a fault. */
bool trng_monitor_block(uint16_t *buf, uint16_t *buf_b, size_t n);

/* Re-measure the boost voltage; outside the window the source faults. */
bool trng_check_bias(void);

/* Health-test failures since boot (not reset by a restart), both sources. */
uint32_t trng_failures_total(void);

/* Paced raw sampling (12-bit values) of one source, or of both at once. */
void trng_sample_block(uint16_t *buf, size_t n, uint32_t rate_hz, trng_src_t src);
void trng_sample_pair(uint16_t *a, uint16_t *b, size_t n, uint32_t rate_hz);

/* Feed raw samples of one source through its continuous health tests. */
void trng_health_feed(const uint16_t *buf, size_t n, trng_src_t src);

/* How many conditioner blocks since boot absorbed clock jitter, and
 * accelerometer readings; an operation that raised one had it mixed in. */
uint32_t trng_jitter_blocks(void);
uint32_t trng_accel_blocks(void);

#endif
