/*
 * Small memory-safety helpers used wherever secrets are handled.
 *
 * zirah_memzero() cannot be removed by the optimiser, unlike a plain memset()
 * on a buffer that is about to go out of scope. zirah_ct_equal() compares in
 * time that does not depend on where the first difference is.
 */
#ifndef ZIRAH_SECURE_H
#define ZIRAH_SECURE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

void zirah_memzero(void *p, size_t n);
bool zirah_ct_equal(const void *a, const void *b, size_t n);

#endif
