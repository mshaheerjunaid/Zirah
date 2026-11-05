#include "trng_logic.h"

#include <math.h>
#include <string.h>

/* ------------------------------------------------------------------------ */
/* SP 800-90B 4.4 continuous health tests on 8-bit symbols                   */
/* ------------------------------------------------------------------------ */

/* Cutoffs for alpha = 2^-20, W = 512 (computed from the 90B formulas). */
uint16_t trng_apt_cutoff(uint32_t h_x100)
{
    if (h_x100 >= 800) return 13;
    if (h_x100 >= 700) return 18;
    if (h_x100 >= 600) return 25;
    if (h_x100 >= 500) return 39;
    if (h_x100 >= 400) return 62;
    if (h_x100 >= 300) return 103;
    if (h_x100 >= 200) return 177;
    if (h_x100 >= 100) return 311;
    return 410; /* H >= 0.5 */
}

uint16_t trng_rct_cutoff(uint32_t h_x100)
{
    /* C = 1 + ceil(20 / H) */
    uint32_t c = 1u + (2000u + h_x100 - 1u) / h_x100;
    return (uint16_t)c;
}

void health_init(health_state_t *h, uint32_t h_x100)
{
    memset(h, 0, sizeof(*h));
    h->pub.rct_cutoff = trng_rct_cutoff(h_x100);
    h->pub.apt_cutoff = trng_apt_cutoff(h_x100);
}

health_result_t health_symbol(health_state_t *h, uint8_t s)
{
    health_result_t r = HEALTH_PASS;
    h->pub.symbols++;

    /* Repetition Count Test */
    if (h->rct_run && s == h->rct_last) {
        h->rct_run++;
    } else {
        h->rct_last = s;
        h->rct_run = 1;
    }
    if (h->rct_run > h->pub.rct_max_run) {
        h->pub.rct_max_run = h->rct_run;
    }
    if (h->rct_run >= h->pub.rct_cutoff) {
        r = HEALTH_RCT;
    }

    /* Adaptive Proportion Test */
    if (h->apt_pos == 0) {
        h->apt_ref = s;
        h->apt_count = 1;
    } else if (s == h->apt_ref) {
        h->apt_count++;
    }
    if (h->apt_count > h->pub.apt_max) {
        h->pub.apt_max = h->apt_count;
    }
    if (h->apt_count >= h->pub.apt_cutoff && r == HEALTH_PASS) {
        r = HEALTH_APT;
    }
    h->apt_pos++;
    if (h->apt_pos == TRNG_APT_WINDOW) {
        h->apt_pos = 0;
    }

    if (r != HEALTH_PASS) {
        h->pub.failures++;
    }
    return r;
}

/* ------------------------------------------------------------------------ */
/* Statistics                                                                */
/* ------------------------------------------------------------------------ */

void trng_analyze(const uint16_t *buf, size_t n, trng_stats_t *st)
{
    static uint32_t seen12[4096 / 32];
    static uint16_t count8[256];
    memset(st, 0, sizeof(*st));
    memset(seen12, 0, sizeof(seen12));
    memset(count8, 0, sizeof(count8));
    if (n < 2) {
        return;
    }
    st->n = (uint32_t)n;
    st->min = 4095;
    double sum = 0.0, sum2 = 0.0, lag = 0.0;
    for (size_t i = 0; i < n; i++) {
        uint16_t v = (uint16_t)(buf[i] & 0x0FFF);
        if (v < st->min) st->min = v;
        if (v > st->max) st->max = v;
        if (v == 0 || v == 4095) st->clipped++;
        seen12[v >> 5] |= 1u << (v & 31u);
        count8[v & 0xFF]++;
        sum += v;
        sum2 += (double)v * v;
    }
    double mean = sum / (double)n;
    double var = sum2 / (double)n - mean * mean;
    if (var < 0) var = 0;
    for (size_t i = 1; i < n; i++) {
        lag += ((double)(buf[i] & 0x0FFF) - mean) * ((double)(buf[i - 1] & 0x0FFF) - mean);
    }
    st->mean = (float)mean;
    st->stddev = (float)sqrt(var);
    st->autocorr1 = var > 0 ? (float)(lag / ((double)(n - 1) * var)) : 0.0f;

    for (int i = 0; i < 4096 / 32; i++) {
        st->distinct12 += (uint32_t)__builtin_popcount(seen12[i]);
    }
    uint32_t maxc = 0;
    for (int i = 0; i < 256; i++) {
        if (count8[i]) st->distinct8++;
        if (count8[i] > maxc) maxc = count8[i];
    }
    /* SP 800-90B 6.3.1 most-common-value estimate (upper 99% bound on p). */
    double p = (double)maxc / (double)n;
    double pu = p + 2.576 * sqrt(p * (1.0 - p) / (double)(n - 1));
    if (pu > 1.0) pu = 1.0;
    st->mcv_entropy = (float)(-log2(pu));

    /* 16-bin histogram between min and max. */
    uint32_t span = (uint32_t)(st->max - st->min) + 1u;
    uint32_t step = (span + 15u) / 16u;
    if (step == 0) step = 1;
    st->hist_lo = st->min;
    st->hist_step = (uint16_t)step;
    for (size_t i = 0; i < n; i++) {
        uint32_t b = ((uint32_t)(buf[i] & 0x0FFF) - st->min) / step;
        if (b > 15) b = 15;
        st->hist[b]++;
    }
}

