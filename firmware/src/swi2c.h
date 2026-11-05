/*
 * Bit-banged I2C master on PA8 (SCL) and PC9 (SDA), open-drain.
 *
 * A software master is used on purpose: the ATECC608 NACKs its address while
 * it is busy or asleep, and needs a "wake" pulse with SDA held low. Both are
 * easy and robust to handle in software, and the bus is slow-speed anyway.
 */
#ifndef ZIRAH_SWI2C_H
#define ZIRAH_SWI2C_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

void i2c_init(void);

/* Returns true if a device ACKs the 7-bit address. */
bool i2c_probe(uint8_t addr7);

/* START, address+W, bytes, STOP. False on any NACK or bus error. */
bool i2c_write(uint8_t addr7, const uint8_t *buf, size_t len);

/* Same as i2c_write but sends a 1-byte prefix before buf (control/word address). */
bool i2c_write_prefixed(uint8_t addr7, uint8_t prefix, const uint8_t *buf, size_t len);

/* START, address+R, bytes (ACK all but last), STOP. False on NACK. */
bool i2c_read(uint8_t addr7, uint8_t *buf, size_t len);

/* Wake helper for the ATECC608: START, address byte 0x00, STOP. */
void i2c_zero_byte_pulse(void);

/* Hold SDA low for `us` microseconds with SCL high, then release (ATECC wake). */
void i2c_sda_low_pulse(uint32_t us);

/* Clock out a stuck slave (9 clocks + STOP). */
void i2c_bus_recover(void);

#endif
