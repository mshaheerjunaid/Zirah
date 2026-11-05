/*
 * Clock jitter between two independent oscillators on the STM32 itself, as
 * a further physical noise source that needs no parts. The LSI is a free-
 * running RC oscillator near 32 kHz inside the chip; TIM5 counts the CPU's
 * 84 MHz timer clock, which comes from the board's 8 MHz crystal, and
 * captures it on every LSI edge (TIM5 channel 4 is wired to the LSI inside
 * the chip for exactly this kind of measurement). The thermal noise of the
 * RC oscillator makes each period a little different.
 *
 * The periods are absorbed by the conditioner in every random block,
 * credited with zero entropy like the ATECC608 and STM32 generators: the
 * jitter has not been assessed, so it adds one more physical input an
 * attacker must predict without weakening anything if it is poor. A source
 * that fails its check at power-up is left out, and one that fails later is
 * left out until the next boot.
 */
#ifndef ZIRAH_JITTER_H
#define ZIRAH_JITTER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define JIT_PERIODS 256u   /* LSI periods per trng_get() call, about 8 ms */

void jitter_init(void);

/* n periods in TIM5 ticks. 0 on success; -1 if the source is off or the
 * periods failed the check (it is then left out until the next boot). */
int jitter_read(uint16_t *d, size_t n);

bool jitter_live(void);
const char *jitter_state_str(void);  /* "live", "absent" or "fault" */

/* The power-up measurement of JIT_PERIODS periods. */
void jitter_probe_stats(uint32_t *mean, uint32_t *distinct, uint32_t *spread, uint32_t *mode);

/* The JITTER command: a fresh run of periods, never used for output. */
void jitter_diagnose(void (*line)(const char *));

#endif
