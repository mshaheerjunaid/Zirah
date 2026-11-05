#include "atecc.h"
#include "swi2c.h"
#include "board.h"
#include "zirah_config.h"

#include <string.h>

#define ADDR ZIRAH_ATECC_ADDR

#define OP_INFO   0x30
#define OP_LOCK   0x17
#define OP_RANDOM 0x1B
#define OP_READ   0x02
#define OP_WRITE  0x12
#define OP_GENKEY 0x40
#define OP_NONCE  0x16
#define OP_SIGN   0x41

#define WA_COMMAND 0x03
#define WA_SLEEP   0x01
#define WA_IDLE    0x02

/* CRC-16 (poly 0x8005, LSB-first per byte) as specified for the ATECC family. */
static void atecc_crc(const uint8_t *data, size_t len, uint8_t crc_le[2])
{
    uint16_t reg = 0;
    for (size_t i = 0; i < len; i++) {
        for (uint8_t sh = 0x01; sh > 0x00; sh = (uint8_t)(sh << 1)) {
            uint8_t data_bit = (data[i] & sh) ? 1 : 0;
            uint8_t crc_bit = (uint8_t)(reg >> 15);
            reg = (uint16_t)(reg << 1);
            if (data_bit != crc_bit) {
                reg ^= 0x8005;
            }
        }
    }
    crc_le[0] = (uint8_t)(reg & 0xFF);
    crc_le[1] = (uint8_t)(reg >> 8);
}

static bool crc_ok(const uint8_t *resp, uint8_t count)
{
    uint8_t c[2];
    if (count < 4) {
        return false;
    }
    atecc_crc(resp, (size_t)(count - 2), c);
    return c[0] == resp[count - 2] && c[1] == resp[count - 1];
}

static void atecc_idle(void)
{
    (void)i2c_write_prefixed(ADDR, WA_IDLE, NULL, 0);
}

/* What the last failed wake saw, for the error text. The wake token is a
 * fixed 4-byte status packet, so nothing secret is ever reported. */
static uint8_t g_wake_rx[4];
static char g_wake_steps[16];      /* per attempt: probe ACK digit, read ACK digit */
static uint8_t g_wake_tries;

