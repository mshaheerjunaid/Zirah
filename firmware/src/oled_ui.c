#include "oled_ui.h"
#include "oled.h"
#include "logo_bitmap.h"
#include "ui_text.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#define W OLED_W

static const char *const k_stage_long[ST_COUNT] = {
    "CONFIRM", "ENTROPY", "MASKS", "ML-DSA", "VERIFY", "ECDSA"};
static const char *const k_stage_short[ST_COUNT] = {
    "CNF", "ENT", "MSK", "SIG", "VFY", "ECC"};
static const char *const k_stage_desc[ST_COUNT] = {
    "Awaiting " ZIRAH_BTN, "SP 800-90B Test", "Refresh Key Shares",
    "Masked ML-DSA-65", "Self-Check Signature", "ATECC608 P-256"};

void oled_ui_view_init(ui_view_t *v, ui_mode_t mode)
{
    memset(v, 0, sizeof(*v));
    v->mode = mode;
    v->stage = -1;
    v->failed = -1;
    v->state = "";
    v->title = "";
    v->trng = "";
}

/* ------------------------------------------------------------------------ */
/* Small pieces                                                              */
/* ------------------------------------------------------------------------ */

static void text_center(int y, const char *s, int scale)
{
    oled_text_at((W - oled_text_width(s, scale)) / 2, y, s, scale, true);
}

static void text_right(int xr, int y, const char *s, bool on)
{
    oled_text_at(xr - oled_text_width(s, 1), y, s, 1, on);
}

/* The emblem in miniature: diamond outline with two loose points, the
 * avalanche break of the logo. Drawn in `on` colour at (x, y), 9x9. */
static void glyph_emblem(int x, int y, bool on)
{
    oled_line(x + 4, y + 0, x + 8, y + 4, on);
    oled_line(x + 8, y + 4, x + 6, y + 6, on);
    oled_line(x + 4, y + 8, x + 0, y + 4, on);
    oled_line(x + 0, y + 4, x + 4, y + 0, on);
    oled_line(x + 4, y + 8, x + 5, y + 7, on);
    oled_pixel(x + 7, y + 7, on);
    oled_pixel(x + 8, y + 8, on);
}

static void header(const char *left, const char *right, bool warn)
{
    oled_rect(0, 0, W, 9, true, true);
    glyph_emblem(2, 0, false);
    oled_text_at(14, 1, left, 1, false);
    int xr = W - 2;
    if (right && *right) {
        text_right(xr, 1, right, false);
        xr -= oled_text_width(right, 1) + 5;
    }
    if (warn) {
        /* Inverted "!" box: button confirmation is off. */
        oled_rect(xr - 7, 1, 7, 7, true, false);
        oled_vline(xr - 4, 2, 4, true);
        oled_pixel(xr - 4, 6, true);
    }
}

static void fmt_volts(char *dst, size_t n, uint32_t mv)
{
    snprintf(dst, n, "%lu.%02luV", (unsigned long)(mv / 1000u),
             (unsigned long)((mv % 1000u) / 10u));
}

static void progress_bar(int x, int y, int w, int h, uint32_t num, uint32_t den)
{
    oled_rect(x, y, w, h, false, true);
    if (den == 0) {
        return;
    }
    if (num > den) {
        num = den;
    }
    int fill = (int)(((uint64_t)(w - 4) * num) / den);
    if (fill > 0) {
        oled_rect(x + 2, y + 2, fill, h - 4, true, true);
    }
}

/* Dotted rectangle outline for stages that have not run yet. */
static void rect_dotted(int x, int y, int w, int h)
{
    for (int i = 0; i < w; i += 2) {
        oled_pixel(x + i, y, true);
        oled_pixel(x + i, y + h - 1, true);
    }
    for (int i = 0; i < h; i += 2) {
        oled_pixel(x, y + i, true);
        oled_pixel(x + w - 1, y + i, true);
    }
}

/* Oscilloscope-style corner brackets around a window. */
static void brackets(int x, int y, int w, int h)
{
    int r = x + w - 1, b = y + h - 1;
    oled_hline(x, x + 3, y, true);   oled_vline(x, y, y + 3, true);
    oled_hline(r - 3, r, y, true);   oled_vline(r, y, y + 3, true);
    oled_hline(x, x + 3, b, true);   oled_vline(x, b - 3, b, true);
    oled_hline(r - 3, r, b, true);   oled_vline(r, b - 3, b, true);
}

/* ------------------------------------------------------------------------ */
/* Live signal window                                                         */
/* ------------------------------------------------------------------------ */

