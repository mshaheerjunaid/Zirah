#include "oled.h"
#include "swi2c.h"
#include "font5x7.h"
#include "zirah_config.h"

#include <string.h>

#define W OLED_W
#define PAGES (OLED_H / 8)

#if ZIRAH_OLED_SSD1306
#define COL_OFFSET 0
#else
#define COL_OFFSET 2 /* SH1106 RAM is 132 columns wide */
#endif

static uint8_t fb[PAGES][W];
static uint8_t shadow[PAGES][W];  /* what the panel currently shows */
static bool g_present;
static bool g_full;               /* resend every page on the next flush */

static bool cmd(const uint8_t *c, size_t n)
{
    return i2c_write_prefixed(ZIRAH_OLED_ADDR, 0x00, c, n);
}

bool oled_present(void)
{
    return g_present;
}

bool oled_init(void)
{
    g_present = i2c_probe(ZIRAH_OLED_ADDR);
    if (!g_present) {
        return false;
    }
#if ZIRAH_OLED_SSD1306
    static const uint8_t init[] = {
        0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
        0x8D, 0x14,             /* charge pump on */
        0x20, 0x02,             /* page addressing */
        0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
        0xDB, 0x40, 0xA4, 0xA6, 0xAF,
    };
#else
    static const uint8_t init[] = {
        0xAE,             /* display off */
        0xD5, 0x80,       /* clock divide */
        0xA8, 0x3F,       /* multiplex 64 */
        0xD3, 0x00,       /* display offset */
        0x40,             /* start line 0 */
        0xAD, 0x8B,       /* DC-DC on (SH1106) */
        0xA1,             /* segment remap */
        0xC8,             /* COM scan direction */
        0xDA, 0x12,       /* COM pins */
        0x81, 0x80,       /* contrast */
        0xD9, 0x22,       /* pre-charge */
        0xDB, 0x35,       /* VCOMH */
        0xA4,             /* follow RAM */
        0xA6,             /* normal (not inverted) */
        0xAF,             /* display on */
    };
#endif
    g_present = cmd(init, sizeof(init));
    oled_clear();
    g_full = true;
    oled_flush();
    return g_present;
}

void oled_clear(void)
{
    memset(fb, 0, sizeof(fb));
}

void oled_invalidate(void)
{
    g_full = true;
}

const uint8_t *oled_framebuffer(void)
{
    return &fb[0][0];
}

/* ------------------------------------------------------------------------ */
/* Primitives                                                                */
/* ------------------------------------------------------------------------ */

#ifdef ZIRAH_NATIVE_TEST
/* Native preview builds count pixels drawn off-screen, so a test can prove
 * that no text or graphic is clipped on any screen. */
unsigned long g_oled_clipped_px;
#endif

void oled_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= W || y < 0 || y >= OLED_H) {
#ifdef ZIRAH_NATIVE_TEST
        g_oled_clipped_px++;
#endif
        return;
    }
    uint8_t bit = (uint8_t)(1u << (y & 7));
    if (on) {
        fb[y >> 3][x] |= bit;
    } else {
        fb[y >> 3][x] &= (uint8_t)~bit;
    }
}

void oled_hline(int x0, int x1, int y, bool on)
{
    if (x0 > x1) {
        int t = x0; x0 = x1; x1 = t;
    }
    for (int x = x0; x <= x1; x++) {
        oled_pixel(x, y, on);
    }
}

void oled_vline(int x, int y0, int y1, bool on)
{
    if (y0 > y1) {
        int t = y0; y0 = y1; y1 = t;
    }
    for (int y = y0; y <= y1; y++) {
        oled_pixel(x, y, on);
    }
}

