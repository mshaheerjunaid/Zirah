/* Host-side stand-in for the software I2C bus: the OLED driver runs
 * unchanged, frames simply stay in its RAM framebuffer. */
#include "swi2c.h"
void i2c_init(void) {}
bool i2c_probe(uint8_t addr7) { (void)addr7; return true; }
bool i2c_write(uint8_t a, const uint8_t *b, size_t n) { (void)a; (void)b; (void)n; return true; }
bool i2c_write_prefixed(uint8_t a, uint8_t p, const uint8_t *b, size_t n) { (void)a; (void)p; (void)b; (void)n; return true; }
bool i2c_read(uint8_t a, uint8_t *b, size_t n) { (void)a; (void)b; (void)n; return false; }
void i2c_sda_low_pulse(uint32_t us) { (void)us; }
void i2c_bus_recover(void) {}
