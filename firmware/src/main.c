/*
 * Zirah firmware: command interpreter, signing flow and OLED dashboard.
 *
 * Host protocol (version ZIRAH_PROTO_VERSION, see docs/PROTOCOL.md): one
 * printable-ASCII command per line. Every command ends with exactly one
 * terminal line, "OK" or "ERR <reason>". Data lines are "KEY value";
 * progress lines are "EVT <stage> [k=v ...]" and are sent as each stage of
 * an operation actually starts.
 *
 * Security rules enforced here (docs/DESIGN.md, "Firmware Hardening"):
 *   - Every private-key operation starts with a fresh SP 800-90B startup test.
 *   - Every ML-DSA signature is verified on the device before it is released;
 *     a failed self-check withholds it and locks signing until power-cycle.
 *   - Anything irreversible or security-relevant (signing, replacing the key,
 *     turning off button confirmation, locking the ATECC608) needs a physical
 *     press of the USER button; a held or stuck button is never consent.
 *   - The identity proof (PROVE) uses a separate FIPS 204 context and is rate
 *     limited, so it can never be turned into a file signature.
 */
#include <string.h>
#include <stdio.h>

#include <libopencm3/cm3/cortex.h>
#include <libopencm3/cm3/scb.h>

#include "board.h"
#include "io.h"
#include "swi2c.h"
#include "atecc.h"
#include "oled.h"
#include "oled_ui.h"
#include "trng.h"
#include "mcu_rng.h"
#include "jitter.h"
#include "accel.h"
#include "board_pins.h"
#include "keystore.h"
#include "secure.h"
#include "zirah_config.h"
#include "zirah_mldsa.h"
#include "kat_vectors.h"
#include "ui_text.h"

#include "api.h"
#include "sign.h"
#include "fips202.h"
#include "ntt.h"

/* Sizes follow the selected ML-DSA parameter set (params.h). */
#define PKB CRYPTO_PUBLICKEYBYTES
#define SKB CRYPTO_SECRETKEYBYTES
#define SIGB CRYPTO_BYTES

/* Peak stack is the masked signer (~30 KB at ML-DSA-65); demand headroom. */
#define ZIRAH_MIN_STACK (40u * 1024u)

/* ------------------------------------------------------------------------ */
/* Memory layout: big buffers live in the 64 KB CCM RAM (0x10000000).         */
/* ------------------------------------------------------------------------ */

typedef struct {
    zirah_msk_t msk; /* device key, masked */
    union {
        uint16_t raw[2][4096]; /* ANALYZE / RAW samples, sources A and B */
        zirah_work_t work;     /* key generation and verification       */
        int32_t xform[2][N];   /* BENCH: transform input and copy       */
    } u;
    uint16_t monitor[ZIRAH_MONITOR_SAMPLES];   /* idle signal block, source A */
    uint16_t monitor_b[ZIRAH_MONITOR_SAMPLES]; /* the same instants, source B */
    uint32_t bench[5][100];                    /* BENCH timings                */
} ccm_layout_t;

_Static_assert(sizeof(ccm_layout_t) <= 64u * 1024u, "CCM layout too large");
#define CCM ((ccm_layout_t *)0x10000000UL)

static uint8_t g_pk[PKB];
static bool g_have_key;
static uint8_t g_sig[SIGB];
static uint8_t g_tmp_sk[SKB];
static uint8_t g_tmp_pk[PKB];
static uint8_t g_uid[12];

static atecc_status_t g_ecc;
static bool g_ecc_ready;
static uint8_t g_eccpk[64];
static bool g_have_eccpk;

static bool g_confirm = ZIRAH_CONFIRM_DEFAULT; /* never persisted: ON at boot */
static bool g_locked;                           /* self-check failed          */
static bool g_kat_ok;
static uint32_t g_sig_count;
static uint32_t g_prove_count, g_prove_last;

static uint16_t g_provision_code;
static uint32_t g_provision_ms;

/* Idle monitor. */
static uint32_t g_mon_seq, g_mon_blocks, g_mon_next, g_fault_retry;
static bool g_mon_valid;
static int32_t g_mon_mean, g_mon_std_x10;
static uint16_t g_mon_min, g_mon_max;
static uint32_t g_mon_clipped;
static int32_t g_mon_b_mean, g_mon_b_std_x10, g_mon_corr_x1000;
static uint32_t g_mon_b_clipped;
static uint32_t g_hold_until; /* keep a result screen up until this time */

/* SHAKE256 (first 16 bytes) of the firmware image in flash, computed at boot.
 * It equals SHAKE256 of the zirah.bin that was flashed, so the PC can tell
 * whether the board runs the build it ships with. */
static uint8_t g_fw_hash[16];

static int rng_cb(uint8_t *out, size_t n)
{
    return trng_get(out, n);
}

/* ------------------------------------------------------------------------ */
/* Small helpers                                                             */
/* ------------------------------------------------------------------------ */

static void fp4(const uint8_t *data, size_t len, uint8_t out[4])
{
    shake256(out, 4, data, len);
}

static void hex8(char *dst, const uint8_t b[4])
{
    static const char hx[] = "0123456789ABCDEF";
    for (int i = 0; i < 4; i++) {
        dst[2 * i] = hx[b[i] >> 4];
        dst[2 * i + 1] = hx[b[i] & 15];
    }
    dst[8] = 0;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Exactly 2n hex digits followed by the end of the argument. */
static bool parse_hex(const char *s, uint8_t *out, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int hi = hexval(s[2 * i]);
        if (hi < 0) return false;
        int lo = hexval(s[2 * i + 1]);
        if (lo < 0) return false;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return s[2 * n] == 0 || s[2 * n] == ' ';
}

/* Decimal number without overflow; rejects values that do not fit. */
static uint32_t parse_u32(const char *s, bool *ok)
{
    uint32_t v = 0;
    *ok = false;
    while (*s == ' ') s++;
    if (*s < '0' || *s > '9') return 0;
    while (*s >= '0' && *s <= '9') {
        uint32_t d = (uint32_t)(*s - '0');
        if (v > (0xFFFFFFFFu - d) / 10u) {
            return 0; /* overflow */
        }
        v = v * 10u + d;
        s++;
    }
    *ok = (*s == 0 || *s == ' ');
    return v;
}

static const char *next_tok(const char *s)
{
    while (*s && *s != ' ') s++;
    while (*s == ' ') s++;
    return s;
}

static bool tok_eq(const char *s, const char *kw)
{
    size_t n = strlen(kw);
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (c >= 'a' && c <= 'z') c = (char)(c - 32);
        if (c != kw[i]) return false;
    }
    return s[n] == 0 || s[n] == ' ';
}

static void ok(void)
{
    io_line("OK");
    io_flush();
}

static void err(const char *why)
{
    io_puts("ERR ");
    io_line(why);
    io_flush();
}

/* ATECC608 failures carry the ATECC code so the host can tell them apart. */
static void err_atecc(int rc)
{
    io_puts("ERR ATECC ");
    io_puts(atecc_strerror(rc));
    if (rc == ATECC_ERR_NOWAKE) {
        io_puts(" ");
        io_puts(atecc_wake_trace());
    }
    io_line("");
    io_flush();
}

static void leds_idle(void)
{
    if (g_locked || trng_state() != TRNG_OK) {
        led_only(LED_RED);
    } else if (g_have_key) {
        led_only(LED_GREEN);
    } else {
        led_only(LED_ORANGE);
    }
}

/* ------------------------------------------------------------------------ */
/* OLED                                                                      */
/* ------------------------------------------------------------------------ */

static void show(const ui_view_t *v)
{
    oled_ui_render(v);
    oled_flush();
}

/* Readable entropy-source state for the OLED (the protocol keeps the
 * machine tokens from trng_state_str()). */
static const char *trng_label(trng_state_t s)
{
    switch (s) {
    case TRNG_OK:          return TXT_TRNG_OK;
    case TRNG_FAULT_BIAS:  return TXT_TRNG_BIAS;
    case TRNG_FAULT_STUCK: return TXT_TRNG_STUCK;
    case TRNG_FAULT_RCT:   return TXT_TRNG_RCT;
    case TRNG_FAULT_APT:   return TXT_TRNG_APT;
    case TRNG_FAULT_B:     return TXT_TRNG_B;
    case TRNG_FAULT_CORR:  return TXT_TRNG_CORR;
    case TRNG_FAULT_SOURCE: return TXT_TRNG_SOURCE;
    default:               return TXT_TRNG_NOT_STARTED;
    }
}

static void fill_identity(ui_view_t *v)
{
    uint8_t f[4];
    if (g_have_key) {
        fp4(g_pk, PKB, f);
        hex8(v->pq_fp, f);
    }
    if (g_ecc_ready && g_have_eccpk) {
        fp4(g_eccpk, 64, f);
        hex8(v->ec_fp, f);
    }
    v->vbias_mv = adc_vbias_last_mv();
    v->sigs = g_sig_count;
    v->trng = trng_label(trng_state());
    v->confirm_off = !g_confirm;
}

static void fault_lines(ui_view_t *v)
{
    v->line1 = TXT_FAULT_ENTROPY;
    v->line2 = trng_label(trng_state());
    switch (trng_state()) {
    case TRNG_FAULT_BIAS:  v->line3 = TXT_HINT_BIAS; break;
    case TRNG_FAULT_STUCK:
    case TRNG_FAULT_SOURCE: v->line3 = TXT_HINT_STUCK; break;
    case TRNG_FAULT_RCT:
    case TRNG_FAULT_APT:   v->line3 = TXT_HINT_HEALTH; break;
    case TRNG_FAULT_B:     v->line3 = TXT_HINT_B; break;
    case TRNG_FAULT_CORR:  v->line3 = TXT_HINT_CORR; break;
    default:               v->line3 = TXT_HINT_NOT_STARTED; break;
    }
}

