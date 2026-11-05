/*
 * 128x64 monochrome OLED (SH1106 by default, SSD1306 selectable) on the
 * shared software I2C bus.
 *
 * Drawing happens in a RAM framebuffer; oled_flush() then sends only the
 * columns of each 8-pixel page that changed since the last flush, so a live
 * trace costs a few hundred bytes of I2C traffic per frame instead of 1 KB.
 */
#ifndef ZIRAH_OLED_H
#define ZIRAH_OLED_H

#include <stdbool.h>
#include <stdint.h>

#define OLED_W 128
#define OLED_H 64

bool oled_init(void);          /* false if no display answers */
bool oled_present(void);
void oled_clear(void);
void oled_flush(void);
void oled_invalidate(void);    /* force the next flush to resend everything */

/* Pixel graphics. Coordinates outside the screen are clipped. */
void oled_pixel(int x, int y, bool on);
void oled_hline(int x0, int x1, int y, bool on);
void oled_vline(int x, int y0, int y1, bool on);
void oled_line(int x0, int y0, int x1, int y1, bool on);
void oled_rect(int x, int y, int w, int h, bool fill, bool on);
void oled_invert_rect(int x, int y, int w, int h);
/* 1-bit bitmap, rows top to bottom, each row MSB-first, (w+7)/8 bytes. */
void oled_bitmap(int x, int y, int w, int h, const uint8_t *bits);

/* 5x7 text at any pixel position; scale 1 or 2. Returns the end x. */
int oled_text_at(int x, int y, const char *s, int scale, bool on);
int oled_text_width(const char *s, int scale);

/* Page-aligned text rows (row 0..7), kept for simple screens. */
void oled_text(uint8_t row, const char *s);
void oled_text_inv(uint8_t row, const char *s);

/* Read-only view of the framebuffer (page-major), for host-side previews. */
const uint8_t *oled_framebuffer(void);

#endif
