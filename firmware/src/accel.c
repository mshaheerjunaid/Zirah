#include "accel.h"
#include "board.h"
#include "trng_logic.h"
#include "secure.h"
#include "board_pins.h"

#include <stdio.h>
#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/stm32/spi.h>

typedef enum { ACC_OFF = 0, ACC_LIVE, ACC_ABSENT, ACC_FAULT } acc_state_t;
typedef enum { CHIP_NONE = 0, CHIP_LIS3DSH, CHIP_LIS302DL } acc_chip_t;

#define WHO_AM_I 0x0Fu
#define STATUS 0x27u            /* both chips; bit 3: new x, y and z data */
#define ID_LIS3DSH 0x3Fu
#define ID_LIS302DL 0x3Bu

static acc_state_t g_acc = ACC_OFF;
static acc_chip_t g_chip = CHIP_NONE;
static uint32_t g_least;
static int16_t g_probe[ACC_SAMPLES * 3];

static void cs(bool low)
{
    if (low) {
        gpio_clear(GPIOE, GPIO3);
    } else {
        gpio_set(GPIOE, GPIO3);
    }
}

static bool xfer(uint8_t out, uint8_t *in)
{
    SPI_DR(SPI1) = out;
    for (uint32_t spin = 0; spin < 20000u; spin++) {
        if (SPI_SR(SPI1) & SPI_SR_RXNE) {
            *in = (uint8_t)SPI_DR(SPI1);
            return true;
        }
    }
    return false;
}

static bool reg_read(uint8_t reg, uint8_t *v)
{
    uint8_t junk;
    cs(true);
    bool ok = xfer((uint8_t)(0x80u | reg), &junk) && xfer(0, v);
    cs(false);
    return ok;
}

static bool reg_write(uint8_t reg, uint8_t v)
{
    uint8_t junk;
    cs(true);
    bool ok = xfer(reg, &junk) && xfer(v, &junk);
    cs(false);
    return ok;
}

/* SPI1 master, mode 3, 84 MHz / 32 = 2.6 MHz (both chips allow 10 MHz). */
static void spi_setup(void)
{
    rcc_periph_clock_enable(RCC_GPIOA);
    rcc_periph_clock_enable(RCC_GPIOE);
    rcc_periph_clock_enable(RCC_SPI1);
    gpio_set(GPIOE, GPIO3);
    gpio_mode_setup(GPIOE, GPIO_MODE_OUTPUT, GPIO_PUPD_NONE, GPIO3);
    gpio_mode_setup(GPIOA, GPIO_MODE_AF, GPIO_PUPD_NONE, GPIO5 | GPIO6 | GPIO7);
    gpio_set_af(GPIOA, GPIO_AF5, GPIO5 | GPIO6 | GPIO7);
    SPI_CR1(SPI1) = 0;
    SPI_CR1(SPI1) = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI | SPI_CR1_BAUDRATE_FPCLK_DIV_32 |
                    SPI_CR1_CPOL_CLK_TO_1_WHEN_IDLE | SPI_CR1_CPHA_CLK_TRANSITION_2;
    SPI_CR1(SPI1) |= SPI_CR1_SPE;
}

/* One reading of x, y and z; false after 20 ms without new data (the chips
 * run at 1600 and 400 readings a second). */
static bool reading(int16_t *xyz)
{
    uint32_t t0 = millis();
    uint8_t st = 0;
    do {
        if (!reg_read(STATUS, &st)) {
            return false;
        }
        if ((uint32_t)(millis() - t0) > 20u) {
            return false;
        }
    } while (!(st & 0x08u));
    for (int axis = 0; axis < 3; axis++) {
        uint8_t lo = 0, hi = 0;
        if (g_chip == CHIP_LIS3DSH) {
            if (!reg_read((uint8_t)(0x28u + 2u * (unsigned)axis), &lo) ||
                !reg_read((uint8_t)(0x29u + 2u * (unsigned)axis), &hi)) {
                return false;
            }
            xyz[axis] = (int16_t)((uint16_t)hi << 8 | lo);
        } else {
            if (!reg_read((uint8_t)(0x29u + 2u * (unsigned)axis), &hi)) {
                return false;
            }
            xyz[axis] = (int16_t)(int8_t)hi;
        }
    }
    return true;
}

