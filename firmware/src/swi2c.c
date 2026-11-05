#include "swi2c.h"
#include "board.h"
#include "zirah_config.h"

#include <libopencm3/stm32/gpio.h>

#define SCL_PORT GPIOA
#define SCL_PIN  GPIO8
#define SDA_PORT GPIOC
#define SDA_PIN  GPIO9

#define HALF ZIRAH_I2C_HALF_PERIOD_US

/* Open-drain: "release" lets the pull-up take the line high. */
static inline void scl_release(void) { gpio_set(SCL_PORT, SCL_PIN); }
static inline void scl_low(void)     { gpio_clear(SCL_PORT, SCL_PIN); }
static inline void sda_release(void) { gpio_set(SDA_PORT, SDA_PIN); }
static inline void sda_low(void)     { gpio_clear(SDA_PORT, SDA_PIN); }
static inline bool sda_read(void)    { return gpio_get(SDA_PORT, SDA_PIN) != 0; }
static inline bool scl_read(void)    { return gpio_get(SCL_PORT, SCL_PIN) != 0; }

static bool g_bus_error;

/* Release SCL and wait for it to actually go high (clock stretching). */
static void scl_high_wait(void)
{
    scl_release();
    uint32_t t0 = cycles_now();
    while (!scl_read()) {
        if (cycles_to_us((uint32_t)(cycles_now() - t0)) > 2000u) {
            g_bus_error = true;
            return;
        }
    }
}

static void start(void)
{
    sda_release();
    scl_high_wait();
    delay_us(HALF);
    sda_low();
    delay_us(HALF);
    scl_low();
    delay_us(HALF);
}

static void stop(void)
{
    sda_low();
    delay_us(HALF);
    scl_high_wait();
    delay_us(HALF);
    sda_release();
    delay_us(HALF);
}

static bool write_byte(uint8_t b)
{
    for (int i = 7; i >= 0; i--) {
        if (b & (1u << i)) {
            sda_release();
        } else {
            sda_low();
        }
        delay_us(HALF);
        scl_high_wait();
        delay_us(HALF);
        scl_low();
    }
    /* ACK bit */
    sda_release();
    delay_us(HALF);
    scl_high_wait();
    delay_us(HALF);
    bool ack = !sda_read();
    scl_low();
    delay_us(HALF);
    return ack;
}

static uint8_t read_byte(bool ack)
{
    uint8_t b = 0;
    sda_release();
    for (int i = 7; i >= 0; i--) {
        delay_us(HALF);
        scl_high_wait();
        delay_us(HALF);
        if (sda_read()) {
            b |= (uint8_t)(1u << i);
        }
        scl_low();
    }
    if (ack) {
        sda_low();
    } else {
        sda_release();
    }
    delay_us(HALF);
    scl_high_wait();
    delay_us(HALF);
    scl_low();
    sda_release();
    delay_us(HALF);
    return b;
}

void i2c_bus_recover(void)
{
    sda_release();
    for (int i = 0; i < 9; i++) {
        scl_low();
        delay_us(HALF);
        scl_release();
        delay_us(HALF);
    }
    stop();
}

void i2c_init(void)
{
    gpio_set(SCL_PORT, SCL_PIN);
    gpio_set(SDA_PORT, SDA_PIN);
    /* Internal pull-ups are weak (~40 kohm); the external 4.7 kohm ones do the real work. */
    gpio_mode_setup(SCL_PORT, GPIO_MODE_OUTPUT, GPIO_PUPD_PULLUP, SCL_PIN);
    gpio_set_output_options(SCL_PORT, GPIO_OTYPE_OD, GPIO_OSPEED_2MHZ, SCL_PIN);
    gpio_mode_setup(SDA_PORT, GPIO_MODE_OUTPUT, GPIO_PUPD_PULLUP, SDA_PIN);
    gpio_set_output_options(SDA_PORT, GPIO_OTYPE_OD, GPIO_OSPEED_2MHZ, SDA_PIN);
    delay_us(100);
    i2c_bus_recover();
}

bool i2c_probe(uint8_t addr7)
{
    g_bus_error = false;
    start();
    bool ack = write_byte((uint8_t)(addr7 << 1));
    stop();
    return ack && !g_bus_error;
}

bool i2c_write_prefixed(uint8_t addr7, uint8_t prefix, const uint8_t *buf, size_t len)
{
    g_bus_error = false;
    start();
    bool ok = write_byte((uint8_t)(addr7 << 1));
    if (ok) {
        ok = write_byte(prefix);
    }
    /* Stop at the first NACK or bus error instead of clocking out the rest
     * of the buffer into a stuck bus. */
    for (size_t i = 0; ok && !g_bus_error && i < len; i++) {
        ok = write_byte(buf[i]);
    }
    stop();
    return ok && !g_bus_error;
}

bool i2c_write(uint8_t addr7, const uint8_t *buf, size_t len)
{
    g_bus_error = false;
    start();
    bool ok = write_byte((uint8_t)(addr7 << 1));
    for (size_t i = 0; ok && !g_bus_error && i < len; i++) {
        ok = write_byte(buf[i]);
    }
    stop();
    return ok && !g_bus_error;
}

bool i2c_read(uint8_t addr7, uint8_t *buf, size_t len)
{
    g_bus_error = false;
    start();
    bool ok = write_byte((uint8_t)((addr7 << 1) | 1u));
    if (ok) {
        for (size_t i = 0; i < len; i++) {
            buf[i] = read_byte(i + 1 < len);
            if (g_bus_error) {
                break;
            }
        }
    }
    stop();
    return ok && !g_bus_error;
}

void i2c_zero_byte_pulse(void)
{
    /* START, the address byte 0x00, STOP: SDA stays low for eight bit times,
     * the wake method Microchip's own libraries use at 100 kHz. */
    g_bus_error = false;
    start();
    (void)write_byte(0x00);
    stop();
}

void i2c_sda_low_pulse(uint32_t us)
{
    scl_high_wait();
    sda_low();
    delay_us(us);
    sda_release();
}