/* The idle screen: live signal when healthy, the fault otherwise. */
static void ui_idle(void)
{
    ui_view_t v;
    if (g_locked) {
        oled_ui_view_init(&v, UI_FAULT);
        v.title = TXT_LOCKED_TITLE;
        v.line1 = TXT_LOCKED_L1;
        v.line2 = TXT_LOCKED_L2;
        v.line3 = TXT_POWER_CYCLE;
        show(&v);
        return;
    }
    if (trng_state() != TRNG_OK) {
        oled_ui_view_init(&v, UI_FAULT);
        fault_lines(&v);
        show(&v);
        return;
    }
    oled_ui_view_init(&v, UI_DASH);
    fill_identity(&v);
    v.state = g_have_key ? TXT_STATE_READY
                         : (keystore_present() ? TXT_STATE_LOCKED : TXT_STATE_NOKEY);
    v.samples = CCM->monitor;
    v.nsamples = g_mon_valid ? ZIRAH_MONITOR_SAMPLES : 0;
    v.signal_live = g_mon_valid;
    show(&v);
}

static void ui_message(const char *title, const char *l1, const char *l2, const char *l3)
{
    ui_view_t v;
    oled_ui_view_init(&v, UI_MESSAGE);
    v.title = title;
    v.line1 = l1;
    v.line2 = l2;
    v.line3 = l3;
    v.confirm_off = !g_confirm;
    show(&v);
}

/* Draw the fault screen (called from the fault path with interrupts off, so
 * nothing here may depend on SysTick). */
void zirah_fault_display(uint32_t code, uint32_t cfsr)
{
    static const char *const names[] = {"?",     "HARD",   "MEMORY", "BUS",
                                        "USAGE", "CANARY", "LAYOUT", "RAM", "RNG"};
    (void)cfsr;
    i2c_bus_recover();
    oled_invalidate();
    ui_view_t v;
    oled_ui_view_init(&v, UI_FAULT);
    v.title = code <= ZIRAH_FAULT_CANARY ? TXT_CPU_FAULT
              : code == ZIRAH_FAULT_RNG  ? TXT_RNG_FAULT
                                         : TXT_BUILD_FAULT;
    v.line1 = names[code < sizeof(names) / sizeof(names[0]) ? code : 0];
    v.line2 = TXT_SECRETS_WIPED;
    v.line3 = TXT_POWER_CYCLE;
    show(&v);
}

static void wipe_outside_ccm(void)
{
    zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
    trng_clear_extra_input();
}

/* Random bytes from two generators that are independent of the avalanche
 * source, the ATECC608's and the STM32's own, absorbed as additional input
 * by the TRNG conditioning (trng_get) for the operation that follows. They
 * are credited zero entropy: the avalanche source alone must still pass its
 * health tests and supply the full assumed entropy, so a missing or faulty
 * generator cannot weaken the output, while a weakness in the avalanche
 * source that the health tests miss is covered by generators an attacker
 * would also have to predict. Returns MIX_ECC and MIX_MCU for the ones that
 * contributed. The caller clears the input with trng_clear_extra_input()
 * when the operation ends. */
#define MIX_ECC 1u
#define MIX_MCU 2u
static uint32_t g_jit_mark, g_acc_mark; /* block counts when the operation started */

/* Whether the clock jitter and the accelerometer went into the conditioner
 * since the last mix_extra_random(), that is, during the current operation. */
static bool jit_mixed(void)
{
    return trng_jitter_blocks() != g_jit_mark;
}

static bool acc_mixed(void)
{
    return trng_accel_blocks() != g_acc_mark;
}

static unsigned mix_extra_random(void)
{
    uint8_t er[64];
    size_t len = 0;
    unsigned mixed = 0;
    g_jit_mark = trng_jitter_blocks();
    g_acc_mark = trng_accel_blocks();
    trng_clear_extra_input();
    if (g_ecc_ready && atecc_random(er) == ATECC_OK) {
        len = 32;
        mixed |= MIX_ECC;
    }
    if (mcu_rng_get(er + len, 32) == 0) {
        len += 32;
        mixed |= MIX_MCU;
    }
    if (len) {
        trng_set_extra_input(er, len);
    }
    zirah_memzero(er, sizeof(er));
    return mixed;
}

/* ------------------------------------------------------------------------ */
/* Operation progress: one EVT line and one OLED update per real stage       */
/* ------------------------------------------------------------------------ */

static const char *const k_evt[ST_COUNT] = {
    "CONFIRM", "ENTROPY", "REFRESH", "SIGN", "VERIFY", "ECDSA"};

static ui_view_t g_op;
static uint32_t g_op_t0;

static void op_begin(const char *title, uint8_t stages)
{
    oled_ui_view_init(&g_op, UI_WORK);
    g_op.title = title;
    g_op.stages = stages;
    g_op_t0 = millis();
    led_only(LED_BLUE);
}

static void op_stage(ui_stage_t s)
{
    if (g_op.stage >= 0) {
        g_op.done |= (uint8_t)STAGE_BIT(g_op.stage);
    }
    g_op.stage = (int8_t)s;
    g_op.elapsed_ms = millis() - g_op_t0;
    if (s != ST_CONFIRM) {
        io_puts("EVT ");
        io_line(k_evt[s]);
        io_flush();
        g_op.mode = UI_WORK;
        show(&g_op);
    }
}

static void op_fail(void)
{
    g_op.failed = g_op.stage;
    g_op.elapsed_ms = millis() - g_op_t0;
    g_op.mode = UI_WORK;
    show(&g_op);
    g_hold_until = millis() + 2500u;
    leds_idle();
}

static void op_done(const char *title, const char *label, const char *digest8, bool ok_pq,
                    bool ec_used, bool ok_ec, const char *note)
{
    ui_view_t v;
    oled_ui_view_init(&v, UI_DONE);
    v.title = title;
    v.digest_label = label;
    if (digest8) {
        memcpy(v.digest8, digest8, 9);
    }
    v.ok_pq = ok_pq;
    v.ec_used = ec_used;
    v.ok_ec = ok_ec;
    v.elapsed_ms = millis() - g_op_t0;
    v.line1 = note;
    show(&v);
    g_hold_until = millis() + 4000u;
    leds_idle();
}

/* ------------------------------------------------------------------------ */
/* Physical presence                                                         */
/* ------------------------------------------------------------------------ */

typedef enum { PRESS_OK, PRESS_TIMEOUT, PRESS_CANCELLED, PRESS_STUCK } press_t;

/*
 * Wait for a deliberate press of the USER button. A button that is already
 * down when the request starts must be released first, and a press must be
 * released within ZIRAH_BUTTON_STUCK_MS: a held or shorted button is never
 * consent. Any byte from the host cancels.
 */
static press_t confirm_press(const char *title, const char *big, const char *l1,
                             const char *l2, const char *evt_args)
{
    ui_view_t v;
    oled_ui_view_init(&v, UI_CONFIRM);
    v.title = title;
    if (big) {
        memcpy(v.digest8, big, strlen(big) < 8 ? strlen(big) + 1 : 9);
        v.digest8[8] = 0;
    }
    v.line1 = l1;
    v.line2 = l2;
    v.total_ms = ZIRAH_CONFIRM_TIMEOUT_MS;
    v.left_ms = ZIRAH_CONFIRM_TIMEOUT_MS;

    char tmp[64];
    snprintf(tmp, sizeof(tmp), "EVT CONFIRM %s timeout_ms=%lu", evt_args,
             (unsigned long)ZIRAH_CONFIRM_TIMEOUT_MS);
    io_line(tmp);
    io_flush();
    show(&v);

    uint32_t t0 = millis();
    while (button_pressed()) {
        if ((uint32_t)(millis() - t0) > ZIRAH_BUTTON_STUCK_MS) {
            return PRESS_STUCK;
        }
        if (io_rx_pending()) {
            (void)io_poll_line();
            return PRESS_CANCELLED;
        }
    }
    t0 = millis();
    uint32_t last_draw = t0;
    for (;;) {
        uint32_t el = millis() - t0;
        if (el >= ZIRAH_CONFIRM_TIMEOUT_MS) {
            return PRESS_TIMEOUT;
        }
        led_set(LED_ORANGE, ((millis() / 250u) & 1u) != 0);
        if (button_pressed()) {
            delay_ms(30);
            if (button_pressed()) {
                uint32_t t1 = millis();
                while (button_pressed()) {
                    if ((uint32_t)(millis() - t1) > ZIRAH_BUTTON_STUCK_MS) {
                        return PRESS_STUCK;
                    }
                }
                return PRESS_OK;
            }
        }
        if (io_rx_pending()) {
            (void)io_poll_line();
            return PRESS_CANCELLED;
        }
        if ((uint32_t)(millis() - last_draw) >= 200u) {
            last_draw = millis();
            v.left_ms = ZIRAH_CONFIRM_TIMEOUT_MS - el;
            show(&v);
        }
    }
}

static const char *press_error(press_t p)
{
    switch (p) {
    case PRESS_TIMEOUT:   return "TIMEOUT no button press";
    case PRESS_CANCELLED: return "CANCELLED by host";
    case PRESS_STUCK:     return "BUTTON_STUCK release the " ZIRAH_BTN " button";
    default:              return "CANCELLED";
    }
}

/* ------------------------------------------------------------------------ */
/* Key handling                                                              */
/* ------------------------------------------------------------------------ */

static bool trng_ensure(void)
{
    if (trng_state() != TRNG_OK) {
        trng_startup();
    }
    return trng_state() == TRNG_OK;
}

static bool load_device_key(void)
{
    if (!keystore_load(g_pk, g_tmp_sk)) {
        g_have_key = false;
        return false;
    }
    if (!trng_ensure()) {
        /* Key exists but cannot be masked without healthy randomness. */
        zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
        g_have_key = false;
        return false;
    }
    int rc = zirah_msk_load(&CCM->msk, g_tmp_sk, rng_cb);
    zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
    g_have_key = (rc == 0);
    return g_have_key;
}