static bool readings(int16_t *xyz, size_t samples)
{
    for (size_t i = 0; i < samples; i++) {
        if (!reading(xyz + 3 * i)) {
            return false;
        }
    }
    return true;
}

void accel_init(void)
{
#if !BOARD_HAS_ACCEL
    g_acc = ACC_ABSENT;               /* this board has no accelerometer */
    return;
#endif
    spi_setup();
    g_acc = ACC_ABSENT;
    uint8_t who = 0;
    if (!reg_read(WHO_AM_I, &who)) {
        return;
    }
    if (who == ID_LIS3DSH) {
        g_chip = CHIP_LIS3DSH;
        if (!reg_write(0x20u, 0x97u)) {      /* CTRL_REG4: 1600 Hz, x, y and z on */
            return;
        }
    } else if (who == ID_LIS302DL) {
        g_chip = CHIP_LIS302DL;
        if (!reg_write(0x20u, 0xC7u)) {      /* CTRL_REG1: 400 Hz, powered, x, y and z on */
            return;
        }
    } else {
        return;
    }
    delay_ms(20);                            /* boot and the first readings, dropped */
    if (readings(g_probe, 4) && readings(g_probe, ACC_SAMPLES) &&
        trng_accel_ok(g_probe, ACC_SAMPLES, &g_least)) {
        g_acc = ACC_LIVE;
    }
    zirah_memzero(g_probe, sizeof(g_probe));
}

int accel_read(int16_t *xyz, size_t samples)
{
    if (g_acc != ACC_LIVE) {
        return -1;
    }
    if (!readings(xyz, samples) || !trng_accel_ok(xyz, samples, NULL)) {
        g_acc = ACC_FAULT;
        zirah_memzero(xyz, samples * 3 * sizeof(int16_t));
        return -1;
    }
    return 0;
}

bool accel_live(void)
{
    return g_acc == ACC_LIVE;
}

const char *accel_state_str(void)
{
    return g_acc == ACC_LIVE ? "live" : g_acc == ACC_FAULT ? "fault" : "absent";
}

const char *accel_chip(void)
{
    return g_chip == CHIP_LIS3DSH ? "lis3dsh" : g_chip == CHIP_LIS302DL ? "lis302dl" : "none";
}

void accel_probe_stats(uint32_t *least)
{
    *least = g_least;
}

void accel_diagnose(void (*line)(const char *))
{
    char tmp[120];
#if !BOARD_HAS_ACCEL
    line("ACC_CHIP who_am_i=0x00 answered=0 chip=none board_has_none=1");
    snprintf(tmp, sizeof(tmp), "ACC state=%s chip=%s", accel_state_str(), accel_chip());
    line(tmp);
    return;
#endif
    uint8_t who = 0;
    bool answered = reg_read(WHO_AM_I, &who);
    snprintf(tmp, sizeof(tmp), "ACC_CHIP who_am_i=0x%02x answered=%u chip=%s", who, answered ? 1u : 0u,
             accel_chip());
    line(tmp);
    if (g_chip != CHIP_NONE) {
        bool read = readings(g_probe, ACC_SAMPLES);
        uint32_t least = 0;
        bool ok = read && trng_accel_ok(g_probe, ACC_SAMPLES, &least);
        int32_t lo[3] = {32767, 32767, 32767}, hi[3] = {-32768, -32768, -32768};
        for (size_t i = 0; read && i < ACC_SAMPLES; i++) {
            for (int a = 0; a < 3; a++) {
                int16_t v = g_probe[3 * i + (size_t)a];
                if (v < lo[a]) lo[a] = v;
                if (v > hi[a]) hi[a] = v;
            }
        }
        snprintf(tmp, sizeof(tmp), "ACC_RUN read=%u n=%lu least_distinct=%lu live=%u", read ? 1u : 0u,
                 (unsigned long)ACC_SAMPLES, (unsigned long)least, ok ? 1u : 0u);
        line(tmp);
        if (read) {
            static const char axis[3] = {'x', 'y', 'z'};
            for (int a = 0; a < 3; a++) {
                snprintf(tmp, sizeof(tmp), "ACC_AXIS %c min=%ld max=%ld", axis[a], (long)lo[a], (long)hi[a]);
                line(tmp);
            }
        }
        zirah_memzero(g_probe, sizeof(g_probe));
    }
    snprintf(tmp, sizeof(tmp), "ACC state=%s chip=%s", accel_state_str(), accel_chip());
    line(tmp);
}
