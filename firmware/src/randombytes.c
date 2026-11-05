/*
 * randombytes() for the ML-DSA reference code.
 *
 * The shipped firmware never calls it: key generation uses zirah_keypair()
 * and signing passes an explicit rnd, both with error returns. It is kept
 * because the reference sign.c references it, and it fails closed: if the
 * entropy source is unhealthy the device wipes its secrets and halts rather
 * than hand the reference code bad randomness through an API that has no
 * way to report an error.
 */
#include "randombytes.h"
#include "board.h"
#include "trng.h"

void randombytes(uint8_t *out, size_t outlen)
{
    if (trng_get(out, outlen) != 0) {
        zirah_fault_entry(ZIRAH_FAULT_RNG);
    }
}
