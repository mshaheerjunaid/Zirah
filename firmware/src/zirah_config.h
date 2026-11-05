/*
 * Zirah build-time configuration.
 *
 * Everything you are expected to tune after measuring your own hardware lives
 * in this one file. Change a value, rebuild with `make`, reflash.
 */
#ifndef ZIRAH_CONFIG_H
#define ZIRAH_CONFIG_H

#define ZIRAH_FW_VERSION "1.0.0"

/*
 * Host protocol version (docs/PROTOCOL.md). The desktop app and the command
 * line tool refuse to sign with firmware that reports a different number, so
 * it must change whenever a command or reply changes.
 */
#define ZIRAH_PROTO_VERSION 1

/* ------------------------------------------------------------------------ */
/* Entropy source                                                            */
/* ------------------------------------------------------------------------ */

/*
 * Assessed min-entropy of ONE noise-source symbol, in hundredths of a bit.
 * A symbol is the low 8 bits of one 12-bit ADC sample of the amplified
 * avalanche noise.
 *
 * The default (100 = 1.00 bit per 8-bit symbol) is deliberately pessimistic.
 * After you run the NIST SP 800-90B non-IID assessment on your own raw data
 * (data/entropy/README.md), you may set this to the reported min-entropy rounded
 * DOWN, for example 3.41 bits -> 341, then rebuild and reflash. Never set it
 * higher than what the NIST tool reported.
 */
#ifndef ZIRAH_H_ASSUMED_X100
#define ZIRAH_H_ASSUMED_X100 100
#endif

/*
 * Oversampling factor for conditioning: each 256-bit output block absorbs
 * enough raw symbols to carry ZIRAH_OVERSAMPLE * 256 bits of assessed
 * min-entropy.
 */
#define ZIRAH_OVERSAMPLE 2

/*
 * Optional second noise source (source B, on PB1; hardware/WIRING.md,
 * "Second Noise Source"). The firmware looks for it at power-up and, when it
 * is there, measures the correlation between the two sources in every
 * startup test. Two independent sources give values around 0 with a spread
 * of about 31 (in thousandths) over the 1024 samples of the test, so this
 * limit of 300 is about ten times that spread and cannot be crossed by
 * chance. A common signal that reaches both sources and makes up more than
 * about 30 % of their variance (supply ripple, an injected tone) crosses it,
 * and the source then faults with FAULT_CORR.
 */
#ifndef ZIRAH_MAX_CORR_X1000
#define ZIRAH_MAX_CORR_X1000 300
#endif

/* Default ADC sampling rate for the noise source, in samples per second. */
#define ZIRAH_SAMPLE_RATE_HZ 20000

/* Bias monitor: resistor divider from the boost output to PB0. */
/* Only the ratio matters: 100 kohm over 10 kohm reads the same. */
#define ZIRAH_DIV_TOP_OHMS    1000000UL  /* 1 Mohm from boost output to PB0 */
#define ZIRAH_DIV_BOTTOM_OHMS 100000UL   /* 100 kohm from PB0 to GND       */

/* Bias voltage window in millivolts. Outside this window = FAULT. */
#define ZIRAH_VBIAS_MIN_MV  9000
#define ZIRAH_VBIAS_MAX_MV 20000

/*
 * Idle signal monitor. While nothing else is happening the firmware samples
 * one block of raw noise per period, runs it through the continuous health
 * tests, draws it on the OLED and keeps it for the PEEK command. These
 * samples are only displayed; they never feed the key or signature path.
 */
#define ZIRAH_MONITOR_SAMPLES   512   /* per source */
#define ZIRAH_MONITOR_PERIOD_MS 900
/* Re-measure the boost voltage every this many monitor blocks. */
#define ZIRAH_MONITOR_VBIAS_EVERY 8

/* ------------------------------------------------------------------------ */
/* Signing                                                                   */
/* ------------------------------------------------------------------------ */

/* FIPS 204 context string bound into every Zirah file signature. */
#define ZIRAH_CTX "zirah-doc-v1"

/*
 * Context string and message prefix for the PROVE identity challenge. A
 * different FIPS 204 context makes an identity proof impossible to replay as
 * a file signature, and the verifier pins the file-signing context.
 */
#define ZIRAH_PROVE_CTX    "zirah-attest-v1"
#define ZIRAH_PROVE_PREFIX "ZIRAH-PROVE-v1"
/* PROVE needs no button press, so it is rate limited. */
#define ZIRAH_PROVE_MIN_INTERVAL_MS 1500
#define ZIRAH_PROVE_MAX_PER_BOOT    64

/* Require a press of the blue USER button before each signature. */
#define ZIRAH_CONFIRM_DEFAULT 1
#define ZIRAH_CONFIRM_TIMEOUT_MS 30000
/* A button held down longer than this is treated as stuck, not as consent. */
#define ZIRAH_BUTTON_STUCK_MS 5000

/* ------------------------------------------------------------------------ */
/* Hardening                                                                 */
/* ------------------------------------------------------------------------ */

/* MPU: SRAM and CCM execute-never, plus a no-access guard above .bss that
 * turns a stack overflow into a clean fault instead of silent corruption. */
#ifndef ZIRAH_ENABLE_MPU
#define ZIRAH_ENABLE_MPU 1
#endif

/* Accept commands on USART2 (PA2/PA3) as well as USB. Set to 0 to shrink the
 * attack surface if you never use a USB-TTL adapter. */
#ifndef ZIRAH_ENABLE_UART
#define ZIRAH_ENABLE_UART 1
#endif

/* ------------------------------------------------------------------------ */
/* Peripherals                                                               */
/* ------------------------------------------------------------------------ */

#define ZIRAH_UART_BAUD 115200

/* ATECC608 7-bit I2C address (factory default 0x60). */
#define ZIRAH_ATECC_ADDR 0x60

/* OLED 7-bit I2C address (almost always 0x3C, sometimes 0x3D). */
#define ZIRAH_OLED_ADDR 0x3C

/*
 * OLED controller. Most 1.3 inch 128x64 modules use the SH1106, which has a
 * 132-column RAM and needs a 2-column offset. If your picture is shifted or
 * has a stripe of garbage on one edge, try ZIRAH_OLED_SSD1306 = 1.
 */
#ifndef ZIRAH_OLED_SSD1306
#define ZIRAH_OLED_SSD1306 0
#endif

/* Software I2C half-period in microseconds (5 -> roughly 100 kHz). */
#define ZIRAH_I2C_HALF_PERIOD_US 5

#endif