static void signal_window(const ui_view_t *v, int x0, int y0, int w, int h)
{
    brackets(x0, y0, w, h);
    int yc = y0 + h / 2;
    int iw = w - 6, ix = x0 + 3;

    if (!v->signal_live || v->samples == 0 || v->nsamples < (uint16_t)iw) {
        for (int x = ix; x < ix + iw; x += 4) {
            oled_hline(x, x + 1, yc, true);
        }
        text_center(y0 + 4, "NO SIGNAL", 1);
        if (v->trng && *v->trng) {
            text_center(yc + 4, v->trng, 1);
        }
        return;
    }

    /* Statistics of the real block: centre on the mean, scale so that
     * +/- 3 standard deviations fill the window. */
    uint32_t n = v->nsamples;
    uint64_t sum = 0, sum2 = 0;
    bool clipped = false;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t s = v->samples[i] & 0x0FFFu;
        sum += s;
        sum2 += (uint64_t)s * s;
        if (s == 0 || s == 4095u) {
            clipped = true;
        }
    }
    float mean = (float)sum / (float)n;
    float var = (float)sum2 / (float)n - mean * mean;
    float sd = var > 1.0f ? sqrtf(var) : 1.0f;
    float k = (float)(h / 2 - 2) / (3.0f * sd);

    for (int x = ix; x < ix + iw; x += 3) {
        oled_pixel(x, yc, true); /* dotted mean line */
    }
    for (int c = 0; c < iw; c++) {
        uint32_t a = (uint32_t)c * n / (uint32_t)iw;
        uint32_t b = (uint32_t)(c + 1) * n / (uint32_t)iw;
        if (b <= a) {
            b = a + 1;
        }
        uint32_t lo = 4095u, hi = 0;
        for (uint32_t i = a; i < b && i < n; i++) {
            uint32_t s = v->samples[i] & 0x0FFFu;
            if (s < lo) lo = s;
            if (s > hi) hi = s;
        }
        int ytop = yc - (int)lroundf(((float)hi - mean) * k);
        int ybot = yc - (int)lroundf(((float)lo - mean) * k);
        if (ytop < y0 + 1) ytop = y0 + 1;
        if (ybot > y0 + h - 2) ybot = y0 + h - 2;
        if (ytop > ybot) ytop = ybot;
        oled_vline(ix + c, ytop, ybot, true);
    }
    if (clipped) {
        int tw = oled_text_width("CLIP", 1) + 2;
        oled_rect(x0 + w - tw - 2, y0 + 1, tw, 9, true, true);
        oled_text_at(x0 + w - tw - 1, y0 + 2, "CLIP", 1, false);
    }
}

/* ------------------------------------------------------------------------ */
/* Stage strip: one box per stage the operation uses                          */
/* ------------------------------------------------------------------------ */

static void stage_strip(const ui_view_t *v, int y, int h)
{
    int count = 0;
    for (int s = 0; s < ST_COUNT; s++) {
        if (v->stages & STAGE_BIT(s)) count++;
    }
    if (count == 0) {
        return;
    }
    int gap = 2;
    int bw = (W - gap * (count - 1)) / count;
    int x = (W - (bw * count + gap * (count - 1))) / 2;
    for (int s = 0; s < ST_COUNT; s++) {
        if (!(v->stages & STAGE_BIT(s))) {
            continue;
        }
        const char *lab = k_stage_short[s];
        int lx = x + (bw - oled_text_width(lab, 1)) / 2;
        int ly = y + (h - 7) / 2;
        if (v->failed == s) {
            oled_rect(x, y, bw, h, false, true);
            oled_line(x + 2, y + 2, x + bw - 3, y + h - 3, true);
            oled_line(x + 2, y + h - 3, x + bw - 3, y + 2, true);
        } else if (v->done & STAGE_BIT(s)) {
            oled_rect(x, y, bw, h, true, true);
            oled_text_at(lx, ly, lab, 1, false);
        } else if (v->stage == s) {
            oled_rect(x, y, bw, h, false, true);
            oled_rect(x + 1, y + 1, bw - 2, h - 2, false, true);
            oled_text_at(lx, ly, lab, 1, true);
        } else {
            rect_dotted(x, y, bw, h);
            oled_text_at(lx, ly, lab, 1, true);
        }
        x += bw + gap;
    }
}

/* ------------------------------------------------------------------------ */
/* Screens                                                                   */
/* ------------------------------------------------------------------------ */

