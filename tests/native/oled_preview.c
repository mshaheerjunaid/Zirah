/*
 * Renders every OLED screen with the real firmware drawing code (oled.c +
 * oled_ui.c), using the same field values and strings that main.c sets, and
 * writes the raw 128x64 framebuffers, one file per screen.
 * tests/native/render_oled.py turns them into PNG previews.
 *
 * It is also a test: oled.c is built with ZIRAH_NATIVE_TEST, which counts
 * pixels drawn outside the panel. Any screen that clips text or graphics
 * makes this program exit with status 1.
 *
 * The noise samples used here are SIMULATED (a fixed pseudo-random Gaussian),
 * so the previews show layout, not measured data.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "oled.h"
#include "oled_ui.h"
#include "ui_text.h"
#include "zirah_config.h"

extern unsigned long g_oled_clipped_px;

static uint16_t sim[512];
static int g_failures;
static int g_count;

static void simulate(double sd)
{
    unsigned s = 12345u;
    for (int i = 0; i < 512; i++) {
        s = s * 1103515245u + 12345u;
        double u1 = ((s >> 8) + 1) / 16777217.0;
        s = s * 1103515245u + 12345u;
        double u2 = ((s >> 8) + 1) / 16777217.0;
        double g = sqrt(-2 * log(u1)) * cos(6.283185307 * u2);
        int v = (int)lround(2048 + sd * g);
        sim[i] = (uint16_t)(v < 0 ? 0 : v > 4095 ? 4095 : v);
    }
}

static void emit(const char *dir, const char *name, const ui_view_t *v)
{
    g_oled_clipped_px = 0;
    oled_ui_render(v);
    g_count++;
    if (g_oled_clipped_px) {
        fprintf(stderr, "FAIL %s: %lu pixels drawn off-screen (text or graphic clipped)\n", name,
                g_oled_clipped_px);
        g_failures++;
    }
    if (!dir) {
        return;
    }
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.fb", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) {
        perror(path);
        exit(2);
    }
    fwrite(oled_framebuffer(), 1, 1024, f);
    fclose(f);
}

static void dash(ui_view_t *v, const char *state)
{
    oled_ui_view_init(v, UI_DASH);
    v->state = state;
    v->samples = sim;
    v->nsamples = 512;
    v->signal_live = true;
    strcpy(v->pq_fp, "1A2B3C4D");
    strcpy(v->ec_fp, "9F3E21AB");
    v->vbias_mv = 12070;
    v->sigs = 12;
    v->trng = TXT_TRNG_OK;
}

static void fault(ui_view_t *v, const char *l2, const char *l3)
{
    /* main.c fault_lines() */
    oled_ui_view_init(v, UI_FAULT);
    v->line1 = TXT_FAULT_ENTROPY;
    v->line2 = l2;
    v->line3 = l3;
}

static void confirm(ui_view_t *v, const char *title, const char *big, const char *l1,
                    const char *l2, uint32_t left)
{
    /* main.c confirm_press() */
    oled_ui_view_init(v, UI_CONFIRM);
    v->title = title;
    size_t n = strlen(big);
    memcpy(v->digest8, big, n < 8 ? n + 1 : 9);
    v->digest8[8] = 0;
    v->line1 = l1;
    v->line2 = l2;
    v->total_ms = 30000;
    v->left_ms = left;
}

static void work(ui_view_t *v, const char *title, uint8_t stages, int stage, uint8_t done,
                 int failed, uint32_t ms)
{
    /* main.c op_begin() / op_stage() / op_fail() */
    oled_ui_view_init(v, UI_WORK);
    v->title = title;
    v->stages = stages;
    v->stage = (int8_t)stage;
    v->done = done;
    v->failed = (int8_t)failed;
    v->elapsed_ms = ms;
}

static void done(ui_view_t *v, const char *title, const char *label, const char *d, bool ec_used,
                 bool ok_ec, const char *note, uint32_t ms)
{
    /* main.c op_done() */
    oled_ui_view_init(v, UI_DONE);
    v->title = title;
    v->digest_label = label;
    strcpy(v->digest8, d);
    v->ok_pq = true;
    v->ec_used = ec_used;
    v->ok_ec = ok_ec;
    v->elapsed_ms = ms;
    v->line1 = note;
}

