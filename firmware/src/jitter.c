#include "jitter.h"
#include "board.h"
#include "trng_logic.h"
#include "secure.h"

#include <stdio.h>
#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/timer.h>

typedef enum { JIT_OFF = 0, JIT_LIVE, JIT_ABSENT, JIT_FAULT } jit_state_t;

static jit_state_t g_jit = JIT_OFF;
static uint32_t g_mean, g_distinct, g_spread, g_mode;
static uint16_t g_probe[JIT_PERIODS];

static void timer_setup(void)
{
    RCC_CSR |= RCC_CSR_LSION;
    for (uint32_t spin = 0; spin < 2000000u && !(RCC_CSR & RCC_CSR_LSIRDY); spin++) {
    }
    rcc_periph_clock_enable(RCC_TIM5);
    TIM_CR1(TIM5) = 0;
    TIM_PSC(TIM5) = 0;                              /* 84 MHz, one tick about 11.9 ns */
    TIM_ARR(TIM5) = 0xFFFFFFFFu;
    TIM5_OR = TIM5_OR_TI4_RMP_LSI;                  /* channel 4 input: the LSI */
    TIM_CCMR2(TIM5) = TIM_CCMR2_CC4S_IN_TI4 | TIM_CCMR2_IC4PSC_OFF;
    TIM_CCER(TIM5) = TIM_CCER_CC4E;                 /* capture on rising edges */
    TIM_EGR(TIM5) = TIM_EGR_UG;
    TIM_SR(TIM5) = 0;
    TIM_CR1(TIM5) = TIM_CR1_CEN;
}

/* The next capture; false after about 2 ms without one (the LSI period is
 * at most about 60 us). */
static bool next_capture(uint32_t *c)
{
    for (uint32_t spin = 0; spin < 100000u; spin++) {
        if (TIM_SR(TIM5) & TIM_SR_CC4IF) {
            *c = TIM_CCR4(TIM5);                    /* reading clears CC4IF */
            return true;
        }
    }
    return false;
}

/* n periods. A missed edge (an interrupt held the loop longer than one
 * period) sets the over-capture flag; that period is dropped and timing
 * restarts from the new edge, so a period is never counted double. */
static bool periods(uint16_t *d, size_t n)
{
    if (!(RCC_CSR & RCC_CSR_LSIRDY)) {
        return false;
    }
    uint32_t prev, cur;
    TIM_SR(TIM5) = 0;
    if (!next_capture(&prev)) {
        return false;
    }
    size_t got = 0;
    uint32_t tries = 0;
    while (got < n) {
        if (++tries > 4u * n + 64u || !next_capture(&cur)) {
            return false;
        }
        if (TIM_SR(TIM5) & TIM_SR_CC4OF) {
            TIM_SR(TIM5) = ~TIM_SR_CC4OF;
            prev = cur;
            continue;
        }
        uint32_t delta = cur - prev;
        d[got++] = delta > 0xFFFFu ? 0xFFFFu : (uint16_t)delta;
        prev = cur;
    }
    return true;
}

static uint32_t mean_of(const uint16_t *d, size_t n)
{
    uint32_t sum = 0;
    for (size_t i = 0; i < n; i++) sum += d[i];
    return n ? sum / (uint32_t)n : 0;
}

void jitter_init(void)
{
    timer_setup();
    g_jit = JIT_ABSENT;
    if (periods(g_probe, JIT_PERIODS) && trng_jitter_ok(g_probe, JIT_PERIODS, &g_distinct, &g_spread, &g_mode)) {
        g_mean = mean_of(g_probe, JIT_PERIODS);
        g_jit = JIT_LIVE;
    }
    zirah_memzero(g_probe, sizeof(g_probe));
}

int jitter_read(uint16_t *d, size_t n)
{
    if (g_jit != JIT_LIVE) {
        return -1;
    }
    if (!periods(d, n) || !trng_jitter_ok(d, n, NULL, NULL, NULL)) {
        g_jit = JIT_FAULT;
        zirah_memzero(d, n * sizeof(uint16_t));
        return -1;
    }
    return 0;
}

bool jitter_live(void)
{
    return g_jit == JIT_LIVE;
}

const char *jitter_state_str(void)
{
    return g_jit == JIT_LIVE ? "live" : g_jit == JIT_FAULT ? "fault" : "absent";
}

void jitter_probe_stats(uint32_t *mean, uint32_t *distinct, uint32_t *spread, uint32_t *mode)
{
    *mean = g_mean;
    *distinct = g_distinct;
    *spread = g_spread;
    *mode = g_mode;
}

void jitter_diagnose(void (*line)(const char *))
{
    char tmp[120];
    snprintf(tmp, sizeof(tmp), "JIT_LSI ready=%u", (RCC_CSR & RCC_CSR_LSIRDY) ? 1u : 0u);
    line(tmp);
    bool read = periods(g_probe, JIT_PERIODS);
    uint32_t distinct = 0, spread = 0, mode = 0, lo = 0xFFFFu, hi = 0;
    bool ok = read && trng_jitter_ok(g_probe, JIT_PERIODS, &distinct, &spread, &mode);
    uint64_t sq = 0;
    uint32_t mean = read ? mean_of(g_probe, JIT_PERIODS) : 0;
    for (size_t i = 0; read && i < JIT_PERIODS; i++) {
        int32_t e = (int32_t)g_probe[i] - (int32_t)mean;
        sq += (uint64_t)((int64_t)e * e);
        if (g_probe[i] < lo) lo = g_probe[i];
        if (g_probe[i] > hi) hi = g_probe[i];
    }
    /* Standard deviation x10 by integer square root. */
    uint64_t v = read ? (sq * 100u) / JIT_PERIODS : 0, r = 0;
    for (uint64_t bit = 1ull << 62; bit; bit >>= 2) {
        if (v >= r + bit) {
            v -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
    }
    snprintf(tmp, sizeof(tmp),
             "JIT_RUN read=%u n=%lu mean_ticks=%lu lsi_hz=%lu std_x10=%lu min=%lu max=%lu distinct=%lu mode=%lu live=%u",
             read ? 1u : 0u, (unsigned long)JIT_PERIODS, (unsigned long)mean,
             (unsigned long)(mean ? 84000000u / mean : 0), (unsigned long)r, (unsigned long)(read ? lo : 0),
             (unsigned long)hi, (unsigned long)distinct, (unsigned long)mode, ok ? 1u : 0u);
    line(tmp);
    if (read) {
        /* The first periods of this fresh run, for diagnosis only. */
        int k = snprintf(tmp, sizeof(tmp), "JIT_PERIODS");
        for (uint32_t j = 0; j < 16 && k > 0 && (size_t)k < sizeof(tmp) - 6; j++) {
            k += snprintf(tmp + k, sizeof(tmp) - (size_t)k, " %u", g_probe[j]);
        }
        line(tmp);
    }
    zirah_memzero(g_probe, sizeof(g_probe));
    snprintf(tmp, sizeof(tmp), "JIT state=%s", jitter_state_str());
    line(tmp);
}
