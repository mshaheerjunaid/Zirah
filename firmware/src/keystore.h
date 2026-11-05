/*
 * ML-DSA key persistence in the last 128 KB flash sector (sector 11,
 * 0x080E0000) of the STM32F407VG.
 *
 * Honest scope: this is ordinary on-chip flash. It is not tamper resistant
 * and anyone with debugger access can read it unless Readout Protection
 * (RDP level 1) is enabled. That gap is part of what Zirah demonstrates: the
 * ATECC608 cannot hold this post-quantum key.
 */
#ifndef ZIRAH_KEYSTORE_H
#define ZIRAH_KEYSTORE_H

#include <stdint.h>
#include <stdbool.h>

bool keystore_load(uint8_t *pk, uint8_t *sk);
bool keystore_save(const uint8_t *pk, const uint8_t *sk);
bool keystore_erase(void);
bool keystore_present(void);

/* False if the firmware image reaches into the key sector (unsafe build). */
bool keystore_layout_ok(void);

uint32_t zirah_crc32(const uint8_t *data, uint32_t len, uint32_t crc);

#endif