static void message(ui_view_t *v, const char *title, const char *l1, const char *l2,
                    const char *l3)
{
    /* main.c ui_message() */
    oled_ui_view_init(v, UI_MESSAGE);
    v->title = title;
    v->line1 = l1;
    v->line2 = l2;
    v->line3 = l3;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : NULL;
    ui_view_t v;
    oled_init();
    simulate(210.0);

    const uint8_t SIGN_ALL = STAGE_BIT(ST_CONFIRM) | STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) |
                             STAGE_BIT(ST_SIGN) | STAGE_BIT(ST_VERIFY) | STAGE_BIT(ST_ECDSA);
    const uint8_t KEYGEN = STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) | STAGE_BIT(ST_SIGN) |
                           STAGE_BIT(ST_VERIFY);

    /* Boot (main.c main()). */
    oled_ui_view_init(&v, UI_BOOT);
    v.line1 = TXT_BOOT_SELFTEST;
    v.line2 = TXT_BOOT_FW_PREFIX ZIRAH_FW_VERSION;
    emit(dir, "01_boot", &v);
    v.line1 = TXT_BOOT_SETTLING;
    emit(dir, "02_boot_settling", &v);

    /* Idle dashboard (ui_idle()). */
    dash(&v, TXT_STATE_READY);
    emit(dir, "03_ready", &v);
    dash(&v, TXT_STATE_READY);
    v.confirm_off = true;
    v.sigs = 4294967295u; /* widest counter */
    emit(dir, "04_ready_confirm_off", &v);
    dash(&v, TXT_STATE_NOKEY);
    v.pq_fp[0] = 0;
    v.ec_fp[0] = 0;
    v.sigs = 0;
    emit(dir, "05_no_key", &v);
    dash(&v, TXT_STATE_LOCKED);
    emit(dir, "06_key_stored_locked", &v);
    simulate(900.0); /* very wide signal that clips at the rails */
    dash(&v, TXT_STATE_READY);
    emit(dir, "07_ready_clipping", &v);
    simulate(210.0);

    /* Faults (ui_idle() and fault_lines()). */
    fault(&v, TXT_TRNG_BIAS, TXT_HINT_BIAS);
    emit(dir, "08_fault_bias", &v);
    fault(&v, TXT_TRNG_STUCK, TXT_HINT_STUCK);
    emit(dir, "09_fault_stuck", &v);
    fault(&v, TXT_TRNG_RCT, TXT_HINT_HEALTH);
    emit(dir, "10_fault_health", &v);
    fault(&v, TXT_TRNG_NOT_STARTED, TXT_HINT_NOT_STARTED);
    emit(dir, "11_fault_not_started", &v);
    fault(&v, TXT_TRNG_B, TXT_HINT_B);
    emit(dir, "11b_fault_source_b", &v);
    fault(&v, TXT_TRNG_CORR, TXT_HINT_CORR);
    emit(dir, "11c_fault_correlated", &v);
    fault(&v, TXT_TRNG_SOURCE, TXT_HINT_STUCK);
    emit(dir, "11d_fault_no_live_noise", &v);
    oled_ui_view_init(&v, UI_FAULT);
    v.title = TXT_LOCKED_TITLE;
    v.line1 = TXT_LOCKED_L1;
    v.line2 = TXT_LOCKED_L2;
    v.line3 = TXT_POWER_CYCLE;
    emit(dir, "12_locked", &v);
    oled_ui_view_init(&v, UI_FAULT);
    v.title = TXT_CPU_FAULT;
    v.line1 = "CANARY";
    v.line2 = TXT_SECRETS_WIPED;
    v.line3 = TXT_POWER_CYCLE;
    emit(dir, "13_cpu_fault", &v);
    v.title = TXT_BUILD_FAULT;
    v.line1 = "LAYOUT";
    emit(dir, "14_build_fault", &v);

    /* Confirmations (confirm_press() callers). */
    confirm(&v, TXT_SIGN_TITLE, "7C41E09A", TXT_SIGN_L1, TXT_SIGN_L2, 21400);
    emit(dir, "15_confirm_sign", &v);
    confirm(&v, TXT_REPLACE_TITLE, TXT_REPLACE_BIG, TXT_REPLACE_L1, TXT_PRESS_USER, 30000);
    emit(dir, "16_confirm_keygen_force", &v);
    confirm(&v, TXT_NOBTN_TITLE, TXT_NOBTN_BIG, TXT_NOBTN_L1, TXT_PRESS_USER, 9000);
    emit(dir, "17_confirm_off", &v);
    confirm(&v, TXT_LOCK_TITLE, TXT_LOCK_BIG, TXT_LOCK_L1, TXT_PRESS_USER, 400);
    emit(dir, "18_confirm_provision", &v);

    /* Work stages (op_stage()), every stage of a full hybrid signature. */
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_ENTROPY, STAGE_BIT(ST_CONFIRM), -1, 3120);
    emit(dir, "19_sign_entropy", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_REFRESH, STAGE_BIT(ST_CONFIRM) | STAGE_BIT(ST_ENTROPY), -1,
         3190);
    emit(dir, "20_sign_refresh", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_SIGN,
         STAGE_BIT(ST_CONFIRM) | STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH), -1, 3240);
    emit(dir, "21_sign_mldsa", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_VERIFY,
         STAGE_BIT(ST_CONFIRM) | STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) |
             STAGE_BIT(ST_SIGN),
         -1, 4410);
    emit(dir, "22_sign_verify", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_ECDSA, SIGN_ALL & ~STAGE_BIT(ST_ECDSA), -1, 5020);
    emit(dir, "23_sign_ecdsa", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_VERIFY,
         STAGE_BIT(ST_CONFIRM) | STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) |
             STAGE_BIT(ST_SIGN),
         ST_VERIFY, 4500);
    emit(dir, "24_sign_failed_verify", &v);
    work(&v, TXT_OP_KEYGEN, KEYGEN, ST_SIGN, STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH), -1, 1800);
    emit(dir, "25_keygen_pct", &v);
    work(&v, TXT_OP_PROVING, KEYGEN, ST_REFRESH, STAGE_BIT(ST_ENTROPY), -1, 90);
    emit(dir, "26_prove", &v);
    work(&v, TXT_OP_SIGNING, SIGN_ALL, ST_ENTROPY, STAGE_BIT(ST_CONFIRM), -1, 99999999u);
    emit(dir, "27_sign_long_elapsed", &v);

    /* Results (op_done()). */
    done(&v, TXT_DONE_SIGNED, TXT_LABEL_DOC, "7C41E09A", true, true, TXT_NOTE_BOTH, 5240);
    emit(dir, "28_signed_hybrid", &v);
    done(&v, TXT_DONE_SIGNED, TXT_LABEL_DOC, "7C41E09A", false, false, TXT_NOTE_PQ_ONLY, 4480);
    emit(dir, "29_signed_pq_only", &v);
    done(&v, TXT_DONE_SIGNED, TXT_LABEL_DOC, "7C41E09A", true, false, TXT_NOTE_PQ_ONLY, 4480);
    emit(dir, "30_signed_ecdsa_failed", &v);
    done(&v, TXT_DONE_NEWKEY, TXT_LABEL_KEY, "1A2B3C4D", false, false, TXT_NOTE_CHECK_FP, 2210);
    emit(dir, "31_new_key", &v);

    /* Tools. */
    oled_ui_view_init(&v, UI_VBIAS);
    v.vbias_mv = 12070;
    v.line1 = TXT_VBIAS_TARGET;
    emit(dir, "32_boost", &v);
    v.vbias_mv = 25000;
    v.line1 = TXT_VBIAS_WATCH;
    emit(dir, "33_boost_watch_high", &v);

    uint32_t hist[16] = {3, 11, 38, 96, 201, 355, 512, 640, 655, 560, 402, 247, 121, 48, 14, 4};
    oled_ui_view_init(&v, UI_ANALYZE);
    v.hist = hist;
    v.std_x10 = 2124;
    v.mean = 2047;
    v.clipped = 0;
    v.mcv_x100 = 721;
    v.num = 4096;
    emit(dir, "34_analyze", &v);
    v.std_x10 = 99999;
    v.mean = 4095;
    v.clipped = 4096;
    v.mcv_x100 = 1200;
    emit(dir, "35_analyze_extremes", &v);

    oled_ui_view_init(&v, UI_CAPTURE);
    v.num = 421888;
    v.den = 1000000;
    emit(dir, "36_capture", &v);
    v.num = 19999999;
    v.den = 20000000;
    emit(dir, "37_capture_max", &v);

    message(&v, TXT_PROVISION_TITLE, TXT_PROVISION_L1, TXT_PROVISION_L2, NULL);
    emit(dir, "38_provision", &v);
    message(&v, TXT_SELFTEST_TITLE, TXT_SELFTEST_L1, TXT_SELFTEST_L2, TXT_SELFTEST_L3);
    emit(dir, "39_selftest_failed", &v);
    message(&v, TXT_BENCH_TITLE, TXT_BENCH_L1, TXT_BENCH_L2, NULL);
    emit(dir, "40_benchmark", &v);

    printf("rendered %d screens, %d clipped\n", g_count, g_failures);
    return g_failures ? 1 : 0;
}
