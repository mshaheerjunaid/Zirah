/*
 * The STM32F407's built-in random number generator (RM0090 chapter 24),
 * used as one more additional input to the conditioner, credited with zero
 * entropy like the ATECC608's generator. It needs no wiring: it samples
 * free-running ring oscillators inside the chip, clocked from the 48 MHz
 * PLL output that USB already uses.
 *
 * The firmware applies the checks the reference manual asks for: the seed
 * and clock error flags, and the continuous test that no 32-bit word equals
 * the one before it. Any failure stops this generator until the next boot;
 * because it is credited zero, that only removes one extra input.
 */
#ifndef ZIRAH_MCU_RNG_H
#define ZIRAH_MCU_RNG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void mcu_rng_init(void);

/* n random bytes; 0 on success, -1 if the generator has failed. */
int mcu_rng_get(uint8_t *out, size_t n);

/* "ok", "fault" or "off" (not started). */
const char *mcu_rng_state_str(void);
bool mcu_rng_ok(void);

#endif