static int atecc_wake(void)
{
    /* Three wake methods, two attempts each, because breakout boards differ:
     * a plain SDA-low pulse, the 0x00 address byte Microchip's libraries
     * send, and an explicit sleep first so the next wake starts clean. */
    static const uint16_t high_us[6] = {2000, 4000, 2000, 4000, 8000, 8000};
    memset(g_wake_steps, 0, sizeof(g_wake_steps));
    for (int attempt = 0; attempt < 6; attempt++) {
        if (attempt >= 4) {
            (void)i2c_write_prefixed(ADDR, WA_SLEEP, NULL, 0);
            delay_ms(5);
        }
        if (attempt == 2 || attempt == 3) {
            i2c_zero_byte_pulse();
        } else {
            i2c_sda_low_pulse(attempt < 2 ? 100u : 500u);    /* tWLO >= 60 us */
        }
        delay_us(high_us[attempt]);                           /* tWHI >= 1500 us */
        uint8_t r[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        bool ok = i2c_read(ADDR, r, 4);
        bool probe = ok || i2c_probe(ADDR);     /* only probed when the read failed */
        memcpy(g_wake_rx, r, 4);
        g_wake_steps[2 * attempt] = probe ? '1' : '0';
        g_wake_steps[2 * attempt + 1] = ok ? '1' : '0';
        g_wake_tries = (uint8_t)(attempt + 1);
        if (ok && r[0] == 0x04 && r[1] == 0x11 && crc_ok(r, 4)) {
            return ATECC_OK;
        }
        if (ok) {
            atecc_idle();          /* awake with other data in its buffer */
            delay_us(1000);
        } else {
            delay_ms(3);
        }
    }
    return ATECC_ERR_NOWAKE;
}

const char *atecc_wake_trace(void)
{
    /* "steps=PRPRPR... rx=XXXXXXXX": P = address ACK on a write probe after
     * the wake, R = ACK on the read of the wake token. */
    static char buf[48];
    static const char hx[] = "0123456789ABCDEF";
    char *p = buf;
    for (const char *t = "steps="; *t; t++) {
        *p++ = *t;
    }
    for (int i = 0; i < 2 * g_wake_tries && i < 12; i++) {
        *p++ = g_wake_steps[i];
    }
    for (const char *t = " rx="; *t; t++) {
        *p++ = *t;
    }
    for (int i = 0; i < 4; i++) {
        *p++ = hx[g_wake_rx[i] >> 4];
        *p++ = hx[g_wake_rx[i] & 15];
    }
    *p = 0;
    return buf;
}

/*
 * Send one command and collect its response.
 * resp receives the full response packet: [count][payload...][crc0][crc1].
 */
static int atecc_cmd(uint8_t op, uint8_t p1, uint16_t p2,
                     const uint8_t *data, uint8_t dlen,
                     uint8_t *resp, uint8_t resp_cap,
                     uint32_t wait_ms)
{
    uint8_t pkt[7 + 64];
    if (dlen > 64 || resp_cap < 4) {
        return ATECC_ERR_LEN;
    }
    uint8_t count = (uint8_t)(7 + dlen);
    pkt[0] = count;
    pkt[1] = op;
    pkt[2] = p1;
    pkt[3] = (uint8_t)(p2 & 0xFF);
    pkt[4] = (uint8_t)(p2 >> 8);
    if (dlen) {
        memcpy(&pkt[5], data, dlen);
    }
    atecc_crc(pkt, (size_t)(count - 2), &pkt[count - 2]);

    int rc = atecc_wake();
    if (rc != ATECC_OK) {
        return rc;
    }
    if (!i2c_write_prefixed(ADDR, WA_COMMAND, pkt, count)) {
        atecc_idle();
        return ATECC_ERR_NACK;
    }

    delay_ms(wait_ms);

    /* The device NACKs its address until execution finishes: poll. */
    uint32_t t0 = millis();
    uint8_t n = 0;
    for (;;) {
        if (i2c_read(ADDR, &n, 1)) {
            break;
        }
        if ((uint32_t)(millis() - t0) > 400u) {
            return ATECC_ERR_TIMEOUT;
        }
        delay_ms(2);
    }
    if (n < 4 || n > resp_cap) {
        atecc_idle();
        return ATECC_ERR_LEN;
    }
    resp[0] = n;
    if (!i2c_read(ADDR, &resp[1], (size_t)(n - 1))) {
        atecc_idle();
        return ATECC_ERR_NACK;
    }
    atecc_idle();

    if (!crc_ok(resp, n)) {
        return ATECC_ERR_CRC;
    }
    if (n == 4 && resp[1] != 0x00) {
        return resp[1]; /* device status / error byte */
    }
    return ATECC_OK;
}

/* Command returning `want` payload bytes. */
static int atecc_cmd_data(uint8_t op, uint8_t p1, uint16_t p2,
                          const uint8_t *data, uint8_t dlen,
                          uint8_t *out, uint8_t want, uint32_t wait_ms)
{
    uint8_t resp[3 + 64];
    int rc = atecc_cmd(op, p1, p2, data, dlen, resp, (uint8_t)sizeof(resp), wait_ms);
    if (rc != ATECC_OK) {
        return rc;
    }
    if (resp[0] != (uint8_t)(want + 3)) {
        return ATECC_ERR_LEN;
    }
    memcpy(out, &resp[1], want);
    return ATECC_OK;
}

/* Command returning only a status byte (expect 0x00). */
static int atecc_cmd_status(uint8_t op, uint8_t p1, uint16_t p2,
                            const uint8_t *data, uint8_t dlen, uint32_t wait_ms)
{
    uint8_t resp[4];
    return atecc_cmd(op, p1, p2, data, dlen, resp, sizeof(resp), wait_ms);
}

int atecc_wake_check(void)
{
    int rc = atecc_wake();
    if (rc == ATECC_OK) {
        atecc_idle();
    }
    return rc;
}

int atecc_info(uint8_t rev[4])
{
    return atecc_cmd_data(OP_INFO, 0x00, 0x0000, NULL, 0, rev, 4, 1);
}

int atecc_read_config(uint8_t cfg[128])
{
    for (uint16_t block = 0; block < 4; block++) {
        int rc = atecc_cmd_data(OP_READ, 0x80 /* config, 32 bytes */,
                                (uint16_t)(block << 3), NULL, 0,
                                &cfg[block * 32], 32, 1);
        if (rc != ATECC_OK) {
            return rc;
        }
    }
    return ATECC_OK;
}

int atecc_status(atecc_status_t *st)
{
    uint8_t cfg[128];
    memset(st, 0, sizeof(*st));
    int rc = atecc_info(st->revision);
    if (rc != ATECC_OK) {
        return rc;
    }
    rc = atecc_read_config(cfg);
    if (rc != ATECC_OK) {
        return rc;
    }
    st->present = true;
    memcpy(&st->serial[0], &cfg[0], 4);
    memcpy(&st->serial[4], &cfg[8], 5);
    st->data_locked = (cfg[86] == 0x00);
    st->config_locked = (cfg[87] == 0x00);
    st->slot0_locked = (cfg[88] & 0x01) == 0; /* bit set = unlocked */
    st->slot0_config = (uint16_t)(cfg[20] | (cfg[21] << 8));
    st->slot0_keyconfig = (uint16_t)(cfg[96] | (cfg[97] << 8));
    return ATECC_OK;
}

bool atecc_ready(const atecc_status_t *st)
{
    return st->present && st->config_locked && st->data_locked &&
           (st->slot0_keyconfig & 0x001C) == 0x0010 && /* KeyType = P-256      */
           (st->slot0_keyconfig & 0x0001) != 0 &&      /* Private              */
           (st->slot0_config & 0x0080) != 0 &&         /* IsSecret: never read */
           (st->slot0_config & 0x0001) != 0;           /* external signing     */
}

int atecc_random(uint8_t out[32])
{
    return atecc_cmd_data(OP_RANDOM, 0x00, 0x0000, NULL, 0, out, 32, 23);
}

int atecc_genkey_public(uint8_t slot, uint8_t pub[64])
{
    return atecc_cmd_data(OP_GENKEY, 0x00, slot, NULL, 0, pub, 64, 115);
}

int atecc_sign_digest(uint8_t slot, const uint8_t digest[32], uint8_t sig[64])
{
    /* Nonce in pass-through mode loads TempKey with the 32-byte digest. */
    int rc = atecc_cmd_status(OP_NONCE, 0x03, 0x0000, digest, 32, 7);
    if (rc != ATECC_OK) {
        return rc;
    }
    /* Sign external message held in TempKey (idle keeps TempKey). */
    return atecc_cmd_data(OP_SIGN, 0x80, slot, NULL, 0, sig, 64, 70);
}

static int write_config_word(uint8_t word_addr, const uint8_t data[4])
{
    return atecc_cmd_status(OP_WRITE, 0x00 /* config, 4 bytes */, word_addr, data, 4, 26);
}

int atecc_provision(uint8_t pub_out[64], void (*log)(const char *msg, int rc))
{
    uint8_t rev[4];
    uint8_t cfg[128];
    int rc;

    rc = atecc_info(rev);
    log("info", rc);
    if (rc != ATECC_OK) {
        return rc;
    }
    if (!(rev[2] == 0x60 || rev[2] == 0x50)) {
        log("unexpected revision, refusing", ATECC_ERR_LEN);
        return ATECC_ERR_LEN;
    }
    rc = atecc_read_config(cfg);
    log("read config", rc);
    if (rc != ATECC_OK) {
        return rc;
    }
    if (cfg[87] != 0x55) {
        log("config zone already locked, refusing to change it", ATECC_ERR_LEN);
        return ATECC_ERR_LEN;
    }

    /* KeyConfig slots 0 and 1 = 0x0033: P-256, private, PubInfo, lockable. */
    static const uint8_t keyconfig[4] = {0x33, 0x00, 0x33, 0x00};
    /* SlotConfig slots 0 and 1 = 0x2083: IsSecret, ext+int signatures, GenKey allowed. */
    static const uint8_t slotconfig[4] = {0x83, 0x20, 0x83, 0x20};

    rc = write_config_word(96 / 4, keyconfig);
    log("write KeyConfig[0..1]", rc);
    if (rc != ATECC_OK) {
        return rc;
    }
    rc = write_config_word(20 / 4, slotconfig);
    log("write SlotConfig[0..1]", rc);
    if (rc != ATECC_OK) {
        return rc;
    }

    rc = atecc_read_config(cfg);
    if (rc == ATECC_OK && (memcmp(&cfg[96], keyconfig, 4) != 0 ||
                           memcmp(&cfg[20], slotconfig, 4) != 0)) {
        rc = ATECC_ERR_CRC;
    }
    log("verify written config", rc);
    if (rc != ATECC_OK) {
        return rc;
    }

    rc = atecc_cmd_status(OP_LOCK, 0x80 /* config zone */, 0x0000, NULL, 0, 32);
    log("lock config zone", rc);
    if (rc != ATECC_OK) {
        return rc;
    }

    rc = atecc_cmd_data(OP_GENKEY, 0x04 /* new private key */, 0x0000,
                        NULL, 0, pub_out, 64, 115);
    log("generate P-256 key in slot 0", rc);
    if (rc != ATECC_OK) {
        return rc;
    }

    rc = atecc_cmd_status(OP_LOCK, 0x81 /* data + OTP */, 0x0000, NULL, 0, 32);
    log("lock data/OTP zone", rc);
    if (rc != ATECC_OK) {
        return rc;
    }

    rc = atecc_cmd_status(OP_LOCK, 0x82 /* single slot: 0 */, 0x0000, NULL, 0, 32);
    log("lock slot 0", rc);
    return rc;
}

const char *atecc_strerror(int rc)
{
    switch (rc) {
    case ATECC_OK: return "ok";
    case ATECC_ERR_NOWAKE: return "no wake response (check wiring, pull-ups, address)";
    case ATECC_ERR_NACK: return "I2C NACK";
    case ATECC_ERR_TIMEOUT: return "timeout";
    case ATECC_ERR_CRC: return "CRC or verify mismatch";
    case ATECC_ERR_LEN: return "unexpected length or state";
    case 0x01: return "device status 0x01 (miscompare)";
    case 0x03: return "device status 0x03 (parse error)";
    case 0x05: return "device status 0x05 (ECC fault)";
    case 0x0F: return "device status 0x0F (execution error, often a lock/config precondition)";
    case 0xEE: return "device status 0xEE (watchdog about to expire)";
    case 0xFF: return "device status 0xFF (CRC error on command)";
    default: return "device status (see datasheet)";
    }
}