static void screen_boot(const ui_view_t *v)
{
    oled_bitmap(2, 8, ZIRAH_LOGO_W, ZIRAH_LOGO_H, zirah_logo_bits);
    oled_text_at(56, 12, "ZIRAH", 2, true);
    oled_text_at(56, 32, "ML-DSA-65", 1, true);
    if (v->line1) oled_text_at(56, 44, v->line1, 1, true);
    if (v->line2) oled_text_at(56, 54, v->line2, 1, true);
}

static void screen_dash(const ui_view_t *v)
{
    char l[24], r[12];
    header("ZIRAH", v->state, v->confirm_off);
    signal_window(v, 0, 11, W, 32);

    snprintf(l, sizeof(l), "PQ %s", v->pq_fp[0] ? v->pq_fp : "No Key");
    oled_text_at(0, 46, l, 1, true);
    fmt_volts(r, sizeof(r), v->vbias_mv);
    text_right(W, 46, r, true);

    snprintf(l, sizeof(l), "EC %s", v->ec_fp[0] ? v->ec_fp : "--");
    oled_text_at(0, 56, l, 1, true);
    /* Capped so the counter can never run into the fingerprint. */
    if (v->sigs > 999999u) {
        snprintf(r, sizeof(r), "#>999999");
    } else {
        snprintf(r, sizeof(r), "#%lu", (unsigned long)v->sigs);
    }
    text_right(W, 56, r, true);
}

static bool is_hex8(const char *s)
{
    for (int i = 0; i < 8; i++) {
        char c = s[i];
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f'))) {
            return false;
        }
    }
    return s[8] == 0;
}

static void screen_confirm(const ui_view_t *v)
{
    char d[16], s[16];
    header(v->title && *v->title ? v->title : "SIGN?", ZIRAH_BTN, false);
    if (is_hex8(v->digest8)) {
        snprintf(d, sizeof(d), "%.4s %.4s", v->digest8, v->digest8 + 4);
    } else {
        snprintf(d, sizeof(d), "%s", v->digest8);
    }
    text_center(14, d, 2);
    text_center(33, v->line1 ? v->line1 : "Match the PC Screen", 1);
    text_center(43, v->line2 ? v->line2 : TXT_SIGN_L2, 1);
    uint32_t secs = (v->left_ms + 999u) / 1000u;
    snprintf(s, sizeof(s), "%lus", (unsigned long)secs);
    progress_bar(0, 54, 104, 8, v->left_ms, v->total_ms);
    text_right(W, 55, s, true);
}

static void screen_work(const ui_view_t *v)
{
    char e[12];
    snprintf(e, sizeof(e), "%lu.%lus", (unsigned long)(v->elapsed_ms / 1000u),
             (unsigned long)((v->elapsed_ms % 1000u) / 100u));
    header(v->title && *v->title ? v->title : "SIGNING", e, false);
    if (v->stage >= 0 && v->stage < ST_COUNT) {
        text_center(15, k_stage_long[v->stage], 2);
        text_center(34, k_stage_desc[v->stage], 1);
    }
    stage_strip(v, 46, 13);
}

static void glyph_check(int x, int y)
{
    for (int t = 0; t < 3; t++) {
        oled_line(x, y + 10 + t, x + 8, y + 18 + t, true);
        oled_line(x + 8, y + 18 + t, x + 24, y + t, true);
    }
}

static void glyph_warn(int x, int y)
{
    for (int t = 0; t < 2; t++) {
        oled_line(x + 16, y + t, x + 32 - t, y + 30, true);
        oled_line(x + 32 - t, y + 30 - t, x + t, y + 30 - t, true);
        oled_line(x + t, y + 30, x + 16, y + t, true);
    }
    oled_rect(x + 15, y + 9, 3, 12, true, true);
    oled_rect(x + 15, y + 24, 3, 3, true, true);
}

static void screen_done(const ui_view_t *v)
{
    char l[24];
    bool ok = v->ok_pq && (!v->ec_used || v->ok_ec);
    /* ATECC608 present but its half failed: the ML-DSA signature is still
     * valid and released, so this is a partial result, not a failure. */
    const char *verdict = ok ? "OK" : (v->ok_pq ? "PQ ONLY" : "FAIL");
    header(v->title && *v->title ? v->title : "SIGNED", verdict, false);
    if (ok) {
        glyph_check(6, 18);
    } else {
        glyph_warn(4, 16);
    }
    snprintf(l, sizeof(l), "%s %s", v->digest_label ? v->digest_label : "Doc", v->digest8);
    oled_text_at(42, 14, l, 1, true);
    snprintf(l, sizeof(l), "ML-DSA  %s", v->ok_pq ? "OK" : "FAIL");
    oled_text_at(42, 25, l, 1, true);
    snprintf(l, sizeof(l), "ECDSA   %s", !v->ec_used ? "--" : (v->ok_ec ? "OK" : "FAIL"));
    oled_text_at(42, 36, l, 1, true);
    snprintf(l, sizeof(l), "%lu ms", (unsigned long)v->elapsed_ms);
    oled_text_at(42, 47, l, 1, true);
    if (v->line1) {
        text_center(56, v->line1, 1);
    }
}

