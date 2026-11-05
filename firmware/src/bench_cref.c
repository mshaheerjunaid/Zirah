/*
 * The reference C NTT a second time, under the names zirah_cref_ntt and
 * zirah_cref_invntt_tomont, so that BENCH can time it on the real chip next
 * to the assembly the signer uses. Only BENCH calls these.
 */
#include "config.h"

#undef DILITHIUM_NAMESPACE
#define DILITHIUM_NAMESPACE(s) zirah_cref_##s

#include "../mldsa/reduce.c"
#include "../mldsa/ntt.c"
