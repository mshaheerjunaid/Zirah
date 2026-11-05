#include "mcu_rng.h"

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/rng.h>

typedef enum { MRNG_OFF = 0, MRNG_OK, MRNG_FAULT } mrng_state_t;

static mrng_state_t g_mrng = MRNG_OFF;
static uint32_t g_prev;

/* One 32-bit word, or false on a seed/clock error or a timeout. A word
 * takes 40 periods of the 48 MHz clock, well under a microsecond. */
static bool next_word(uint32_t *w)
{
    for (uint32_t spin = 0; spin < 100000u; spin++) {
        uint32_t sr = RNG_SR;
        if (sr & (RNG_SR_SECS | RNG_SR_CECS | RNG_SR_SEIS | RNG_SR_CEIS)) {
            return false;
        }
        if (sr & RNG_SR_DRDY) {
            *w = RNG_DR;
            return true;
        }
    }
    return false;
}

void mcu_rng_init(void)
{
    rcc_periph_clock_enable(RCC_RNG);
    RNG_CR |= RNG_CR_RNGEN;
    /* The first word is only kept as the reference for the continuous test. */
    g_mrng = next_word(&g_prev) ? MRNG_OK : MRNG_FAULT;
}

int mcu_rng_get(uint8_t *out, size_t n)
{
    if (g_mrng != MRNG_OK) {
        return -1;
    }
    while (n > 0) {
        uint32_t w;
        if (!next_word(&w) || w == g_prev) {
            g_mrng = MRNG_FAULT;
            RNG_CR &= ~RNG_CR_RNGEN;
            return -1;
        }
        g_prev = w;
        size_t take = n < 4 ? n : 4;
        for (size_t i = 0; i < take; i++) {
            out[i] = (uint8_t)(w >> (8 * i));
        }
        out += take;
        n -= take;
    }
    return 0;
}

bool mcu_rng_ok(void)
{
    return g_mrng == MRNG_OK;
}

const char *mcu_rng_state_str(void)
{
    return g_mrng == MRNG_OK ? "ok" : g_mrng == MRNG_FAULT ? "fault" : "off";
}
