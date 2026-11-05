#include "board_pins.h"
#include "trng.h"
#include "board.h"
#include "zirah_config.h"
#include "fips202.h"
#include "secure.h"
#include "jitter.h"
#include "accel.h"

#include <string.h>

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/stm32/adc.h>

#define CH_NOISE   NOISE_CH   /* PA1 (Discovery) or PA4 (DevEBox), ADC1: source A */
#define CH_NOISE_B 9   /* PB1, ADC2: source B (optional)      */
#define CH_BIAS    8   /* PB0, ADC1                           */
#define CH_TEMP    16  /* internal temperature sensor, ADC1   */
#define CH_VREFINT 17

/* Factory calibration in system memory (STM32F405/407 datasheet): VREFINT
 * and the temperature sensor, raw ADC values taken at VDDA = 3.3 V. */
#define VREFINT_CAL (*(volatile uint16_t *)0x1FFF7A2AUL)
#define TS_CAL1     (*(volatile uint16_t *)0x1FFF7A2CUL) /*  30 C */
#define TS_CAL2     (*(volatile uint16_t *)0x1FFF7A2EUL) /* 110 C */

#if (ZIRAH_H_ASSUMED_X100 < 50) || (ZIRAH_H_ASSUMED_X100 > 800)
#error "ZIRAH_H_ASSUMED_X100 must be between 50 (0.5 bit) and 800 (8 bits) per 8-bit symbol"
#endif
#if (ZIRAH_MAX_CORR_X1000 < 100) || (ZIRAH_MAX_CORR_X1000 > 900)
#error "ZIRAH_MAX_CORR_X1000 must be between 100 and 900 (a correlation of 0.1 to 0.9)"
#endif

/* Minimum standard deviation (in 12-bit LSB, x10) for a source to count as alive. */
#ifndef ZIRAH_MIN_STD_X10
#define ZIRAH_MIN_STD_X10 80
#endif

/* ------------------------------------------------------------------------ */
/* ADC                                                                       */
/* ------------------------------------------------------------------------ */

static void adc_common(uint32_t adc)
{
    adc_power_off(adc);
    adc_disable_scan_mode(adc);
    adc_set_single_conversion_mode(adc);
    adc_disable_external_trigger_regular(adc);
    adc_set_right_aligned(adc);
    adc_set_resolution(adc, ADC_CR1_RES_12BIT);
}

void adc_setup(void)
{
    rcc_periph_clock_enable(RCC_ADC1);
    rcc_periph_clock_enable(RCC_ADC2);
    gpio_mode_setup(NOISE_PORT, GPIO_MODE_ANALOG, GPIO_PUPD_NONE, NOISE_PIN);
    gpio_mode_setup(GPIOB, GPIO_MODE_ANALOG, GPIO_PUPD_NONE, GPIO0 | GPIO1);

    adc_set_clk_prescale(ADC_CCR_ADCPRE_BY4);     /* 84 MHz / 4 = 21 MHz, both ADCs */
    adc_common(ADC1);
    adc_common(ADC2);
    adc_set_sample_time(ADC1, CH_NOISE, ADC_SMPR_SMP_15CYC);
    adc_set_sample_time(ADC2, CH_NOISE_B, ADC_SMPR_SMP_15CYC);
    adc_set_sample_time(ADC1, CH_BIAS, ADC_SMPR_SMP_480CYC);
    adc_set_sample_time(ADC1, CH_TEMP, ADC_SMPR_SMP_480CYC);
    adc_set_sample_time(ADC1, CH_VREFINT, ADC_SMPR_SMP_480CYC);
    adc_enable_temperature_sensor();              /* also enables VREFINT */
    adc_power_on(ADC1);
    adc_power_on(ADC2);
    delay_us(20);
}

static uint16_t adc_read_channel(uint8_t ch)
{
    uint8_t seq[1] = {ch};
    adc_set_regular_sequence(ADC1, 1, seq);
    adc_start_conversion_regular(ADC1);
    while (!adc_eoc(ADC1)) {
    }
    return (uint16_t)adc_read_regular(ADC1);
}

uint32_t adc_vdd_mv(void)
{
    uint32_t acc = 0;
    for (int i = 0; i < 32; i++) {
        acc += adc_read_channel(CH_VREFINT);
    }
    uint32_t raw = acc / 32u;
    if (raw == 0) {
        return 0;
    }
    return (3300UL * (uint32_t)VREFINT_CAL) / raw;
}