/* Make sure the stored key is loaded and masked; report why if it cannot be. */
static bool ensure_key(void)
{
    if (g_locked) {
        err("LOCKED a signature self-check failed; power-cycle the device");
        return false;
    }
    if (!g_have_key && keystore_present() && trng_ensure()) {
        (void)load_device_key();
    }
    if (!g_have_key) {
        err(keystore_present() ? "NO_ENTROPY key stored but entropy source unhealthy (run HEALTH)"
                               : "NO_KEY run KEYGEN");
    }
    return g_have_key;
}

static void ecc_refresh(void)
{
    g_ecc_ready = false;
    g_have_eccpk = false;
    if (atecc_status(&g_ecc) != ATECC_OK) {
        memset(&g_ecc, 0, sizeof(g_ecc));
        return;
    }
    g_ecc_ready = atecc_ready(&g_ecc);
    if (g_ecc_ready && atecc_genkey_public(0, g_eccpk) == ATECC_OK) {
        g_have_eccpk = true;
    }
}

/* Fresh SP 800-90B startup test before every private-key operation. */
static bool entropy_fresh(void)
{
    trng_startup();
    return trng_state() == TRNG_OK;
}

/* ------------------------------------------------------------------------ */
/* Boot self-tests (independent known answers, see tests/native)              */
/* ------------------------------------------------------------------------ */

static keccak_state g_kat_prg;

static int kat_seed_rng(uint8_t *out, size_t n)
{
    if (n != 32) {
        return -1;
    }
    memcpy(out, k_kat_xi, 32);
    return 0;
}

static int kat_mask_rng(uint8_t *out, size_t n)
{
    shake256_squeeze(out, n, &g_kat_prg);
    return 0;
}

/* The ML-DSA known answers are for ML-DSA-65; other builds test SHAKE256 only. */
#if DILITHIUM_MODE == 3
#define ZIRAH_HAVE_MLDSA_KAT 1
#endif

/* SHAKE256 vector, then ML-DSA keygen + masked sign + verify against
 * fingerprints computed independently with dilithium-py. */
static bool boot_kat(void)
{
    uint8_t out[32], f[16];
    shake256(out, 32, (const uint8_t *)ZIRAH_KAT_SHAKE_MSG, sizeof(ZIRAH_KAT_SHAKE_MSG) - 1);
    if (!zirah_ct_equal(out, k_kat_shake, 32)) {
        return false;
    }
#ifdef ZIRAH_HAVE_MLDSA_KAT
    bool good = zirah_keypair(g_tmp_pk, g_tmp_sk, &CCM->u.work, kat_seed_rng) == 0;
    shake256(f, 16, g_tmp_pk, PKB);
    good = good && zirah_ct_equal(f, k_kat_pk_fp, 16);
    shake256_init(&g_kat_prg);
    shake256_absorb(&g_kat_prg, (const uint8_t *)ZIRAH_KAT_MASK_SEED,
                    sizeof(ZIRAH_KAT_MASK_SEED) - 1);
    shake256_finalize(&g_kat_prg);
    good = good && zirah_msk_load(&CCM->msk, g_tmp_sk, kat_mask_rng) == 0;
    uint8_t rnd[32] = {0};
    size_t siglen = 0;
    if (good) {
        const uint8_t *m = (const uint8_t *)ZIRAH_KAT_MSG;
        const uint8_t *c = (const uint8_t *)ZIRAH_KAT_CTX;
        zirah_sign_ctx(g_sig, &siglen, m, sizeof(ZIRAH_KAT_MSG) - 1, c,
                       sizeof(ZIRAH_KAT_CTX) - 1, rnd, &CCM->msk, NULL);
        shake256(f, 16, g_sig, siglen);
        good = siglen == SIGB && zirah_ct_equal(f, k_kat_sig_fp, 16) &&
               zirah_verify_ctx(g_sig, siglen, m, sizeof(ZIRAH_KAT_MSG) - 1, c,
                                sizeof(ZIRAH_KAT_CTX) - 1, g_tmp_pk, &CCM->u.work) == 0;
        zirah_memzero(g_sig, sizeof(g_sig));
    }
    zirah_msk_wipe(&CCM->msk);
    zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
    zirah_memzero(&g_kat_prg, sizeof(g_kat_prg));
    return good;
#else
    (void)f;
    return true;
#endif
}

/* ------------------------------------------------------------------------ */
/* Idle monitor                                                              */
/* ------------------------------------------------------------------------ */

static void monitor_tick(void)
{
    uint32_t now = millis();
    if (trng_state() != TRNG_OK && !g_locked) {
        /* Restart attempt every few seconds, so fixing the wiring brings the
         * source back without a command. A restart is a full startup test. */
        if ((int32_t)(now - g_fault_retry) >= 0) {
            g_fault_retry = now + 5000u;
            if (entropy_fresh() && !g_have_key) {
                (void)load_device_key();
            }
            leds_idle();
        }
    }
    (void)trng_monitor_block(CCM->monitor, CCM->monitor_b, ZIRAH_MONITOR_SAMPLES);
    if (++g_mon_blocks % ZIRAH_MONITOR_VBIAS_EVERY == 0) {
        (void)trng_check_bias();
    }
    uint16_t lo = 4095, hi = 0;
    uint32_t clip = 0;
    for (uint32_t i = 0; i < ZIRAH_MONITOR_SAMPLES; i++) {
        uint16_t s = (uint16_t)(CCM->monitor[i] & 0x0FFFu);
        if (s < lo) lo = s;
        if (s > hi) hi = s;
        if (s == 0 || s == 4095u) clip++;
    }
    int32_t mean_x10;
    trng_mean_std_x10(CCM->monitor, ZIRAH_MONITOR_SAMPLES, &mean_x10, &g_mon_std_x10);
    g_mon_mean = mean_x10 / 10;
    g_mon_min = lo;
    g_mon_max = hi;
    g_mon_clipped = clip;
    if (trng_b_present()) {
        uint32_t bclip = 0;
        for (uint32_t i = 0; i < ZIRAH_MONITOR_SAMPLES; i++) {
            uint16_t s = (uint16_t)(CCM->monitor_b[i] & 0x0FFFu);
            if (s == 0 || s == 4095u) bclip++;
        }
        trng_mean_std_x10(CCM->monitor_b, ZIRAH_MONITOR_SAMPLES, &mean_x10, &g_mon_b_std_x10);
        g_mon_b_mean = mean_x10 / 10;
        g_mon_b_clipped = bclip;
        /* Shown only: a running average over the last few blocks steadies the
         * number. The fault limit is applied in every startup test instead. */
        int32_t r = trng_corr_x1000(CCM->monitor, CCM->monitor_b, ZIRAH_MONITOR_SAMPLES);
        g_mon_corr_x1000 = g_mon_seq ? (3 * g_mon_corr_x1000 + r) / 4 : r;
    }
    g_mon_valid = trng_state() == TRNG_OK;
    g_mon_seq++;
    leds_idle();
    if ((int32_t)(now - g_hold_until) >= 0) {
        ui_idle();
    }
}

/* ------------------------------------------------------------------------ */
/* Commands                                                                  */
/* ------------------------------------------------------------------------ */

static void cmd_help(void)
{
    io_line("Zirah commands:");
    io_line("  PING                      check the link");
    io_line("  INFO                      device, key, entropy and ATECC608 status");
    io_line("  PEEK                      latest idle noise block (same data as the OLED)");
    io_line("  I2CSCAN                   list I2C addresses that answer");
    io_line("  VBIAS [WATCH]             boost/bias voltage (WATCH = live, any key stops)");
    io_line("  ANALYZE [n] [rate] [B]    noise statistics without an oscilloscope (B: source B)");
    io_line("  HEALTH                    rerun the SP 800-90B startup health test");
    io_line("  RAW <n> [rate] [B]        stream n raw 16-bit samples for NIST SP 800-90B");
    io_line("  KEYGEN [FORCE]            new ML-DSA-65 key (FORCE needs the USER button)");
    io_line("  PK | ECCPK                print the ML-DSA-65 / ATECC608 P-256 public key");
    io_line("  SIGN <64 hex digest>      hybrid signature (needs the USER button)");
    io_line("  PROVE <64 hex nonce>      identity proof, separate context, rate limited");
    io_line("  BENCH [n]                 time n masked signatures with a throwaway key");
    io_line("  JITTER                    measure the LSI clock jitter source");
    io_line("  ACCEL                     read the board accelerometer source");
    io_line("  CONFIRM ON|OFF            OFF needs the USER button; ON again at every boot");
    io_line("  ATECC INFO|CONFIG|RANDOM  inspect the secure element");
    io_line("  ATECC PROVISION [code]    PERMANENT one-time setup (code + USER button)");
    ok();
}

/* "SOURCE_B absent" or "SOURCE_B present std_x10=.. corr_x1000=.. fails=..". */
static void source_b_line(void)
{
    char tmp[96];
    if (!trng_b_present()) {
        io_line("SOURCE_B absent");
        return;
    }
    trng_health_t hb;
    trng_health_get_b(&hb);
    snprintf(tmp, sizeof(tmp), "SOURCE_B present std_x10=%ld corr_x1000=%ld symbols=%lu fails=%lu",
             (long)trng_b_std_x10(), (long)trng_corr_x1000_last(), (unsigned long)hb.symbols,
             (unsigned long)hb.failures);
    io_line(tmp);
}

