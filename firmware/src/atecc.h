/*
 * Minimal ATECC608 driver (I2C), enough for Zirah:
 *   Info, Read (config zone), Write (config zone), Lock, GenKey, Nonce,
 *   Sign (external digest), Random.
 *
 * The provisioning sequence mirrors the configuration used by SparkFun's
 * ATECCX08a Arduino library (slot 0 = P-256 private key, external signing
 * allowed), which is a well-tested layout for the ATECC608A.
 *
 * WARNING: locking is permanent. Nothing in this driver locks the chip unless
 * the PROVISION command is confirmed explicitly from the host.
 */
#ifndef ZIRAH_ATECC_H
#define ZIRAH_ATECC_H

#include <stdint.h>
#include <stdbool.h>

/* Return codes (negative = driver/bus error, positive = device status byte). */
#define ATECC_OK          0
#define ATECC_ERR_NOWAKE  -1
#define ATECC_ERR_NACK    -2
#define ATECC_ERR_TIMEOUT -3
#define ATECC_ERR_CRC     -4
#define ATECC_ERR_LEN     -5

typedef struct {
    bool present;
    uint8_t revision[4];
    uint8_t serial[9];
    bool config_locked;
    bool data_locked;
    bool slot0_locked;
    uint16_t slot0_config;
    uint16_t slot0_keyconfig;
} atecc_status_t;

int atecc_wake_check(void);
int atecc_info(uint8_t rev[4]);
int atecc_read_config(uint8_t cfg[128]);
int atecc_status(atecc_status_t *st);

/* True when the chip is configured and locked the way Zirah expects. */
bool atecc_ready(const atecc_status_t *st);

int atecc_random(uint8_t out[32]);
int atecc_genkey_public(uint8_t slot, uint8_t pub[64]);
int atecc_sign_digest(uint8_t slot, const uint8_t digest[32], uint8_t sig[64]);

/*
 * Permanent provisioning. Writes slot 0/1 KeyConfig + SlotConfig, locks the
 * config zone, generates the slot 0 private key, locks the data/OTP zone and
 * then slot 0. `log` is called with a short progress message per step.
 */
int atecc_provision(uint8_t pub_out[64], void (*log)(const char *msg, int rc));

const char *atecc_strerror(int rc);
/* After ATECC_ERR_NOWAKE: what the last wake attempt received, as short text. */
const char *atecc_wake_trace(void);

#endif
