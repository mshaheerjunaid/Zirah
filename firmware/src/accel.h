/*
 * The STM32F4DISCOVERY's own accelerometer (LIS3DSH on newer boards,
 * LIS302DL on older ones) as a further physical noise source that needs no
 * parts. It sits on SPI1 (PA5, PA6, PA7, chip select PE3). With the board at
 * rest its readings still move in their low bits, from the sensor's own
 * mechanical and electronic noise.
 *
 * ACC_SAMPLES readings of all three axes are taken once per trng_get() call
 * and absorbed into every conditioner block of that call, credited with
 * zero entropy like the ATECC608 and STM32 generators: movement of the
 * board shows up in them and they have not been assessed. A chip that does
 * not answer, or whose readings show no noise at power-up, is left out; one
 * that fails later is left out until the next boot.
 */
#ifndef ZIRAH_ACCEL_H
#define ZIRAH_ACCEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ACC_SAMPLES 16u   /* x, y, z readings per trng_get() call */

void accel_init(void);

/* samples readings of x, y and z, interleaved. 0 on success; -1 if the
 * accelerometer is off or the readings failed the check. */
int accel_read(int16_t *xyz, size_t samples);

bool accel_live(void);
const char *accel_state_str(void);   /* "live", "absent" or "fault" */
const char *accel_chip(void);        /* "lis3dsh", "lis302dl" or "none" */
void accel_probe_stats(uint32_t *least);

/* The ACCEL command: a fresh set of readings, never used for output. */
void accel_diagnose(void (*line)(const char *));

#endif