static void cmd_info(void)
{
    char tmp[96];
    trng_health_t h;
    uint8_t f[4];

    io_line("FW " ZIRAH_FW_VERSION);
    snprintf(tmp, sizeof(tmp), "PROTO %u", (unsigned)ZIRAH_PROTO_VERSION);
    io_line(tmp);
    io_line("ALG ML-DSA-65 (FIPS 204), masked c*s1/c*s2, hybrid ECDSA P-256 via ATECC608");
    io_kv_hex("UID", g_uid, 12);
    io_kv_hex("FW_HASH", g_fw_hash, sizeof(g_fw_hash));
    io_puts("KAT ");
    io_puts(g_kat_ok ? "ok" : "failed");
#ifdef ZIRAH_HAVE_MLDSA_KAT
    io_line(" scope=shake256,ml-dsa-65");
#else
    io_line(" scope=shake256");
#endif
    snprintf(tmp, sizeof(tmp), "HARDENING mpu=%u canary=1 stack=%lu rdp=%u",
             board_mpu_enabled() ? 1u : 0u, (unsigned long)board_stack_bytes(),
             board_rdp_level());
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "VDD_MV %lu", (unsigned long)adc_vdd_mv());
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "VBIAS_MV %lu", (unsigned long)adc_vbias_last_mv());
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "TEMP_C10 %ld", (long)adc_temp_c10());
    io_line(tmp);
    io_puts("TRNG ");
    io_line(trng_state_str(trng_state()));
    trng_health_get(&h);
    snprintf(tmp, sizeof(tmp), "H_ASSUMED %u.%02u bits/symbol",
             ZIRAH_H_ASSUMED_X100 / 100, ZIRAH_H_ASSUMED_X100 % 100);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "HEALTH symbols=%lu rct_cut=%u apt_cut=%u fails=%lu fails_total=%lu",
             (unsigned long)h.symbols, h.rct_cutoff, h.apt_cutoff, (unsigned long)h.failures,
             (unsigned long)trng_failures_total());
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "MONITOR seq=%lu valid=%u std_x10=%ld",
             (unsigned long)g_mon_seq, g_mon_valid ? 1u : 0u, (long)g_mon_std_x10);
    io_line(tmp);
    source_b_line();
    io_puts("STM32_RNG ");
    io_line(mcu_rng_state_str());
    {
        uint32_t mean, distinct, spread, mode;
        jitter_probe_stats(&mean, &distinct, &spread, &mode);
        if (jitter_live()) {
            snprintf(tmp, sizeof(tmp), "JITTER live mean_ticks=%lu distinct=%lu spread=%lu mode=%lu of=%lu",
                     (unsigned long)mean, (unsigned long)distinct, (unsigned long)spread, (unsigned long)mode,
                     (unsigned long)JIT_PERIODS);
        } else {
            snprintf(tmp, sizeof(tmp), "JITTER %s", jitter_state_str());
        }
        io_line(tmp);
        uint32_t least;
        accel_probe_stats(&least);
        if (accel_live()) {
            snprintf(tmp, sizeof(tmp), "ACCEL live chip=%s least_distinct=%lu of=%lu", accel_chip(),
                     (unsigned long)least, (unsigned long)ACC_SAMPLES);
        } else {
            snprintf(tmp, sizeof(tmp), "ACCEL %s chip=%s", accel_state_str(), accel_chip());
        }
        io_line(tmp);
    }
    io_puts("KEY ");
    io_line(g_have_key ? "present" : (keystore_present() ? "stored" : "none"));
    if (g_have_key) {
        fp4(g_pk, PKB, f);
        io_kv_hex("PK_FP", f, 4);
        snprintf(tmp, sizeof(tmp), "MASK_REFRESHES %lu", (unsigned long)CCM->msk.refreshes);
        io_line(tmp);
    }
    io_puts("ATECC ");
    if (!g_ecc.present) {
        io_line("absent");
    } else {
        io_puts(g_ecc_ready ? "ready" : "not-provisioned");
        io_puts(" rev=");
        io_hex(g_ecc.revision, 4);
        io_puts(" config_locked=");
        io_puts(g_ecc.config_locked ? "1" : "0");
        io_puts(" data_locked=");
        io_line(g_ecc.data_locked ? "1" : "0");
        if (g_have_eccpk) {
            fp4(g_eccpk, 64, f);
            io_kv_hex("ECC_FP", f, 4);
        }
    }
    snprintf(tmp, sizeof(tmp), "BOARD %s noise=%s serial=%s hse_mhz=%lu", ZIRAH_BOARD_NAME, NOISE_NAME,
             UART_NAME, (unsigned long)board_hse_mhz());
    io_line(tmp);
    io_puts("OLED ");
    io_line(oled_present() ? "present" : "absent");
    io_puts("USB ");
    io_line(io_usb_configured() ? "configured" : "not-configured");
    io_puts("CONFIRM ");
    io_line(g_confirm ? "on" : "off");
    io_puts("SIGNING ");
    io_line(g_locked ? "locked" : "enabled");
    snprintf(tmp, sizeof(tmp), "PROVE_LEFT %lu",
             (unsigned long)(ZIRAH_PROVE_MAX_PER_BOOT - g_prove_count));
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "SIGS %lu", (unsigned long)g_sig_count);
    io_line(tmp);
    ok();
}

static void cmd_peek(void)
{
    char tmp[240];
    int len = snprintf(tmp, sizeof(tmp),
             "PEEK seq=%lu n=%u rate=%u valid=%u mean=%ld std_x10=%ld min=%u max=%u clipped=%lu trng=%s",
             (unsigned long)g_mon_seq, g_mon_seq ? (unsigned)ZIRAH_MONITOR_SAMPLES : 0u,
             (unsigned)ZIRAH_SAMPLE_RATE_HZ, g_mon_valid ? 1u : 0u, (long)g_mon_mean,
             (long)g_mon_std_x10, g_mon_min, g_mon_max, (unsigned long)g_mon_clipped,
             trng_state_str(trng_state()));
    if (trng_b_present() && len > 0 && (size_t)len < sizeof(tmp)) {
        snprintf(tmp + len, sizeof(tmp) - (size_t)len,
                 " b=1 b_mean=%ld b_std_x10=%ld b_clipped=%lu corr_x1000=%ld", (long)g_mon_b_mean,
                 (long)g_mon_b_std_x10, (unsigned long)g_mon_b_clipped, (long)g_mon_corr_x1000);
    }
    io_line(tmp);
    if (g_mon_seq) {
        static const char hx[] = "0123456789abcdef";
        io_puts("SAMPLES ");
        for (uint32_t i = 0; i < ZIRAH_MONITOR_SAMPLES; i++) {
            uint16_t s = (uint16_t)(CCM->monitor[i] & 0x0FFFu);
            io_putc(hx[(s >> 8) & 15]);
            io_putc(hx[(s >> 4) & 15]);
            io_putc(hx[s & 15]);
        }
        io_putc('\n');
    }
    ok();
}

static void cmd_i2cscan(void)
{
    char tmp[40];
    int found = 0;
    for (uint8_t a = 0x08; a < 0x78; a++) {
        if (i2c_probe(a)) {
            snprintf(tmp, sizeof(tmp), "I2C 0x%02X", a);
            io_line(tmp);
            found++;
        }
    }
    /* The ATECC608 only answers when awake. */
    if (atecc_wake_check() == ATECC_OK) {
        snprintf(tmp, sizeof(tmp), "I2C 0x%02X atecc-awake", ZIRAH_ATECC_ADDR);
        io_line(tmp);
        found++;
    }
    if (!found) {
        io_line("I2C none (check SDA=PC9, SCL=PA8, pull-ups, 3V and GND)");
    }
    ok();
}

static void cmd_vbias(const char *arg)
{
    char tmp[64];
    bool watch = tok_eq(arg, "WATCH");
    ui_view_t v;
    oled_ui_view_init(&v, UI_VBIAS);
    do {
        uint32_t vb = adc_vbias_mv();
        uint32_t vdd = adc_vdd_mv();
        snprintf(tmp, sizeof(tmp), "VBIAS_MV %lu VDD_MV %lu", (unsigned long)vb,
                 (unsigned long)vdd);
        io_line(tmp);
        v.vbias_mv = vb;
        v.line1 = watch ? TXT_VBIAS_WATCH : TXT_VBIAS_TARGET;
        show(&v);
        if (watch) {
            delay_ms(250);
        }
    } while (watch && !io_rx_pending());
    if (watch) {
        (void)io_poll_line();
    }
    g_hold_until = millis() + 3000u;
    ok();
}

/* "[n] [rate] [B]" for ANALYZE and RAW: up to two numbers in that order,
 * and B anywhere to choose source B. False after printing ERR. */
static bool parse_sample_args(const char *arg, uint32_t *n, uint32_t *rate, trng_src_t *src,
                              bool n_required, uint32_t n_min, uint32_t n_max, const char *usage)
{
    bool okp, have_n = false, have_rate = false;
    *src = TRNG_SRC_A;
    for (const char *t = arg; *t; t = next_tok(t)) {
        if (tok_eq(t, "B")) {
            *src = TRNG_SRC_B;
        } else if (!have_n) {
            *n = parse_u32(t, &okp);
            if (!okp || *n < n_min || *n > n_max) {
                err(usage);
                return false;
            }
            have_n = true;
        } else if (!have_rate) {
            *rate = parse_u32(t, &okp);
            if (!okp || *rate < 100 || *rate > 500000) {
                err("BAD_ARG rate must be 100..500000");
                return false;
            }
            have_rate = true;
        } else {
            err(usage);
            return false;
        }
    }
    if (n_required && !have_n) {
        err(usage);
        return false;
    }
    if (*src == TRNG_SRC_B && !trng_b_present()) {
        err("NO_SOURCE_B no second noise source was found on PB1 at power-up");
        return false;
    }
    return true;
}

static void print_std(char *tmp, size_t len, const char *prefix, const trng_stats_t *st)
{
    snprintf(tmp, len, "%sMIN %u MAX %u MEAN %d STD %d.%d", prefix, st->min, st->max, (int)st->mean,
             (int)st->stddev, (int)((st->stddev - (int)st->stddev) * 10));
}

