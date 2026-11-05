#include "keystore.h"
#include "api.h"
#include "params.h"
#include "secure.h"
#include "board_pins.h"

#include <string.h>

#include <libopencm3/stm32/flash.h>

#define KS_MAGIC  0x3148525AUL /* "ZRH1" little endian */

#define PKB CRYPTO_PUBLICKEYBYTES
#define SKB CRYPTO_SECRETKEYBYTES

#define FLASH_ERRORS (FLASH_SR_PGSERR | FLASH_SR_PGPERR | FLASH_SR_PGAERR | \
                      FLASH_SR_WRPERR | FLASH_SR_OPERR)

/* Linker symbols: where the firmware image ends in flash. */
extern uint32_t _data_loadaddr, _data, _edata;

typedef struct {
    uint32_t magic;
    uint32_t pklen;
    uint32_t sklen;
    uint32_t crc;
} ks_hdr_t;

uint32_t zirah_crc32(const uint8_t *data, uint32_t len, uint32_t crc)
{
    crc = ~crc;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc >> 1) ^ (0xEDB88320UL & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

static const ks_hdr_t *hdr(void)
{
    return (const ks_hdr_t *)KS_ADDR;
}

static const uint8_t *payload(void)
{
    return (const uint8_t *)(KS_ADDR + sizeof(ks_hdr_t));
}

bool keystore_layout_ok(void)
{
    /* The image (code, rodata, then the .data initialisers) must end below
     * the key sector, or a future, larger build would overwrite the key. */
    uint32_t image_end = (uint32_t)&_data_loadaddr +
                         ((uint32_t)&_edata - (uint32_t)&_data);
    return image_end <= KS_ADDR;
}

bool keystore_present(void)
{
    const ks_hdr_t *h = hdr();
    if (h->magic != KS_MAGIC || h->pklen != PKB || h->sklen != SKB) {
        return false;
    }
    return zirah_crc32(payload(), PKB + SKB, 0) == h->crc;
}

bool keystore_load(uint8_t *pk, uint8_t *sk)
{
    if (!keystore_present()) {
        return false;
    }
    memcpy(pk, payload(), PKB);
    memcpy(sk, payload() + PKB, SKB);
    return true;
}

static bool flash_ok(void)
{
    return (FLASH_SR & FLASH_ERRORS) == 0;
}

bool keystore_erase(void)
{
    flash_unlock();
    flash_clear_status_flags();
    flash_erase_sector(KS_SECTOR, FLASH_CR_PROGRAM_X32);
    bool ok = flash_ok();
    flash_lock();
    return ok && hdr()->magic == 0xFFFFFFFFUL;
}

bool keystore_save(const uint8_t *pk, const uint8_t *sk)
{
    ks_hdr_t h;
    static uint8_t buf[PKB + SKB];
    memcpy(buf, pk, PKB);
    memcpy(buf + PKB, sk, SKB);
    h.magic = KS_MAGIC;
    h.pklen = PKB;
    h.sklen = SKB;
    h.crc = zirah_crc32(buf, PKB + SKB, 0);

    flash_unlock();
    flash_clear_status_flags();
    flash_erase_sector(KS_SECTOR, FLASH_CR_PROGRAM_X32);
    bool ok = flash_ok();
    if (ok) {
        /* Payload first, header last: a reset in between leaves no header,
         * so a half-written key is never accepted. */
        flash_program(KS_ADDR + sizeof(ks_hdr_t), buf, PKB + SKB);
        ok = flash_ok();
    }
    if (ok) {
        flash_program(KS_ADDR, (const uint8_t *)&h, sizeof(h));
        ok = flash_ok();
    }
    flash_lock();

    /* Read back and compare byte for byte, not just the CRC. */
    ok = ok && memcmp(payload(), buf, PKB + SKB) == 0 &&
         memcmp((const void *)KS_ADDR, &h, sizeof(h)) == 0 && keystore_present();
    zirah_memzero(buf, sizeof(buf));
    if (!ok) {
        /* Never leave partial secret-key bytes behind in flash. */
        (void)keystore_erase();
    }
    return ok;
}
