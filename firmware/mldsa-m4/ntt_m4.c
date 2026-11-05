/*
 * Forward and inverse NTT for the STM32F407 (Cortex-M4F) in assembly.
 *
 * ntt.S and macros.i are taken unchanged from pqm4
 * (https://github.com/mupq/pqm4, crypto_sign/ml-dsa-44/m4f, which ML-DSA-65
 * shares), the code of Greconici, Kannwischer and Sprenkels, "Compact
 * Dilithium Implementations on Cortex-M3 and Cortex-M4", IACR TCHES 2021(1),
 * released under CC0 (see THIRD_PARTY_NOTICES.md).
 *
 * These two wrappers give the assembly the names the reference code calls,
 * so the firmware build simply links this file and ntt.S in place of
 * mldsa/ntt.c (Makefile, NTT=asm, the default).
 *
 * The assembly computes the same transforms modulo q but merges layers, so
 * its outputs are not always the same integer representatives as the C code.
 * Every place the result is used reduces it first (reduce32 or caddq, and
 * both versions keep |output| < q after the inverse transform), so keys and
 * signatures come out byte-identical. tests/qemu checks this by running the
 * whole native test suite on an emulated Cortex-M4 with this assembly and
 * comparing its transcript of every key and signature with the C build.
 *
 * The assembly has no branches or memory accesses that depend on the data,
 * and its 64-bit multiplies are constant time on the Cortex-M4 (the pqm4
 * authors warn in ntt.S that this does not hold on the Cortex-M3).
 */
#include <stdint.h>

#include "ntt.h"
#include "params.h"

void pqcrystals_dilithium_ntt(int32_t a[N]);
void pqcrystals_dilithium_invntt_tomont(int32_t a[N]);

void ntt(int32_t a[N])
{
    pqcrystals_dilithium_ntt(a);
}

void invntt_tomont(int32_t a[N])
{
    pqcrystals_dilithium_invntt_tomont(a);
}