static void cmd_analyze(const char *arg)
{
    uint32_t n = 4096, rate = ZIRAH_SAMPLE_RATE_HZ;
    trng_src_t src;
    if (!parse_sample_args(arg, &n, &rate, &src, false, 64, 4096,
                           "BAD_ARG usage ANALYZE [n 64..4096] [rate] [B]")) {
        return;
    }
    uint16_t *a = CCM->u.raw[0], *b = CCM->u.raw[1];
    bool pair = trng_b_present();
    if (pair) {
        trng_sample_pair(a, b, n, rate);
    } else {
        trng_sample_block(a, n, rate, TRNG_SRC_A);
    }
    const uint16_t *mine = src == TRNG_SRC_B ? b : a, *other = src == TRNG_SRC_B ? a : b;
    trng_stats_t st;
    trng_analyze(mine, n, &st);

    char tmp[96];
    snprintf(tmp, sizeof(tmp), "N %lu RATE %lu SOURCE %s", (unsigned long)st.n, (unsigned long)rate,
             src == TRNG_SRC_B ? "B" : "A");
    io_line(tmp);
    print_std(tmp, sizeof(tmp), "", &st);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "CLIPPED %lu DISTINCT12 %lu DISTINCT8 %lu",
             (unsigned long)st.clipped, (unsigned long)st.distinct12, (unsigned long)st.distinct8);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "MCV_H %d.%02d bits/symbol (quick estimate only)",
             (int)st.mcv_entropy, (int)((st.mcv_entropy - (int)st.mcv_entropy) * 100));
    io_line(tmp);
    int ac = (int)(st.autocorr1 * 1000.0f);
    snprintf(tmp, sizeof(tmp), "AUTOCORR1 %s%d.%03d", ac < 0 ? "-" : "",
             (ac < 0 ? -ac : ac) / 1000, (ac < 0 ? -ac : ac) % 1000);
    io_line(tmp);
    uint32_t maxh = 1;
    for (int i = 0; i < 16; i++) {
        if (st.hist[i] > maxh) maxh = st.hist[i];
    }
    for (int i = 0; i < 16; i++) {
        uint32_t lo = st.hist_lo + (uint32_t)i * st.hist_step;
        int bars = (int)((st.hist[i] * 40u) / maxh);
        snprintf(tmp, sizeof(tmp), "HIST %4lu %5lu ", (unsigned long)lo, (unsigned long)st.hist[i]);
        io_puts(tmp);
        for (int k = 0; k < bars; k++) io_putc('#');
        io_putc('\n');
    }
    if (pair) {
        /* The other source at the same instants, and how much the two agree. */
        trng_stats_t so;
        trng_analyze(other, n, &so);
        print_std(tmp, sizeof(tmp), src == TRNG_SRC_B ? "OTHER A " : "OTHER B ", &so);
        io_line(tmp);
        int32_t r = trng_corr_x1000(a, b, n);
        int32_t m = r < 0 ? -r : r;
        snprintf(tmp, sizeof(tmp), "CORR_AB %s%ld.%03ld (limit 0.%03u)", r < 0 ? "-" : "", (long)(m / 1000),
                 (long)(m % 1000), (unsigned)ZIRAH_MAX_CORR_X1000);
        io_line(tmp);
    }

    ui_view_t v;
    oled_ui_view_init(&v, UI_ANALYZE);
    v.hist = st.hist;
    v.std_x10 = (int32_t)(st.stddev * 10.0f);
    v.mean = (int32_t)st.mean;
    v.clipped = st.clipped;
    v.mcv_x100 = (int32_t)(st.mcv_entropy * 100.0f);
    v.num = st.n;
    show(&v);
    g_hold_until = millis() + 6000u;
    ok();
}

static void cmd_health(void)
{
    char tmp[112];
    trng_health_t h;
    trng_state_t s = trng_startup();
    trng_health_get(&h);
    io_puts("TRNG ");
    io_line(trng_state_str(s));
    snprintf(tmp, sizeof(tmp), "HEALTH symbols=%lu rct_cut=%u rct_max=%lu apt_cut=%u apt_max=%lu",
             (unsigned long)h.symbols, h.rct_cutoff, (unsigned long)h.rct_max_run,
             h.apt_cutoff, (unsigned long)h.apt_max);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "VBIAS_MV %lu", (unsigned long)adc_vbias_last_mv());
    io_line(tmp);
    source_b_line();
    snprintf(tmp, sizeof(tmp), "TEMP_C10 %ld", (long)adc_temp_c10());
    io_line(tmp);
    if (!g_have_key && s == TRNG_OK) {
        (void)load_device_key();
    }
    leds_idle();
    g_hold_until = 0;
    ui_idle();
    if (s == TRNG_OK) {
        ok();
    } else {
        err("ENTROPY_FAULT entropy source unhealthy (see docs/GUIDE.md)");
    }
}

static void cmd_raw(const char *arg)
{
    uint32_t n = 0, rate = ZIRAH_SAMPLE_RATE_HZ;
    trng_src_t src;
    if (!parse_sample_args(arg, &n, &rate, &src, true, 1, 20000000u,
                           "BAD_ARG usage RAW <n 1..20000000> [rate] [B]")) {
        return;
    }
    char tmp[64];
    trng_health_t before, after;
    if (src == TRNG_SRC_B) {
        trng_health_get_b(&before);
    } else {
        trng_health_get(&before);
    }
    snprintf(tmp, sizeof(tmp), "RAW BEGIN %lu %lu", (unsigned long)n, (unsigned long)rate);
    io_line(tmp);
    io_flush();
    led_only(LED_BLUE);

    ui_view_t v;
    oled_ui_view_init(&v, UI_CAPTURE);
    v.den = n;
    show(&v);
    uint32_t last_draw = millis();

    uint32_t crc = 0;
    uint32_t left = n;
    while (left) {
        uint32_t k = left > 2048u ? 2048u : left;
        uint16_t *buf = CCM->u.raw[0];
        trng_sample_block(buf, k, rate, src);
        trng_health_feed(buf, k, src);
        crc = zirah_crc32((const uint8_t *)buf, k * 2u, crc);
        io_write((const uint8_t *)buf, k * 2u);
        left -= k;
        if ((uint32_t)(millis() - last_draw) >= 1000u) {
            last_draw = millis();
            v.num = n - left;
            show(&v);
        }
    }
    io_flush();
    if (src == TRNG_SRC_B) {
        trng_health_get_b(&after);
    } else {
        trng_health_get(&after);
    }
    snprintf(tmp, sizeof(tmp), "\nRAW END crc32=%08lx health_failures=%lu",
             (unsigned long)crc, (unsigned long)(after.failures - before.failures));
    io_line(tmp);
    leds_idle();
    g_hold_until = 0;
    ui_idle();
    ok();
}

static void cmd_keygen(const char *arg)
{
    bool force = tok_eq(arg, "FORCE");
    if (g_locked) {
        err("LOCKED a signature self-check failed; power-cycle the device");
        return;
    }
    if (keystore_present() && !force) {
        err("KEY_EXISTS use KEYGEN FORCE to destroy the old key and make a new one");
        return;
    }
    if (force && keystore_present()) {
        press_t p = confirm_press(TXT_REPLACE_TITLE, TXT_REPLACE_BIG, TXT_REPLACE_L1,
                                  TXT_PRESS_USER, "purpose=keygen-force");
        if (p != PRESS_OK) {
            g_hold_until = 0;
            ui_idle();
            leds_idle();
            err(press_error(p));
            return;
        }
    }

    op_begin(TXT_OP_KEYGEN, STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) | STAGE_BIT(ST_SIGN) |
                           STAGE_BIT(ST_VERIFY));
    op_stage(ST_ENTROPY);
    if (!entropy_fresh()) {
        op_fail();
        err("ENTROPY_FAULT entropy source unhealthy, refusing to generate a key");
        return;
    }
    unsigned mixed = mix_extra_random();
    uint32_t t0 = cycles_now();
    if (zirah_keypair(g_tmp_pk, g_tmp_sk, &CCM->u.work, rng_cb) != 0) {
        zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
        trng_clear_extra_input();
        op_fail();
        err("ENTROPY_FAULT entropy failed during key generation");
        return;
    }
    uint32_t us = cycles_to_us(cycles_now() - t0);

    /* Pairwise consistency test through the real masked path before the key
     * is stored: mask it, sign a test message, verify with the new pk. */
    op_stage(ST_REFRESH);
    bool pct = zirah_msk_load(&CCM->msk, g_tmp_sk, rng_cb) == 0;
    op_stage(ST_SIGN);
    uint8_t rnd[32];
    size_t siglen = 0;
    pct = pct && trng_get(rnd, sizeof(rnd)) == 0;
    if (pct) {
        zirah_sign_ctx(g_sig, &siglen, (const uint8_t *)"ZIRAH-PCT-v1", 12,
                       (const uint8_t *)"zirah-pct-v1", 12, rnd, &CCM->msk, NULL);
    }
    zirah_memzero(rnd, sizeof(rnd));
    trng_clear_extra_input();
    op_stage(ST_VERIFY);
    pct = pct && zirah_verify_ctx(g_sig, siglen, (const uint8_t *)"ZIRAH-PCT-v1", 12,
                                  (const uint8_t *)"zirah-pct-v1", 12, g_tmp_pk,
                                  &CCM->u.work) == 0;
    if (!pct || !keystore_save(g_tmp_pk, g_tmp_sk)) {
        zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
        zirah_msk_wipe(&CCM->msk);
        g_have_key = false;
        (void)load_device_key(); /* the previous key, if one is still stored */
        op_fail();
        err(pct ? "FLASH flash write failed" : "PCT pairwise consistency test failed");
        return;
    }
    zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));
    memcpy(g_pk, g_tmp_pk, PKB);
    g_have_key = true;

    char tmp[48];
    uint8_t f[4];
    char fp[9];
    io_kv_hex("PK", g_pk, PKB);
    fp4(g_pk, PKB, f);
    io_kv_hex("PK_FP", f, 4);
    snprintf(tmp, sizeof(tmp), "KEYGEN_US %lu", (unsigned long)us);
    io_line(tmp);
    io_line((mixed & MIX_ECC) ? "ECC_RNG mixed" : "ECC_RNG none");
    io_line((mixed & MIX_MCU) ? "STM32_RNG mixed" : "STM32_RNG none");
    io_line(jit_mixed() ? "JITTER mixed" : "JITTER none");
    io_line(acc_mixed() ? "ACCEL mixed" : "ACCEL none");
    io_line("PCT ok");
    hex8(fp, f);
    op_done(TXT_DONE_NEWKEY, TXT_LABEL_KEY, fp, true, false, false, TXT_NOTE_CHECK_FP);
    ok();
}