int32_t adc_temp_c10(void)
{
    uint32_t vdd = adc_vdd_mv();
    uint32_t acc = 0;
    for (int i = 0; i < 16; i++) {
        acc += adc_read_channel(CH_TEMP);
    }
    if (vdd == 0) {
        return 0;
    }
    /* The calibration points were taken at VDDA = 3.3 V: rescale to that. */
    int32_t raw33 = (int32_t)((acc * vdd + 16u * 3300u / 2u) / (16u * 3300u));
    int32_t c1 = TS_CAL1, c2 = TS_CAL2;
    if (c1 > 0 && c1 < 4095 && c2 > c1 + 50 && c2 < 4095) {
        return 300 + (raw33 - c1) * 800 / (c2 - c1);
    }
    /* No usable calibration: datasheet typicals, 0.76 V at 25 C, 2.5 mV/C. */
    int32_t mv = raw33 * 3300 / 4095;
    return 250 + (mv - 760) * 4;
}

/*
 * Briefly pull an analog pin to ground through the internal pull-down, then
 * hand it back to the ADC. A pin that is really wired to the circuit recovers
 * to its true voltage within the settle time; a floating (unwired) pin stays
 * near 0 V. This stops an unbuilt or disconnected board from reading random
 * floating-pin values that could look like a valid bias or a live source.
 */
static void pin_discharge(uint32_t port, uint16_t pin, uint32_t settle_ms)
{
    gpio_mode_setup(port, GPIO_MODE_INPUT, GPIO_PUPD_PULLDOWN, pin);
    delay_ms(5);
    gpio_mode_setup(port, GPIO_MODE_ANALOG, GPIO_PUPD_NONE, pin);
    delay_ms(settle_ms);
}

static uint32_t g_last_vbias_mv;

uint32_t adc_vbias_last_mv(void)
{
    return g_last_vbias_mv;
}

uint32_t adc_vbias_mv(void)
{
    /* Monitor node time constant is about 9 ms (91 kohm x 100 nF). */
    pin_discharge(GPIOB, GPIO0, 60);
    uint32_t vdd = adc_vdd_mv();
    uint32_t acc = 0;
    for (int i = 0; i < 64; i++) {
        acc += adc_read_channel(CH_BIAS);
    }
    uint32_t pin_mv = (uint32_t)(((uint64_t)acc * vdd) / (64ULL * 4095ULL));
    g_last_vbias_mv = (uint32_t)(((uint64_t)pin_mv * (ZIRAH_DIV_TOP_OHMS + ZIRAH_DIV_BOTTOM_OHMS)) /
                                 ZIRAH_DIV_BOTTOM_OHMS);
    return g_last_vbias_mv;
}

/* ------------------------------------------------------------------------ */
/* Health state of both sources                                              */
/* ------------------------------------------------------------------------ */

static trng_state_t g_state = TRNG_NOT_STARTED;
static health_state_t g_ha, g_hb;
static uint32_t g_failures_total;

static bool g_b_decided; /* the first startup test after boot decides       */
static bool g_b_present; /* source B was wired at boot and must stay healthy */
static int32_t g_b_std_x10;
static int32_t g_corr_last = 0;

static void health_reset(void)
{
    health_init(&g_ha, ZIRAH_H_ASSUMED_X100);
    health_init(&g_hb, ZIRAH_H_ASSUMED_X100);
}

static void feed_a(uint8_t s)
{
    health_result_t r = health_symbol(&g_ha, s);
    if (r != HEALTH_PASS) {
        g_failures_total++;
        g_state = r == HEALTH_RCT ? TRNG_FAULT_RCT : TRNG_FAULT_APT;
    }
}

static void feed_b(uint8_t s)
{
    if (health_symbol(&g_hb, s) != HEALTH_PASS) {
        g_failures_total++;
        if (g_state == TRNG_OK) {
            g_state = TRNG_FAULT_B;
        }
    }
}

uint32_t trng_failures_total(void)
{
    return g_failures_total;
}

void trng_health_feed(const uint16_t *buf, size_t n, trng_src_t src)
{
    for (size_t i = 0; i < n; i++) {
        uint8_t s = (uint8_t)(buf[i] & 0xFF);
        if (src == TRNG_SRC_B) {
            feed_b(s);
        } else {
            feed_a(s);
        }
    }
}