static uint32_t isqrt64(uint64_t x)
{
    uint64_t r = 0, bit = 1ull << 62;
    while (bit > x) bit >>= 2;
    while (bit) {
        if (x >= r + bit) {
            x -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    return (uint32_t)r;
}

void trng_mean_std_x10(const uint16_t *buf, size_t n, int32_t *mean_x10, int32_t *std_x10)
{
    *mean_x10 = 0;
    *std_x10 = 0;
    if (n == 0) {
        return;
    }
    uint64_t sum = 0, sum2 = 0;
    for (size_t i = 0; i < n; i++) {
        uint32_t v = buf[i] & 0x0FFFu;
        sum += v;
        sum2 += (uint64_t)v * v;
    }
    /* var * 100 = (100 * n * sum2 - (10 * sum)^2) / n^2, kept exact. */
    uint64_t nn = (uint64_t)n;
    uint64_t a = 100u * nn * sum2, b = 100u * sum * sum;
    uint64_t var_x100 = a > b ? (a - b) / (nn * nn) : 0;
    *mean_x10 = (int32_t)((sum * 10u + nn / 2u) / nn);
    *std_x10 = (int32_t)isqrt64(var_x100);
}

int32_t trng_corr_x1000(const uint16_t *a, const uint16_t *b, size_t n)
{
    if (n < 2) {
        return 0;
    }
    int64_t sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
    for (size_t i = 0; i < n; i++) {
        int64_t x = a[i] & 0x0FFF, y = b[i] & 0x0FFF;
        sa += x;
        sb += y;
        saa += x * x;
        sbb += y * y;
        sab += x * y;
    }
    int64_t nn = (int64_t)n;
    /* All three fit in 64 bits for n <= 65536 12-bit samples. */
    int64_t cov = nn * sab - sa * sb;
    int64_t va = nn * saa - sa * sa;
    int64_t vb = nn * sbb - sb * sb;
    if (va <= 0 || vb <= 0) {
        return 0;
    }
    double r = (double)cov / sqrt((double)va * (double)vb);
    if (r > 1.0) r = 1.0;
    if (r < -1.0) r = -1.0;
    return (int32_t)(r * 1000.0);
}

int32_t trng_autocorr1_x1000(const uint16_t *buf, size_t n)
{
    if (n < 3) {
        return 0;
    }
    return trng_corr_x1000(buf, buf + 1, n - 1);
}

bool trng_source_a_live(int32_t mean_x10, int32_t std_x10, int32_t ac1_x1000)
{
    int32_t off = mean_x10 - TRNG_A_CENTRE_X10;
    if (off < 0) off = -off;
    return off <= TRNG_A_MAX_OFFSET_X10 && std_x10 >= TRNG_B_ALIVE_STD_X10 && ac1_x1000 < TRNG_A_MAX_AC1_X1000;
}

bool trng_source_live(int32_t mean_x10, int32_t std_x10, int32_t ac1_x1000)
{
    return mean_x10 >= TRNG_B_PRESENT_MIN_X10 && std_x10 >= TRNG_B_ALIVE_STD_X10 &&
           ac1_x1000 < TRNG_B_MAX_AC1_X1000;
}

bool trng_jitter_ok(const uint16_t *d, size_t n, uint32_t *distinct, uint32_t *spread, uint32_t *mode)
{
    static uint16_t hist[256];
    uint32_t lo = 0xFFFFu, hi = 0, inside = 1;
    for (size_t i = 0; i < n; i++) {
        if (d[i] < lo) lo = d[i];
        if (d[i] > hi) hi = d[i];
        if (d[i] < TRNG_JIT_MIN_TICKS || d[i] > TRNG_JIT_MAX_TICKS) inside = 0;
    }
    uint32_t used = 0, top = 0;
    if (n && hi - lo < 256u) {
        memset(hist, 0, sizeof(hist));
        for (size_t i = 0; i < n; i++) {
            uint16_t c = ++hist[d[i] - lo];
            if (c == 1) used++;
            if (c > top) top = c;
        }
    } else if (n) {
        used = 256;                 /* spread wider than the table: certainly varied */
        top = 1;
    }
    if (distinct) *distinct = used;
    if (spread) *spread = n ? hi - lo : 0;
    if (mode) *mode = top;
    return n >= 16 && inside && used >= TRNG_JIT_MIN_DISTINCT && top * 4u <= (uint32_t)n * 3u;
}

bool trng_accel_ok(const int16_t *xyz, size_t samples, uint32_t *least)
{
    uint32_t fewest = 0xFFFFFFFFu;
    bool silent = true;
    for (size_t axis = 0; axis < 3; axis++) {
        uint32_t used = 0;
        for (size_t i = 0; i < samples; i++) {
            int16_t v = xyz[3 * i + axis];
            if (v != 0 && v != -1) silent = false;
            bool seen = false;
            for (size_t j = 0; j < i && !seen; j++) seen = xyz[3 * j + axis] == v;
            if (!seen) used++;
        }
        if (used < fewest) fewest = used;
    }
    if (!samples) fewest = 0;
    if (least) *least = fewest;
    return samples >= TRNG_ACC_MIN_SAMPLES && fewest >= TRNG_ACC_MIN_DISTINCT && !silent;
}
