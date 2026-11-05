/*
 * The two boards Zirah runs on, chosen at build time (make BOARD=...).
 *
 *   discovery  STM32F4DISCOVERY, STM32F407VG, 1 MB flash (the default)
 *   devebox    DevEBox "STM32F4XX_M" core board, STM32F407VE, 512 KB flash,
 *              USB-C, SWD header, no ST-LINK, user LED on PA1, K1 on PA0
 *
 * Both carry the same STM32F407 die (168 MHz, 192 KB RAM with 64 KB CCM,
 * hardware RNG, readout protection), so everything that matters for
 * security and timing is the same; only the pins, the flash size and the
 * programmer differ. On the DevEBox the user LED is wired to PA1, the pin
 * the Discovery build samples the noise on, so the noise moves to PA4 there.
 */
#ifndef ZIRAH_BOARD_PINS_H
#define ZIRAH_BOARD_PINS_H

#if defined(ZIRAH_BOARD_DEVEBOX)

#define ZIRAH_BOARD_NAME "devebox"
#define NOISE_PORT GPIOA          /* source A: PA4, ADC12_IN4 */
#define NOISE_PIN GPIO4
#define NOISE_CH 4
#define NOISE_NAME "PA4"
#define UART_DEV USART1           /* PA9 TX, PA10 RX: also the ROM bootloader's port */
#define UART_RCC RCC_USART1
#define UART_IRQ NVIC_USART1_IRQ
#define UART_ISR usart1_isr
#define UART_PORT GPIOA
#define UART_PINS (GPIO9 | GPIO10)
#define UART_NAME "PA9/PA10"
#define KS_SECTOR 7               /* the last 128 KB sector of 512 KB */
#define KS_ADDR 0x08060000UL
#define BOARD_HAS_ACCEL 0         /* no accelerometer on this board */

#else

#define ZIRAH_BOARD_NAME "discovery"
#define NOISE_PORT GPIOA          /* source A: PA1, ADC123_IN1 */
#define NOISE_PIN GPIO1
#define NOISE_CH 1
#define NOISE_NAME "PA1"
#define UART_DEV USART2           /* PA2 TX, PA3 RX */
#define UART_RCC RCC_USART2
#define UART_IRQ NVIC_USART2_IRQ
#define UART_ISR usart2_isr
#define UART_PORT GPIOA
#define UART_PINS (GPIO2 | GPIO3)
#define UART_NAME "PA2/PA3"
#define KS_SECTOR 11              /* the last 128 KB sector of 1 MB */
#define KS_ADDR 0x080E0000UL
#define BOARD_HAS_ACCEL 1         /* LIS3DSH or LIS302DL on SPI1 */

#endif

#endif