void trng_health_get(trng_health_t *h)
{
    *h = g_ha.pub;
}

void trng_health_get_b(trng_health_t *h)
{
    *h = g_hb.pub;
}

bool trng_b_present(void)
{
    return g_b_present;
}

int32_t trng_b_std_x10(void)
{
    return g_b_std_x10;
}

int32_t trng_corr_x1000_last(void)
{
    return g_corr_last;
}

bool trng_check_bias(void)
{
    uint32_t vb = adc_vbias_mv();
    if (vb < ZIRAH_VBIAS_MIN_MV || vb > ZIRAH_VBIAS_MAX_MV) {
        g_state = TRNG_FAULT_BIAS;
        return false;
    }
    return true;
}

/* ------------------------------------------------------------------------ */
/* Sampling                                                                  */
/* ------------------------------------------------------------------------ */

static uint32_t period_for(uint32_t rate_hz)
{
    if (rate_hz == 0) {
        rate_hz = ZIRAH_SAMPLE_RATE_HZ;
    }
    return ZIRAH_CPU_HZ / rate_hz;
}

void trng_sample_block(uint16_t *buf, size_t n, uint32_t rate_hz, trng_src_t src)
{
    uint32_t period = period_for(rate_hz);
    uint32_t adc = src == TRNG_SRC_B ? ADC2 : ADC1;
    uint8_t seq[1] = {src == TRNG_SRC_B ? CH_NOISE_B : CH_NOISE};
    adc_set_regular_sequence(adc, 1, seq);
    uint32_t next = cycles_now() + period;
    for (size_t i = 0; i < n; i++) {
        while ((int32_t)(cycles_now() - next) < 0) {
        }
        next += period;
        adc_start_conversion_regular(adc);
        while (!adc_eoc(adc)) {
        }
        buf[i] = (uint16_t)adc_read_regular(adc);
    }
}

/* Both sources at the same instants: ADC2 and ADC1 are started by two
 * register writes a few CPU cycles apart (libopencm3's start function would
 * wait for the first ADC to begin before starting the second), so their
 * 0.7 us sampling windows almost completely overlap. */
void trng_sample_pair(uint16_t *a, uint16_t *b, size_t n, uint32_t rate_hz)
{
    uint32_t period = period_for(rate_hz);
    uint8_t sa[1] = {CH_NOISE}, sb[1] = {CH_NOISE_B};
    adc_set_regular_sequence(ADC1, 1, sa);
    adc_set_regular_sequence(ADC2, 1, sb);
    uint32_t next = cycles_now() + period;
    for (size_t i = 0; i < n; i++) {
        while ((int32_t)(cycles_now() - next) < 0) {
        }
        next += period;
        ADC_CR2(ADC2) |= ADC_CR2_SWSTART;
        ADC_CR2(ADC1) |= ADC_CR2_SWSTART;
        while (!adc_eoc(ADC1)) {
        }
        while (!adc_eoc(ADC2)) {
        }
        a[i] = (uint16_t)adc_read_regular(ADC1);
        b[i] = (uint16_t)adc_read_regular(ADC2);
    }
}

bool trng_monitor_block(uint16_t *buf, uint16_t *buf_b, size_t n)
{
    if (g_b_present && buf_b) {
        trng_sample_pair(buf, buf_b, n, ZIRAH_SAMPLE_RATE_HZ);
        trng_health_feed(buf, n, TRNG_SRC_A);
        trng_health_feed(buf_b, n, TRNG_SRC_B);
    } else {
        trng_sample_block(buf, n, ZIRAH_SAMPLE_RATE_HZ, TRNG_SRC_A);
        trng_health_feed(buf, n, TRNG_SRC_A);
    }
    return g_state == TRNG_OK;
}

/* ------------------------------------------------------------------------ */
/* Startup                                                                   */
/* ------------------------------------------------------------------------ */

static uint16_t g_block[1024], g_block_b[1024];

/* Is a live source B on PB1? Pull the pin to ground, let it go, and see
 * whether an amplifier pulls it back up to its reference with fast noise on
 * top (trng_source_live() explains why a bare pin cannot pass). */
