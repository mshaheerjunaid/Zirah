/*
 * Every OLED string that main.c puts on screen, in one place, so the native
 * preview test (tests/native/oled_preview.c) renders exactly the same text
 * and can prove that none of it is clipped.
 *
 * Style: Title Case. Every word starts with a capital letter except the
 * short function words (a, an, the, and, or, for, of, to, in, on, at, by,
 * it, with); words written fully in capitals stay that way. Unit symbols
 * keep their SI case (ms, V).
 *
 * Width limits on the 128-pixel panel (6 pixels per character):
 *   fault screen lines start at x = 40  -> at most 14 characters
 *   centred lines                        -> at most 21 characters
 *   boot screen lines start at x = 56   -> at most 12 characters
 */
#ifndef ZIRAH_UI_TEXT_H
#define ZIRAH_UI_TEXT_H

/* The confirmation button's name on this board: the Discovery's blue USER
 * button, or K1 on the DevEBox. */
#if defined(ZIRAH_BOARD_DEVEBOX)
#define ZIRAH_BTN "K1"
#define ZIRAH_NOISE_PIN "PA4"
#else
#define ZIRAH_BTN "USER"
#define ZIRAH_NOISE_PIN "PA1"
#endif

/* Boot */
#define TXT_BOOT_SELFTEST    "Self-Test"
#define TXT_BOOT_SETTLING    "Settling"
#define TXT_BOOT_FW_PREFIX   "FW "
#define TXT_SELFTEST_TITLE   "SELF-TEST"
#define TXT_SELFTEST_L1      "Crypto Self-Test"
#define TXT_SELFTEST_L2      "FAILED"
#define TXT_SELFTEST_L3      "Do Not Use"

/* Dashboard header states */
#define TXT_STATE_READY      "READY"
#define TXT_STATE_LOCKED     "LOCKED"
#define TXT_STATE_NOKEY      "NO KEY"

/* Entropy faults: line 1, a readable state, a hint */
#define TXT_FAULT_ENTROPY    "ENTROPY"
#define TXT_TRNG_OK          "OK"
#define TXT_TRNG_NOT_STARTED "Not Started"
#define TXT_TRNG_BIAS        "Bias Fault"
#define TXT_TRNG_STUCK       "Stuck Signal"
#define TXT_TRNG_RCT         "RCT Failed"
#define TXT_TRNG_APT         "APT Failed"
#define TXT_TRNG_B           "Source B Fault"
#define TXT_TRNG_CORR        "A-B Correlated"
#define TXT_TRNG_SOURCE      "No Live Noise"
#define TXT_HINT_BIAS        "Check Boost"
#define TXT_HINT_STUCK       "Check " ZIRAH_NOISE_PIN
#define TXT_HINT_HEALTH      "Health Test"
#define TXT_HINT_NOT_STARTED "Run HEALTH"
#define TXT_HINT_B           "Check PB1"
#define TXT_HINT_CORR        "Check Supply"

/* Signature self-check failure */
#define TXT_LOCKED_TITLE     "LOCKED"
#define TXT_LOCKED_L1        "SELF-CHECK"
#define TXT_LOCKED_L2        "Bad Signature"
#define TXT_POWER_CYCLE      "Power-Cycle"

/* CPU and build faults */
#define TXT_CPU_FAULT        "CPU FAULT"
#define TXT_RNG_FAULT        "RNG FAULT"
#define TXT_BUILD_FAULT      "BUILD FAULT"
#define TXT_SECRETS_WIPED    "Secrets Wiped"

/* Confirmations */
#define TXT_SIGN_TITLE       "SIGN?"
#define TXT_SIGN_L1          "Match the PC Screen"
#define TXT_SIGN_L2          "Press " ZIRAH_BTN " to Sign"
#define TXT_REPLACE_TITLE    "REPLACE?"
#define TXT_REPLACE_BIG      "REPLACE"
#define TXT_REPLACE_L1       "Old Key Is Lost"
#define TXT_NOBTN_TITLE      "NO BUTTON?"
#define TXT_NOBTN_BIG        "NO BTN"
#define TXT_NOBTN_L1         "Sign Without " ZIRAH_BTN
#define TXT_LOCK_TITLE       "LOCK ATECC?"
#define TXT_LOCK_BIG         "LOCK"
#define TXT_LOCK_L1          "Permanent, No Undo"
#define TXT_PRESS_USER       "Press " ZIRAH_BTN

/* Operations */
#define TXT_OP_SIGNING       "SIGNING"
#define TXT_OP_KEYGEN        "KEYGEN"
#define TXT_OP_PROVING       "PROVING"

/* Results */
#define TXT_DONE_SIGNED      "SIGNED"
#define TXT_DONE_NEWKEY      "NEW KEY"
#define TXT_LABEL_DOC        "Doc"
#define TXT_LABEL_KEY        "Key"
#define TXT_NOTE_BOTH        "Both Halves Done"
#define TXT_NOTE_PQ_ONLY     "Post-Quantum Only"
#define TXT_NOTE_CHECK_FP    "Check FP on the PC"

/* Tools */
#define TXT_VBIAS_TARGET     "Target 12V, 9-20V OK"
#define TXT_VBIAS_WATCH      "Turn Screw Slowly"
#define TXT_PROVISION_TITLE  "PROVISION"
#define TXT_PROVISION_L1     "Configuring ATECC"
#define TXT_PROVISION_L2     "Do Not Unplug"
#define TXT_BENCH_TITLE      "BENCHMARK"
#define TXT_BENCH_L1         "Throwaway Key"
#define TXT_BENCH_L2         "Device Key Untouched"

#endif