static void screen_fault(const ui_view_t *v)
{
    header(v->title && *v->title ? v->title : "FAULT", "", false);
    glyph_warn(2, 14);
    if (v->line1) oled_text_at(40, 14, v->line1, 1, true);
    if (v->line2) oled_text_at(40, 25, v->line2, 1, true);
    if (v->line3) oled_text_at(40, 36, v->line3, 1, true);
    text_center(55, "Signing Disabled", 1);
}

static void screen_vbias(const ui_view_t *v)
{
    char b[16];
    header("BOOST", "LIVE", false);
    fmt_volts(b, sizeof(b), v->vbias_mv);
    text_center(15, b, 2);
    /* 0..20 V scale, window markers at 9 V and 20 V, target at 12 V. */
    int x0 = 2, w = 124, y = 37;
    progress_bar(x0, y, w, 9, v->vbias_mv > 20000u ? 20000u : v->vbias_mv, 20000u);
    int x9 = x0 + (w * 9) / 20, x12 = x0 + (w * 12) / 20;
    oled_vline(x9, y - 3, y - 1, true);
    oled_line(x12 - 2, y - 4, x12, y - 2, true);
    oled_line(x12 + 2, y - 4, x12, y - 2, true);
    text_center(51, v->line1 ? v->line1 : "Target 12V, 9-20V OK", 1);
}

static void screen_analyze(const ui_view_t *v)
{
    char l[40];
    snprintf(l, sizeof(l), "%lu", (unsigned long)v->num);
    header("NOISE", l, false);
    uint32_t mx = 1;
    for (int i = 0; v->hist && i < 16; i++) {
        if (v->hist[i] > mx) mx = v->hist[i];
    }
    for (int i = 0; v->hist && i < 16; i++) {
        int bh = (int)((v->hist[i] * 30u) / mx);
        if (bh > 0) {
            oled_rect(i * 8 + 1, 42 - bh, 6, bh, true, true);
        }
    }
    oled_hline(0, W - 1, 43, true);
    snprintf(l, sizeof(l), "Std %ld.%ld Clip %lu", (long)(v->std_x10 / 10),
             (long)(v->std_x10 % 10), (unsigned long)v->clipped);
    oled_text_at(0, 47, l, 1, true);
    snprintf(l, sizeof(l), "Mean %ld MCV %ld.%02ld", (long)v->mean,
             (long)(v->mcv_x100 / 100), (long)(v->mcv_x100 % 100));
    oled_text_at(0, 56, l, 1, true);
}

static void screen_capture(const ui_view_t *v)
{
    char l[32];
    uint32_t pct = v->den ? (uint32_t)(((uint64_t)v->num * 100u) / v->den) : 0;
    snprintf(l, sizeof(l), "%lu%%", (unsigned long)pct);
    header("CAPTURE", l, false);
    text_center(18, "Streaming Raw Samples", 1);
    snprintf(l, sizeof(l), "%lu/%lu", (unsigned long)v->num, (unsigned long)v->den);
    text_center(29, l, 1);
    progress_bar(2, 41, 124, 9, v->num, v->den);
    text_center(55, "Keep USB Connected", 1);
}

static void screen_message(const ui_view_t *v)
{
    header(v->title ? v->title : "", v->state, v->confirm_off);
    if (v->line1) text_center(18, v->line1, 1);
    if (v->line2) text_center(30, v->line2, 1);
    if (v->line3) text_center(42, v->line3, 1);
    if (v->den) {
        progress_bar(2, 53, 124, 9, v->num, v->den);
    }
}

void oled_ui_render(const ui_view_t *v)
{
    oled_clear();
    switch (v->mode) {
    case UI_BOOT:    screen_boot(v); break;
    case UI_DASH:    screen_dash(v); break;
    case UI_CONFIRM: screen_confirm(v); break;
    case UI_WORK:    screen_work(v); break;
    case UI_DONE:    screen_done(v); break;
    case UI_FAULT:   screen_fault(v); break;
    case UI_VBIAS:   screen_vbias(v); break;
    case UI_ANALYZE: screen_analyze(v); break;
    case UI_CAPTURE: screen_capture(v); break;
    case UI_MESSAGE: screen_message(v); break;
    }
}