static bool probe_b(void)
{
    pin_discharge(GPIOB, GPIO1, 2);
    trng_sample_block(g_block_b, 256, ZIRAH_SAMPLE_RATE_HZ, TRNG_SRC_B);
    int32_t mean_x10, std_x10;
    trng_mean_std_x10(g_block_b, 256, &mean_x10, &std_x10);
    return trng_source_live(mean_x10, std_x10, trng_autocorr1_x1000(g_block_b, 256));
}

trng_state_t trng_startup(void)
{
    health_reset();

#if !defined(ZIRAH_SKIP_VBIAS_CHECK)
    uint32_t vb = adc_vbias_mv();
    if (vb < ZIRAH_VBIAS_MIN_MV || vb > ZIRAH_VBIAS_MAX_MV) {
        g_state = TRNG_FAULT_BIAS;
        return g_state;
    }
#endif

    g_state = TRNG_OK;
    bool live = probe_b();
    if (!g_b_decided) {
        g_b_decided = true;
        g_b_present = live;
    } else if (g_b_present && !live) {
        g_b_std_x10 = 0;
        g_state = TRNG_FAULT_B; /* live at boot, gone now: fail closed */
        return g_state;
    }

    pin_discharge(NOISE_PORT, NOISE_PIN, 5);
    int32_t mean_x10, std_x10;
    if (g_b_present) {
        trng_sample_pair(g_block, g_block_b, 1024, ZIRAH_SAMPLE_RATE_HZ);
        trng_health_feed(g_block, 1024, TRNG_SRC_A);
        trng_health_feed(g_block_b, 1024, TRNG_SRC_B);
        trng_mean_std_x10(g_block_b, 1024, &mean_x10, &g_b_std_x10);
        g_corr_last = trng_corr_x1000(g_block, g_block_b, 1024);
    } else {
        trng_sample_block(g_block, 1024, ZIRAH_SAMPLE_RATE_HZ, TRNG_SRC_A);
        trng_health_feed(g_block, 1024, TRNG_SRC_A);
    }
    if (g_state != TRNG_OK) {
        return g_state;
    }
    trng_mean_std_x10(g_block, 1024, &mean_x10, &std_x10);
    if (std_x10 < ZIRAH_MIN_STD_X10) {
        g_state = TRNG_FAULT_STUCK;
    } else if (!trng_source_a_live(mean_x10, std_x10, trng_autocorr1_x1000(g_block, 1024))) {
        g_state = TRNG_FAULT_SOURCE;
    } else if (g_b_present && g_b_std_x10 < ZIRAH_MIN_STD_X10) {
        g_state = TRNG_FAULT_B;
    } else if (g_b_present && (g_corr_last > ZIRAH_MAX_CORR_X1000 ||
                               g_corr_last < -ZIRAH_MAX_CORR_X1000)) {
        g_state = TRNG_FAULT_CORR;
    }
    return g_state;
}

trng_state_t trng_state(void)
{
    return g_state;
}

const char *trng_state_str(trng_state_t s)
{
    switch (s) {
    case TRNG_NOT_STARTED: return "NOT_STARTED";
    case TRNG_OK: return "OK";
    case TRNG_FAULT_BIAS: return "FAULT_BIAS";
    case TRNG_FAULT_STUCK: return "FAULT_STUCK";
    case TRNG_FAULT_RCT: return "FAULT_RCT";
    case TRNG_FAULT_APT: return "FAULT_APT";
    case TRNG_FAULT_B: return "FAULT_B";
    case TRNG_FAULT_CORR: return "FAULT_CORR";
    case TRNG_FAULT_SOURCE: return "FAULT_SOURCE";
    default: return "?";
    }
}

/* ------------------------------------------------------------------------ */
/* Conditioning                                                              */
/* ------------------------------------------------------------------------ */

static uint8_t g_extra[TRNG_EXTRA_MAX];
static size_t g_extra_len;
static uint64_t g_block_counter;
static uint32_t g_jit_blocks, g_acc_blocks;  /* blocks that absorbed clock jitter, accelerometer */
static uint16_t g_jit[JIT_PERIODS];
static int16_t g_acc[ACC_SAMPLES * 3];

uint32_t trng_jitter_blocks(void)
{
    return g_jit_blocks;
}

uint32_t trng_accel_blocks(void)
{
    return g_acc_blocks;
}