static void cmd_pk(void)
{
    if (!ensure_key()) {
        return;
    }
    uint8_t f[4];
    io_kv_hex("PK", g_pk, PKB);
    fp4(g_pk, PKB, f);
    io_kv_hex("PK_FP", f, 4);
    ok();
}

static void cmd_eccpk(void)
{
    if (!g_ecc_ready || !g_have_eccpk) {
        err("NO_ECC ATECC608 not provisioned (see ATECC PROVISION)");
        return;
    }
    uint8_t f[4];
    io_kv_hex("ECCPK", g_eccpk, 64);
    fp4(g_eccpk, 64, f);
    io_kv_hex("ECC_FP", f, 4);
    ok();
}

/* Masked sign + on-device verification. Returns false (and locks signing)
 * if the self-check fails; the signature buffer is then wiped. */
static bool sign_and_check(const uint8_t *msg, size_t mlen, const char *ctx,
                           size_t *siglen, uint32_t *attempts, uint32_t us[3])
{
    uint8_t rnd[32];
    uint32_t c0 = cycles_now();
    if (trng_get(rnd, sizeof(rnd)) != 0) {
        return false;
    }
    op_stage(ST_REFRESH);
    uint32_t c1 = cycles_now();
    if (zirah_msk_refresh(&CCM->msk, rng_cb) != 0) {
        zirah_memzero(rnd, sizeof(rnd));
        return false;
    }
    op_stage(ST_SIGN);
    uint32_t c2 = cycles_now();
    zirah_sign_ctx(g_sig, siglen, msg, mlen, (const uint8_t *)ctx, strlen(ctx), rnd,
                   &CCM->msk, attempts);
    zirah_memzero(rnd, sizeof(rnd));
    op_stage(ST_VERIFY);
    uint32_t c3 = cycles_now();
    bool good = zirah_verify_ctx(g_sig, *siglen, msg, mlen, (const uint8_t *)ctx, strlen(ctx),
                                 g_pk, &CCM->u.work) == 0;
    uint32_t c4 = cycles_now();
    us[0] = cycles_to_us(c1 - c0) + cycles_to_us(c2 - c1); /* entropy + refresh */
    us[1] = cycles_to_us(c3 - c2);                           /* sign              */
    us[2] = cycles_to_us(c4 - c3);                           /* verify            */
    if (!good) {
        /* A signature that does not verify may be the product of a fault and
         * can leak key information: never release it, and stop signing. */
        zirah_memzero(g_sig, sizeof(g_sig));
        g_locked = true;
    }
    return good;
}

static void cmd_sign(const char *arg)
{
    uint8_t digest[32];
    if (!parse_hex(arg, digest, 32) || arg[64] != 0) {
        err("BAD_ARG usage SIGN <64 hex chars = SHA-256 of the document>");
        return;
    }
    if (!ensure_key()) {
        return;
    }
    char dg[9];
    hex8(dg, digest);
    uint8_t stages = STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) | STAGE_BIT(ST_SIGN) |
                     STAGE_BIT(ST_VERIFY);
    if (g_confirm) stages |= STAGE_BIT(ST_CONFIRM);
    if (g_ecc_ready) stages |= STAGE_BIT(ST_ECDSA);
    op_begin(TXT_OP_SIGNING, stages);

    if (g_confirm) {
        op_stage(ST_CONFIRM);
        char args[24];
        snprintf(args, sizeof(args), "doc=%s", dg);
        press_t p = confirm_press(TXT_SIGN_TITLE, dg, TXT_SIGN_L1, TXT_SIGN_L2, args);
        if (p != PRESS_OK) {
            op_fail();
            err(press_error(p));
            return;
        }
    }

    op_stage(ST_ENTROPY);
    uint32_t c0 = cycles_now();
    if (!entropy_fresh()) {
        op_fail();
        err("ENTROPY_FAULT entropy source failed its startup test, refusing to sign");
        return;
    }
    unsigned mixed = mix_extra_random();
    uint32_t health_us = cycles_to_us(cycles_now() - c0);

    size_t siglen = 0;
    uint32_t attempts = 0, us[3] = {0, 0, 0};
    bool good = sign_and_check(digest, 32, ZIRAH_CTX, &siglen, &attempts, us);
    trng_clear_extra_input();
    if (!good) {
        op_fail();
        err(g_locked ? "SELF_CHECK signature failed on-device verification; withheld, signing locked"
                     : "ENTROPY_FAULT entropy failed while signing");
        g_hold_until = 0;
        ui_idle();
        return;
    }

    uint8_t ecsig[64];
    int erc = -1;
    uint32_t ec_us = 0;
    if (g_ecc_ready) {
        op_stage(ST_ECDSA);
        uint32_t c = cycles_now();
        erc = atecc_sign_digest(0, digest, ecsig);
        ec_us = cycles_to_us(cycles_now() - c);
    }

    io_kv_hex("MLDSA", g_sig, siglen);
    if (erc == ATECC_OK) {
        io_kv_hex("ECDSA", ecsig, 64);
    } else {
        io_puts("ECDSA NONE ");
        io_line(g_ecc_ready ? atecc_strerror(erc) : "ATECC608 not provisioned");
    }
    char tmp[192];
    snprintf(tmp, sizeof(tmp),
             "STATS health_us=%lu trng_us=%lu mldsa_us=%lu verify_us=%lu ecdsa_us=%lu attempts=%lu"
             " ecc_rng=%u stm32_rng=%u jitter=%u accel=%u sources=%u",
             (unsigned long)health_us, (unsigned long)us[0], (unsigned long)us[1],
             (unsigned long)us[2], (unsigned long)ec_us, (unsigned long)attempts,
             (mixed & MIX_ECC) ? 1u : 0u, (mixed & MIX_MCU) ? 1u : 0u, jit_mixed() ? 1u : 0u, acc_mixed() ? 1u : 0u, trng_b_present() ? 2u : 1u);
    io_line(tmp);

    g_sig_count++;
    op_done(TXT_DONE_SIGNED, TXT_LABEL_DOC, dg, true, g_ecc_ready, erc == ATECC_OK,
            erc == ATECC_OK ? TXT_NOTE_BOTH : TXT_NOTE_PQ_ONLY);
    ok();
}

static void cmd_prove(const char *arg)
{
    uint8_t nonce[32];
    if (!parse_hex(arg, nonce, 32) || arg[64] != 0) {
        err("BAD_ARG usage PROVE <64 hex chars = host nonce>");
        return;
    }
    if (g_prove_count >= ZIRAH_PROVE_MAX_PER_BOOT) {
        err("RATE_LIMIT identity proof budget used up for this boot");
        return;
    }
    if (g_prove_count > 0 &&
        (uint32_t)(millis() - g_prove_last) < ZIRAH_PROVE_MIN_INTERVAL_MS) {
        err("RATE_LIMIT wait before the next identity proof");
        return;
    }
    if (!ensure_key()) {
        return;
    }
    /* message = prefix || device UID || host nonce, under its own context. */
    uint8_t msg[sizeof(ZIRAH_PROVE_PREFIX) - 1 + 12 + 32];
    memcpy(msg, ZIRAH_PROVE_PREFIX, sizeof(ZIRAH_PROVE_PREFIX) - 1);
    memcpy(msg + sizeof(ZIRAH_PROVE_PREFIX) - 1, g_uid, 12);
    memcpy(msg + sizeof(ZIRAH_PROVE_PREFIX) - 1 + 12, nonce, 32);

    op_begin(TXT_OP_PROVING, STAGE_BIT(ST_ENTROPY) | STAGE_BIT(ST_REFRESH) | STAGE_BIT(ST_SIGN) |
                            STAGE_BIT(ST_VERIFY));
    op_stage(ST_ENTROPY);
    if (!entropy_fresh()) {
        op_fail();
        err("ENTROPY_FAULT entropy source failed its startup test");
        return;
    }
    size_t siglen = 0;
    uint32_t attempts = 0, us[3];
    g_prove_count++;
    g_prove_last = millis();
    (void)mix_extra_random();
    bool proved = sign_and_check(msg, sizeof(msg), ZIRAH_PROVE_CTX, &siglen, &attempts, us);
    trng_clear_extra_input();
    if (!proved) {
        op_fail();
        err(g_locked ? "SELF_CHECK proof failed on-device verification; signing locked"
                     : "ENTROPY_FAULT entropy failed during the proof");
        return;
    }
    io_kv_hex("PROOF", g_sig, siglen);
    g_hold_until = 0;
    leds_idle();
    ui_idle();
    ok();
}

/* ------------------------------------------------------------------------ */
/* BENCH: timings of the real signing path on this chip                      */
/* ------------------------------------------------------------------------ */

/*
 * The benchmark signs with a throwaway key made for it in RAM, never with
 * the device key, so it cannot be used to collect power traces of the device
 * key without the USER button (PROVE is rate limited for the same reason).
 * The device key is reloaded from flash, with fresh masks, at the end.
 * Nothing it signs is ever sent out, and it uses its own FIPS 204 context.
 */
#define BENCH_MAX 100u
#define ZIRAH_BENCH_CTX "zirah-bench-v1"
enum { B_TRNG, B_REFRESH, B_SIGN, B_VERIFY, B_ATTEMPTS, B_ROWS };
#define g_bench (CCM->bench)
_Static_assert(sizeof(((ccm_layout_t *)0)->bench) == B_ROWS * BENCH_MAX * sizeof(uint32_t),
               "BENCH table size");