void oled_line(int x0, int y0, int x1, int y1, bool on)
{
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y0 - y1 : y1 - y0;
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    for (int guard = 0; guard < 512; guard++) {
        oled_pixel(x0, y0, on);
        if (x0 == x1 && y0 == y1) {
            break;
        }
        int e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
}

void oled_rect(int x, int y, int w, int h, bool fill, bool on)
{
    if (w <= 0 || h <= 0) {
        return;
    }
    if (fill) {
        for (int yy = y; yy < y + h; yy++) {
            oled_hline(x, x + w - 1, yy, on);
        }
    } else {
        oled_hline(x, x + w - 1, y, on);
        oled_hline(x, x + w - 1, y + h - 1, on);
        oled_vline(x, y, y + h - 1, on);
        oled_vline(x + w - 1, y, y + h - 1, on);
    }
}

void oled_invert_rect(int x, int y, int w, int h)
{
    for (int yy = y; yy < y + h; yy++) {
        for (int xx = x; xx < x + w; xx++) {
            if (xx < 0 || xx >= W || yy < 0 || yy >= OLED_H) {
                continue;
            }
            fb[yy >> 3][xx] ^= (uint8_t)(1u << (yy & 7));
        }
    }
}

void oled_bitmap(int x, int y, int w, int h, const uint8_t *bits)
{
    int stride = (w + 7) / 8;
    for (int r = 0; r < h; r++) {
        for (int c = 0; c < w; c++) {
            if (bits[r * stride + c / 8] & (0x80u >> (c & 7))) {
                oled_pixel(x + c, y + r, true);
            }
        }
    }
}

int oled_text_width(const char *s, int scale)
{
    int n = (int)strlen(s);
    return n ? n * 6 * scale - scale : 0;
}

int oled_text_at(int x, int y, const char *s, int scale, bool on)
{
    if (scale < 1) {
        scale = 1;
    }
    while (*s) {
        char ch = *s++;
        if (ch < FONT5X7_FIRST || ch > FONT5X7_LAST) {
            ch = '?';
        }
        const uint8_t *g = &font5x7[(ch - FONT5X7_FIRST) * 5];
        for (int col = 0; col < 5; col++) {
            for (int row = 0; row < 7; row++) {
                if (g[col] & (1u << row)) {
                    for (int dy = 0; dy < scale; dy++) {
                        for (int dx = 0; dx < scale; dx++) {
                            oled_pixel(x + col * scale + dx, y + row * scale + dy, on);
                        }
                    }
                }
            }
        }
        x += 6 * scale;
    }
    return x;
}

/* Page-aligned rows (used by the fault screen, which must be minimal). */
static void draw_row(uint8_t row, const char *s, bool inv)
{
    if (row >= PAGES) {
        return;
    }
    memset(fb[row], inv ? 0xFF : 0x00, W);
    oled_text_at(inv ? 1 : 0, row * 8, s, 1, !inv);
}

void oled_text(uint8_t row, const char *s)
{
    draw_row(row, s, false);
}

void oled_text_inv(uint8_t row, const char *s)
{
    draw_row(row, s, true);
}

/* ------------------------------------------------------------------------ */
/* Transfer: only the columns that changed                                    */
/* ------------------------------------------------------------------------ */

void oled_flush(void)
{
    if (!g_present) {
        return;
    }
    for (uint8_t p = 0; p < PAGES; p++) {
        int lo = 0, hi = W - 1;
        if (!g_full) {
            while (lo < W && fb[p][lo] == shadow[p][lo]) {
                lo++;
            }
            if (lo == W) {
                continue; /* page unchanged */
            }
            while (hi > lo && fb[p][hi] == shadow[p][hi]) {
                hi--;
            }
        }
        uint8_t col = (uint8_t)(COL_OFFSET + lo);
        uint8_t c[3] = {
            (uint8_t)(0xB0 | p),
            (uint8_t)(0x00 | (col & 0x0F)),
            (uint8_t)(0x10 | (col >> 4)),
        };
        if (!cmd(c, 3)) {
            g_full = true; /* panel state unknown: resend everything next time */
            return;
        }
        if (!i2c_write_prefixed(ZIRAH_OLED_ADDR, 0x40, &fb[p][lo], (size_t)(hi - lo + 1))) {
            g_full = true;
            return;
        }
        memcpy(&shadow[p][lo], &fb[p][lo], (size_t)(hi - lo + 1));
    }
    g_full = false;
}