void trng_set_extra_input(const uint8_t *x, size_t n)
{
    if (n > sizeof(g_extra)) n = sizeof(g_extra);
    memcpy(g_extra, x, n);
    g_extra_len = n;
}

void trng_clear_extra_input(void)
{
    zirah_memzero(g_extra, sizeof(g_extra));
    g_extra_len = 0;
}

int trng_get(uint8_t *out, size_t n)
{
    if (g_state != TRNG_OK) {
        return -1;
    }

    /* Symbols of source A per 32-byte block, so that symbols * H >=
     * OVERSAMPLE * 256 bits. Source B adds as many symbols again, credited
     * zero. */
    uint32_t need_x100 = (uint32_t)ZIRAH_OVERSAMPLE * 256u * 100u;
    uint32_t nsym = (need_x100 + ZIRAH_H_ASSUMED_X100 - 1u) / ZIRAH_H_ASSUMED_X100;
    bool two = g_b_present;

    uint16_t raw[64], raw_b[64];
    uint8_t sym[64];
    /* Clock jitter and accelerometer readings, once per call and absorbed
     * into every block of it, read before any ADC sampling. */
    bool jit = jitter_read(g_jit, JIT_PERIODS) == 0;
    bool acc = accel_read(g_acc, ACC_SAMPLES) == 0;
    while (n > 0) {
        keccak_state st;
        shake256_init(&st);
        shake256_absorb(&st, (const uint8_t *)"ZIRAH-TRNG-v1", 13);
        uint8_t hdr[10];
        for (int i = 0; i < 8; i++) hdr[i] = (uint8_t)(g_block_counter >> (8 * i));
        /* sources; 0x20 clock jitter, 0x40 accelerometer */
        hdr[8] = (uint8_t)((two ? 2u : 1u) | (jit ? 0x20u : 0u) | (acc ? 0x40u : 0u));
        hdr[9] = (uint8_t)g_extra_len;        /* length of the additional input  */
        g_block_counter++;
        shake256_absorb(&st, hdr, sizeof(hdr));
        if (g_extra_len) {
            shake256_absorb(&st, g_extra, g_extra_len);
        }
        if (jit) {
            shake256_absorb(&st, (const uint8_t *)g_jit, sizeof(g_jit));
            g_jit_blocks++;
        }
        if (acc) {
            shake256_absorb(&st, (const uint8_t *)g_acc, sizeof(g_acc));
            g_acc_blocks++;
        }
        uint32_t left = nsym;
        while (left > 0) {
            uint32_t k = left > 64u ? 64u : left;
            if (two) {
                trng_sample_pair(raw, raw_b, k, ZIRAH_SAMPLE_RATE_HZ);
            } else {
                trng_sample_block(raw, k, ZIRAH_SAMPLE_RATE_HZ, TRNG_SRC_A);
            }
            for (uint32_t i = 0; i < k; i++) {
                sym[i] = (uint8_t)(raw[i] & 0xFF);
                feed_a(sym[i]);
            }
            shake256_absorb(&st, sym, k);
            if (two) {
                for (uint32_t i = 0; i < k; i++) {
                    sym[i] = (uint8_t)(raw_b[i] & 0xFF);
                    feed_b(sym[i]);
                }
                shake256_absorb(&st, sym, k);
            }
            left -= k;
        }
        if (g_state != TRNG_OK) {
            zirah_memzero(&st, sizeof(st));
            zirah_memzero(raw, sizeof(raw));
            zirah_memzero(raw_b, sizeof(raw_b));
            zirah_memzero(sym, sizeof(sym));
            zirah_memzero(g_jit, sizeof(g_jit));
            zirah_memzero(g_acc, sizeof(g_acc));
            return -1;
        }
        shake256_finalize(&st);
        uint8_t blk[32];
        shake256_squeeze(blk, 32, &st);
        size_t take = n < 32 ? n : 32;
        memcpy(out, blk, take);
        out += take;
        n -= take;
        zirah_memzero(blk, sizeof(blk));
        zirah_memzero(&st, sizeof(st));
    }
    /* The raw symbols that went into the output must not linger on the stack. */
    zirah_memzero(g_jit, sizeof(g_jit));
    zirah_memzero(g_acc, sizeof(g_acc));
    zirah_memzero(raw, sizeof(raw));
    zirah_memzero(raw_b, sizeof(raw_b));
    zirah_memzero(sym, sizeof(sym));
    return 0;
}