static void sort_u32(uint32_t *a, uint32_t n)
{
    for (uint32_t i = 1; i < n; i++) {
        uint32_t v = a[i], j = i;
        while (j > 0 && a[j - 1] > v) {
            a[j] = a[j - 1];
            j--;
        }
        a[j] = v;
    }
}

static void bench_line(const char *name, const uint32_t *v, uint32_t n)
{
    static uint32_t s[BENCH_MAX];
    char tmp[112];
    uint64_t sum = 0;
    memcpy(s, v, n * sizeof(uint32_t));
    sort_u32(s, n);
    for (uint32_t i = 0; i < n; i++) {
        sum += s[i];
    }
    uint32_t med = (n & 1u) ? s[n / 2] : (uint32_t)(((uint64_t)s[n / 2 - 1] + s[n / 2]) / 2u);
    snprintf(tmp, sizeof(tmp), "BENCH %s n=%lu min=%lu median=%lu max=%lu mean=%lu", name,
             (unsigned long)n, (unsigned long)s[0], (unsigned long)med, (unsigned long)s[n - 1],
             (unsigned long)(sum / n));
    io_line(tmp);
}

void zirah_cref_ntt(int32_t a[N]);            /* bench_cref.c */
void zirah_cref_invntt_tomont(int32_t a[N]);

/* Cycles of one transform: the median of 101 calls, each on a fresh copy of
 * the same input. */
static uint32_t bench_xform(void (*f)(int32_t *), const int32_t *in, int32_t *work)
{
    static uint32_t t[101];
    for (uint32_t r = 0; r < 101; r++) {
        memcpy(work, in, N * sizeof(int32_t));
        uint32_t c0 = cycles_now();
        f(work);
        t[r] = cycles_now() - c0;
    }
    sort_u32(t, 101);
    return t[50];
}

static void cmd_bench(const char *arg)
{
    bool okp;
    uint32_t n = 20;
    if (*arg) {
        n = parse_u32(arg, &okp);
        if (!okp || n < 1 || n > BENCH_MAX) {
            err("BAD_ARG usage BENCH [n 1..100]");
            return;
        }
    }
    if (g_locked) {
        err("LOCKED a signature self-check failed; power-cycle the device");
        return;
    }
    ui_message(TXT_BENCH_TITLE, TXT_BENCH_L1, TXT_BENCH_L2, NULL);
    led_only(LED_BLUE);
    if (!entropy_fresh()) {
        leds_idle();
        ui_idle();
        err("ENTROPY_FAULT entropy source unhealthy, benchmark not run");
        return;
    }
    /* The same extra random inputs as a real signature, so every conditioner
     * block absorbs the same amount of data. */
    unsigned mixed = mix_extra_random();
    char tmp[112];
    snprintf(tmp, sizeof(tmp), "BENCH build ntt=%s n=%lu cpu_mhz=%lu sources=%u ecc_rng=%u stm32_rng=%u"
             " jitter=%u accel=%u",
#ifdef ZIRAH_NTT_ASM
             "asm",
#else
             "c",
#endif
             (unsigned long)n, (unsigned long)(ZIRAH_CPU_HZ / 1000000u), trng_b_present() ? 2u : 1u,
             (mixed & MIX_ECC) ? 1u : 0u, (mixed & MIX_MCU) ? 1u : 0u, jitter_live() ? 1u : 0u, accel_live() ? 1u : 0u);
    io_line(tmp);

    /* The device key leaves CCM for the duration; it is reloaded below. */
    g_have_key = false;
    zirah_msk_wipe(&CCM->msk);
    uint32_t c0 = cycles_now();
    bool good = zirah_keypair(g_tmp_pk, g_tmp_sk, &CCM->u.work, rng_cb) == 0;
    uint32_t keygen_us = cycles_to_us(cycles_now() - c0);
    c0 = cycles_now();
    good = good && zirah_msk_load(&CCM->msk, g_tmp_sk, rng_cb) == 0;
    uint32_t load_us = cycles_to_us(cycles_now() - c0);
    zirah_memzero(g_tmp_sk, sizeof(g_tmp_sk));

    uint32_t done = 0;
    for (uint32_t i = 0; good && i < n; i++) {
        uint8_t msg[32], rnd[32], idx[4] = {(uint8_t)i, (uint8_t)(i >> 8), 0, 0};
        keccak_state ks;
        shake256_init(&ks);
        shake256_absorb(&ks, (const uint8_t *)"ZIRAH-BENCH-v1", 14);
        shake256_absorb(&ks, idx, sizeof(idx));
        shake256_finalize(&ks);
        shake256_squeeze(msg, sizeof(msg), &ks);
        size_t siglen = 0;
        uint32_t att = 0;
        uint32_t t0 = cycles_now();
        good = trng_get(rnd, sizeof(rnd)) == 0;
        uint32_t t1 = cycles_now();
        good = good && zirah_msk_refresh(&CCM->msk, rng_cb) == 0;
        uint32_t t2 = cycles_now();
        if (good) {
            zirah_sign_ctx(g_sig, &siglen, msg, sizeof(msg), (const uint8_t *)ZIRAH_BENCH_CTX,
                           sizeof(ZIRAH_BENCH_CTX) - 1, rnd, &CCM->msk, &att);
        }
        uint32_t t3 = cycles_now();
        bool ver = good && zirah_verify_ctx(g_sig, siglen, msg, sizeof(msg),
                                            (const uint8_t *)ZIRAH_BENCH_CTX,
                                            sizeof(ZIRAH_BENCH_CTX) - 1, g_tmp_pk, &CCM->u.work) == 0;
        uint32_t t4 = cycles_now();
        zirah_memzero(rnd, sizeof(rnd));
        zirah_memzero(g_sig, sizeof(g_sig));
        if (good && !ver) {
            g_locked = true; /* the same rule as for a real signature */
        }
        good = good && ver;
        if (!good) {
            break;
        }
        g_bench[B_TRNG][i] = cycles_to_us(t1 - t0);
        g_bench[B_REFRESH][i] = cycles_to_us(t2 - t1);
        g_bench[B_SIGN][i] = cycles_to_us(t3 - t2);
        g_bench[B_VERIFY][i] = cycles_to_us(t4 - t3);
        g_bench[B_ATTEMPTS][i] = att;
        done++;
        if (done % 5u == 0 || done == n) {
            snprintf(tmp, sizeof(tmp), "EVT BENCH done=%lu of=%lu", (unsigned long)done,
                     (unsigned long)n);
            io_line(tmp);
        }
    }
    zirah_msk_wipe(&CCM->msk);
    trng_clear_extra_input();

    /* The transforms alone, on a fixed pseudo-random polynomial. */
    uint32_t nc[4] = {0, 0, 0, 0}, shake_cycles = 0;
    if (good) {
        int32_t *in = CCM->u.xform[0], *work = CCM->u.xform[1];
        uint8_t buf[4 * N];
        shake256(buf, sizeof(buf), (const uint8_t *)"ZIRAH-BENCH-NTT", 15);
        for (uint32_t i = 0; i < N; i++) {
            uint32_t w = (uint32_t)buf[4 * i] | (uint32_t)buf[4 * i + 1] << 8 |
                         (uint32_t)buf[4 * i + 2] << 16 | (uint32_t)buf[4 * i + 3] << 24;
            in[i] = (int32_t)(w % (2u * Q - 1u)) - (Q - 1);
        }
        nc[0] = bench_xform(ntt, in, work);
        nc[1] = bench_xform(zirah_cref_ntt, in, work);
        nc[2] = bench_xform(invntt_tomont, in, work);
        nc[3] = bench_xform(zirah_cref_invntt_tomont, in, work);
        static uint32_t t[21];
        uint8_t out[32];
        for (uint32_t r = 0; r < 21; r++) {
            uint32_t s0 = cycles_now();
            shake256(out, sizeof(out), buf, 1024);
            t[r] = cycles_now() - s0;
        }
        sort_u32(t, 21);
        shake_cycles = t[10];
        zirah_memzero(CCM->u.xform, sizeof(CCM->u.xform));
    }

    /* The device key back into CCM, with fresh masks. */
    if (keystore_present()) {
        (void)load_device_key();
    }
    leds_idle();
    g_hold_until = 0;
    ui_idle();
    if (!good) {
        err(g_locked ? "SELF_CHECK a benchmark signature failed on-device verification; signing locked"
                     : "ENTROPY_FAULT entropy failed during the benchmark");
        return;
    }
    snprintf(tmp, sizeof(tmp), "BENCH keygen_us value=%lu", (unsigned long)keygen_us);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "BENCH mask_load_us value=%lu", (unsigned long)load_us);
    io_line(tmp);
    bench_line("trng32_us", g_bench[B_TRNG], done);
    bench_line("refresh_us", g_bench[B_REFRESH], done);
    bench_line("sign_us", g_bench[B_SIGN], done);
    bench_line("verify_us", g_bench[B_VERIFY], done);
    bench_line("attempts", g_bench[B_ATTEMPTS], done);
    snprintf(tmp, sizeof(tmp), "BENCH ntt_cycles signer=%lu c=%lu", (unsigned long)nc[0],
             (unsigned long)nc[1]);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "BENCH invntt_cycles signer=%lu c=%lu", (unsigned long)nc[2],
             (unsigned long)nc[3]);
    io_line(tmp);
    snprintf(tmp, sizeof(tmp), "BENCH shake256_1kb_cycles value=%lu", (unsigned long)shake_cycles);
    io_line(tmp);
    ok();
}

static void cmd_confirm(const char *arg)
{
    if (tok_eq(arg, "ON")) {
        g_confirm = true;
        ui_idle();
        ok();
        return;
    }
    if (!tok_eq(arg, "OFF")) {
        err("BAD_ARG usage CONFIRM ON|OFF");
        return;
    }
    /* A remote host must not be able to remove the physical check. */
    press_t p = confirm_press(TXT_NOBTN_TITLE, TXT_NOBTN_BIG, TXT_NOBTN_L1, TXT_PRESS_USER,
                              "purpose=confirm-off");
    if (p != PRESS_OK) {
        g_hold_until = 0;
        ui_idle();
        leds_idle();
        err(press_error(p));
        return;
    }
    g_confirm = false;
    leds_idle();
    ui_idle();
    ok();
}

