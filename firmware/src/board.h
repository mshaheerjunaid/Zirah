/*
 * Board support for the STM32F4DISCOVERY (STM32F407VG).
 *
 * Pin map used by Zirah (all pins below are free on the Discovery board):
 *   PA1   ADC123_IN1   amplified avalanche noise (op-amp output via 1 kohm)
 *   PB0   ADC12_IN8    boost-voltage monitor (1 Mohm / 100 kohm divider)
 *   PA2   USART2_TX    -> RX of a USB-TTL adapter (optional)
 *   PA3   USART2_RX    <- TX of a USB-TTL adapter (optional)
 *   PA8   I2C SCL      software I2C, shared by ATECC608 and OLED
 *   PC9   I2C SDA      software I2C, shared by ATECC608 and OLED
 *   PA9/11/12          USB OTG FS (micro-USB connector CN5), used as USB serial
 *   PA0   USER button  (blue button, active high)
 *   PD12-15            on-board LEDs (green, orange, red, blue)
 *   PC0               OTG VBUS power switch enable, held high (switch off)
 *
 * Hardening done here (see docs/DESIGN.md, "Firmware Hardening"):
 *   - MPU: SRAM and CCM are execute-never; a no-access guard region sits
 *     just above .bss so a stack overflow faults instead of corrupting data.
 *   - Fault handlers switch to a private stack, wipe all secrets and halt.
 *   - -fstack-protector-strong canary, randomised from the TRNG at boot.
 *   - Integer divide-by-zero traps instead of silently returning 0.
 *   - The C heap is disabled (_sbrk always fails), because the linker would
 *     otherwise place it on top of the masked key in CCM.
 */
#ifndef ZIRAH_BOARD_H
#define ZIRAH_BOARD_H

#include <stdint.h>
#include <stdbool.h>

#define LED_GREEN  (1u << 12)
#define LED_ORANGE (1u << 13)
#define LED_RED    (1u << 14)
#define LED_BLUE   (1u << 15)

void board_init(void);

/* The crystal the clocks run from, in MHz; 0 when running from the HSI. */
uint32_t board_hse_mhz(void);

uint32_t millis(void);
void delay_ms(uint32_t ms);
void delay_us(uint32_t us);

/* Free-running CPU cycle counter (168 MHz). */
uint32_t cycles_now(void);
uint32_t cycles_to_us(uint32_t cycles);

void led_set(uint32_t mask, bool on);
void led_only(uint32_t mask);

bool button_pressed(void);

/* 96-bit factory unique device ID (not secret; used as an identifier). */
void board_uid(uint8_t out[12]);

/* Enable the MPU layout described above. Returns false if the RAM layout
 * leaves less than `min_stack` bytes of stack (the build is then unsafe). */
bool board_mpu_init(uint32_t min_stack);
bool board_mpu_enabled(void);
uint32_t board_stack_bytes(void);

/* Flash readout protection level from the option bytes: 0 (debugger can read
 * the flash, including the ML-DSA key), 1 (flash reads blocked) or 2
 * (permanent, debug port disabled). Only reported, never changed here;
 * scripts/rdp.ps1 sets it. */
unsigned board_rdp_level(void);

/* Replace the stack-protector canary. Call only from main(), which never
 * returns, so no live frame holds the old value. */
void board_set_stack_guard(uint32_t value);

/* Called from the fault path to wipe buffers outside CCM. */
void board_set_fault_wiper(void (*fn)(void));

/* Fault codes passed to zirah_fault_entry() and shown on the OLED/UART. */
#define ZIRAH_FAULT_HARD   1u  /* HardFault                                  */
#define ZIRAH_FAULT_MEM    2u  /* MemManage (MPU: guard hit, execute-never)  */
#define ZIRAH_FAULT_BUS    3u  /* BusFault                                   */
#define ZIRAH_FAULT_USAGE  4u  /* UsageFault (undefined instr, divide by 0) */
#define ZIRAH_FAULT_CANARY 5u  /* stack-protector canary mismatch            */
#define ZIRAH_FAULT_LAYOUT 6u  /* firmware image overlaps the key store      */
#define ZIRAH_FAULT_RAM    7u  /* RAM layout leaves too little stack         */
#define ZIRAH_FAULT_RNG    8u  /* reference code asked for bad randomness    */

/* Wipe CCM, run the wiper, show the reason, halt with a red LED. */
__attribute__((noreturn)) void zirah_fault_entry(uint32_t code);

/* CPU clock in Hz after board_init(). */
#define ZIRAH_CPU_HZ 168000000UL

#endif
