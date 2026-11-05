/*
 * Zirah OLED interface: pure rendering of a view description into the OLED
 * framebuffer. No I/O, no timing and no access to secrets, so it can never
 * interfere with signing, and the very same code is compiled on a PC to
 * produce exact screen previews (tests/native/oled_preview.c).
 *
 * Honesty rule for every screen: graphics only show measured values or real
 * firmware events. The signal window draws the real raw ADC samples of the
 * idle monitor; with no healthy samples it says NO SIGNAL instead of
 * animating anything.
 */
#ifndef ZIRAH_OLED_UI_H
#define ZIRAH_OLED_UI_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    UI_BOOT,     /* logo while the source settles             */
    UI_DASH,     /* idle: live signal, key fingerprints        */
    UI_CONFIRM,  /* waiting for the USER button               */
    UI_WORK,     /* operation in progress, real stage events   */
    UI_DONE,     /* result of the last operation              */
    UI_FAULT,    /* entropy or device fault                    */
    UI_VBIAS,    /* boost voltage while tuning                 */
    UI_ANALYZE,  /* histogram of a real sample block           */
    UI_CAPTURE,  /* raw capture progress                       */
    UI_MESSAGE,  /* short status (key generation, setup)       */
} ui_mode_t;

/* Firmware stages, in the order they run. Not every operation uses all. */
typedef enum {
    ST_CONFIRM = 0,
    ST_ENTROPY,
    ST_REFRESH,
    ST_SIGN,
    ST_VERIFY,
    ST_ECDSA,
    ST_COUNT
} ui_stage_t;

#define STAGE_BIT(s) (1u << (s))

typedef struct {
    ui_mode_t mode;
    const char *state;      /* header word on the right: READY, NO KEY, ... */
    const char *title;      /* header word on the left for non-dash screens */
    bool confirm_off;       /* button confirmation disabled: show a warning */

    /* Live signal: raw 12-bit samples of the idle monitor (real data only). */
    const uint16_t *samples;
    uint16_t nsamples;
    bool signal_live;

    /* Identity and health. Empty strings mean "none". */
    char pq_fp[9];
    char ec_fp[9];
    uint32_t vbias_mv;
    uint32_t sigs;
    const char *trng;

    /* Operation progress. */
    uint8_t stages;         /* STAGE_BIT mask of stages this operation uses */
    int8_t stage;           /* running stage, -1 for none                 */
    uint8_t done;           /* STAGE_BIT mask of finished stages          */
    int8_t failed;          /* failed stage, -1 for none                  */
    char digest8[9];        /* first 4 bytes of the manifest digest, or a */
                            /* short word on confirm screens               */
    const char *digest_label; /* "doc" or "key" on the result screen     */
    uint32_t left_ms, total_ms;
    uint32_t elapsed_ms;
    bool ok_pq, ok_ec, ec_used;

    const char *line1, *line2, *line3;

    /* Analyze. */
    const uint32_t *hist;   /* 16 bins */
    int32_t std_x10, mean;
    uint32_t clipped;
    int32_t mcv_x100;

    /* Generic progress (capture, setup). */
    uint32_t num, den;
} ui_view_t;

void oled_ui_view_init(ui_view_t *v, ui_mode_t mode);
void oled_ui_render(const ui_view_t *v);

#endif