static void provision_log(const char *msg, int rc)
{
    io_puts("STEP ");
    io_puts(msg);
    io_puts(" ... ");
    io_line(rc == ATECC_OK ? "ok" : atecc_strerror(rc));
    io_flush();
}

static void cmd_atecc(const char *arg)
{
    if (tok_eq(arg, "INFO")) {
        atecc_status_t st;
        int rc = atecc_status(&st);
        if (rc != ATECC_OK) {
            err_atecc(rc);
            return;
        }
        io_kv_hex("REVISION", st.revision, 4);
        io_kv_hex("SERIAL", st.serial, 9);
        io_puts("CONFIG_LOCKED ");
        io_line(st.config_locked ? "yes" : "no");
        io_puts("DATA_LOCKED ");
        io_line(st.data_locked ? "yes" : "no");
        io_puts("SLOT0_LOCKED ");
        io_line(st.slot0_locked ? "yes" : "no");
        char tmp[48];
        snprintf(tmp, sizeof(tmp), "SLOT0_CONFIG 0x%04X KEYCONFIG 0x%04X", st.slot0_config,
                 st.slot0_keyconfig);
        io_line(tmp);
        io_puts("ZIRAH_READY ");
        io_line(atecc_ready(&st) ? "yes" : "no");
        ok();
    } else if (tok_eq(arg, "CONFIG")) {
        uint8_t cfg[128];
        int rc = atecc_read_config(cfg);
        if (rc != ATECC_OK) {
            err_atecc(rc);
            return;
        }
        for (int i = 0; i < 4; i++) {
            char k[12];
            snprintf(k, sizeof(k), "CONFIG%d", i);
            io_kv_hex(k, &cfg[i * 32], 32);
        }
        ok();
    } else if (tok_eq(arg, "RANDOM")) {
        uint8_t r[32];
        int rc = atecc_random(r);
        if (rc != ATECC_OK) {
            err_atecc(rc);
            return;
        }
        io_kv_hex("RANDOM", r, 32);
        if (!g_ecc.config_locked) {
            io_line("NOTE config zone unlocked: the chip returns a fixed test pattern");
        }
        ok();
    } else if (tok_eq(arg, "PROVISION")) {
        const char *code = next_tok(arg);
        if (!*code) {
            uint8_t r[2];
            if (trng_state() != TRNG_OK || trng_get(r, 2) != 0) {
                r[0] = (uint8_t)cycles_now();
                r[1] = (uint8_t)(cycles_now() >> 9);
            }
            g_provision_code = (uint16_t)(1000u + ((uint32_t)(r[0] << 8 | r[1]) % 9000u));
            g_provision_ms = millis();
            io_line("WARNING this permanently configures and LOCKS the ATECC608.");
            io_line("WARNING it cannot be undone. Slot 0 gets a P-256 key that never leaves the chip.");
            char tmp[48];
            snprintf(tmp, sizeof(tmp), "CONFIRM_CODE %u", g_provision_code);
            io_line(tmp);
            io_line("Run: ATECC PROVISION <code> within 60 seconds, then press " ZIRAH_BTN ".");
            ok();
            return;
        }
        bool okp;
        uint32_t c = parse_u32(code, &okp);
        if (!okp || g_provision_code == 0 || c != g_provision_code ||
            (uint32_t)(millis() - g_provision_ms) > 60000u) {
            g_provision_code = 0;
            err("BAD_CODE wrong or expired confirmation code (run ATECC PROVISION again)");
            return;
        }
        g_provision_code = 0;
        press_t p = confirm_press(TXT_LOCK_TITLE, TXT_LOCK_BIG, TXT_LOCK_L1, TXT_PRESS_USER,
                                  "purpose=atecc-provision");
        if (p != PRESS_OK) {
            g_hold_until = 0;
            ui_idle();
            leds_idle();
            err(press_error(p));
            return;
        }
        ui_message(TXT_PROVISION_TITLE, TXT_PROVISION_L1, TXT_PROVISION_L2, NULL);
        uint8_t pub[64];
        int rc = atecc_provision(pub, provision_log);
        ecc_refresh();
        g_hold_until = 0;
        ui_idle();
        if (rc != ATECC_OK) {
            err_atecc(rc);
            return;
        }
        io_kv_hex("ECCPK", pub, 64);
        ok();
    } else {
        err("BAD_ARG usage ATECC INFO|CONFIG|RANDOM|PROVISION");
    }
}

static void dispatch(const char *line)
{
    while (*line == ' ') line++;
    const char *arg = next_tok(line);

    if (!*line) {
        err("BAD_LINE empty, too long or non-printable line");
    } else if (tok_eq(line, "HELP")) {
        cmd_help();
    } else if (tok_eq(line, "PING")) {
        io_line("PONG");
        ok();
    } else if (tok_eq(line, "INFO")) {
        cmd_info();
    } else if (tok_eq(line, "PEEK")) {
        cmd_peek();
    } else if (tok_eq(line, "I2CSCAN")) {
        cmd_i2cscan();
    } else if (tok_eq(line, "VBIAS")) {
        cmd_vbias(arg);
    } else if (tok_eq(line, "ANALYZE")) {
        cmd_analyze(arg);
    } else if (tok_eq(line, "HEALTH")) {
        cmd_health();
    } else if (tok_eq(line, "RAW")) {
        cmd_raw(arg);
    } else if (tok_eq(line, "KEYGEN")) {
        cmd_keygen(arg);
    } else if (tok_eq(line, "PK")) {
        cmd_pk();
    } else if (tok_eq(line, "ECCPK")) {
        cmd_eccpk();
    } else if (tok_eq(line, "SIGN")) {
        cmd_sign(arg);
    } else if (tok_eq(line, "PROVE")) {
        cmd_prove(arg);
    } else if (tok_eq(line, "BENCH")) {
        cmd_bench(arg);
    } else if (tok_eq(line, "JITTER")) {
        jitter_diagnose(io_line);
        ok();
    } else if (tok_eq(line, "ACCEL")) {
        accel_diagnose(io_line);
        ok();
    } else if (tok_eq(line, "CONFIRM")) {
        cmd_confirm(arg);
    } else if (tok_eq(line, "ATECC")) {
        cmd_atecc(arg);
    } else if (tok_eq(line, "REBOOT")) {
        ok();
        delay_ms(50);
        scb_reset_system();
    } else {
        err("UNKNOWN unknown command (try HELP)");
    }
}

/* Linker symbols (libopencm3 cortex-m-generic.ld): the image in flash ends
 * where the load copy of .data ends. */
extern uint32_t _data_loadaddr, _data, _edata;

static void fw_hash_compute(void)
{
    uintptr_t start = 0x08000000u;
    size_t len = (size_t)((uintptr_t)&_data_loadaddr - start) +
                 (size_t)((uintptr_t)&_edata - (uintptr_t)&_data);
    shake256(g_fw_hash, sizeof(g_fw_hash), (const uint8_t *)start, len);
}

int main(void)
{
    board_init();
    /* CCM holds arbitrary values after power-up; start from zero. */
    zirah_memzero(CCM, sizeof(ccm_layout_t));
    led_only(LED_ORANGE);
    board_uid(g_uid);

    io_init();
    i2c_init();
    adc_setup();
    mcu_rng_init();
    jitter_init();
    accel_init();
    (void)oled_init();

    ui_view_t v;
    oled_ui_view_init(&v, UI_BOOT);
    v.line1 = TXT_BOOT_SELFTEST;
    v.line2 = TXT_BOOT_FW_PREFIX ZIRAH_FW_VERSION;
    show(&v);

    if (!keystore_layout_ok()) {
        zirah_fault_entry(ZIRAH_FAULT_LAYOUT); /* image would overwrite the key */
    }
    if (!board_mpu_init(ZIRAH_MIN_STACK)) {
        zirah_fault_entry(ZIRAH_FAULT_RAM); /* too little stack for signing */
    }
    board_set_fault_wiper(wipe_outside_ccm);

    fw_hash_compute();
    g_kat_ok = boot_kat();
    if (!g_kat_ok) {
        ui_message(TXT_SELFTEST_TITLE, TXT_SELFTEST_L1, TXT_SELFTEST_L2, TXT_SELFTEST_L3);
        g_locked = true;
    }

    v.line1 = TXT_BOOT_SETTLING;
    show(&v);
    delay_ms(2000); /* let the boost filter and the op-amp reference settle */
    trng_startup();

    /* Randomise the stack-protector canary from the entropy source. */
    uint32_t canary = 0;
    if ((trng_state() != TRNG_OK || trng_get((uint8_t *)&canary, sizeof(canary)) != 0) &&
        mcu_rng_get((uint8_t *)&canary, sizeof(canary)) != 0) {
        canary = ((uint32_t)g_uid[0] << 24 | (uint32_t)g_uid[5] << 16 |
                  (uint32_t)g_uid[11] << 8) ^ cycles_now();
    }
    board_set_stack_guard(canary);
    zirah_memzero(&canary, sizeof(canary));

    ecc_refresh();
    (void)load_device_key();
    g_mon_next = millis();
    leds_idle();
    ui_idle();
    io_broadcast("ZIRAH " ZIRAH_FW_VERSION " ready. Type HELP.\n");

    for (;;) {
        const char *line = io_poll_line();
        if (line) {
            dispatch(line);
            /* Give the host a moment for follow-up commands before sampling. */
            g_mon_next = millis() + 250u;
            continue;
        }
        uint32_t now = millis();
        if (g_hold_until && (int32_t)(now - g_hold_until) >= 0) {
            g_hold_until = 0;
            ui_idle();
        }
        if ((int32_t)(now - g_mon_next) >= 0) {
            g_mon_next = now + ZIRAH_MONITOR_PERIOD_MS;
            monitor_tick();
        }
    }
}
