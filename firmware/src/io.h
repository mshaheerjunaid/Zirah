/*
 * Host link. Zirah talks over two transports at once:
 *   - USB serial (CDC-ACM) on the Discovery's micro-USB connector, and
 *   - USART2 (PA2/PA3) for an external USB-TTL adapter.
 * Replies go to whichever transport delivered the most recent command.
 */
#ifndef ZIRAH_IO_H
#define ZIRAH_IO_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

void io_init(void);

/* Returns a complete command line (without CR/LF) or NULL if none yet. */
const char *io_poll_line(void);

/* Returns true if a byte is waiting (used to abort long operations). */
bool io_rx_pending(void);

void io_putc(char c);
void io_puts(const char *s);
void io_write(const uint8_t *buf, size_t len);   /* raw binary */
void io_hex(const uint8_t *buf, size_t len);     /* lowercase hex */
void io_u32(uint32_t v);
void io_i32(int32_t v);
void io_line(const char *s);                     /* s + "\n" */
void io_kv_hex(const char *key, const uint8_t *buf, size_t len);
void io_flush(void);

/* Send the same banner on both transports (used at boot). */
void io_broadcast(const char *s);

bool io_usb_configured(void);

#endif
