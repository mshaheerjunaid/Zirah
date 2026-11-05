# Zirah Host Protocol, Version 1

This is the protocol between the Zirah firmware and a PC, over the USB serial port (micro-USB, CN5) or a USB-TTL adapter on PA2/PA3 at 115200 baud. The desktop app and the command line tool both speak it through `host/zirah_core.py`, and the simulated device in `host/zirah_sim.py` answers it too. `zirah term` gives a direct terminal.

`INFO` reports `PROTO 1`. Host software refuses to sign with firmware that reports any other number, so the number must change whenever a command or a reply changes.

## Framing

The host sends one command per line: printable ASCII (0x20 to 0x7E), at most 200 characters on the host side, ended by a newline. The firmware rejects any line with other bytes, an empty line or an over-long line with `ERR BAD_LINE`. Keywords are case-insensitive; hex arguments are exact-length.

Every command ends with exactly one terminal line:

- `OK`, or
- `ERR <CODE> <text>`, where `CODE` is one of the codes below and `text` is a short explanation for people.

Before the terminal line the firmware may send:

- data lines, `KEY value`, for example `PK 3f9a...`;
- progress lines, `EVT <STAGE> [key=value ...]`, sent the moment that stage actually starts.

The host reads with a bounded line reader: a line longer than 8192 bytes, a non-ASCII byte or a control character is a protocol error, and the host resynchronises before its next command. The longest valid line is the ML-DSA-65 signature (`MLDSA` plus 6618 hex digits).

At power-up the firmware prints `ZIRAH 1.0.0 ready. Type HELP.` once; hosts ignore unsolicited lines.

## Commands

| Command | Button | Purpose | Data Lines |
|---|---|---|---|
| `PING` | | link check | `PONG` |
| `INFO` | | device, key, entropy and ATECC608 status | see below |
| `PEEK` | | the latest idle block of raw noise samples, the same block the OLED draws | `PEEK ...`, `SAMPLES ...` |
| `HEALTH` | | reruns the SP 800-90B start-up test | `TRNG`, `HEALTH`, `VBIAS_MV`, `SOURCE_B`, `TEMP_C10` |
| `SIGN <64 hex>` | yes, unless confirmation is off | hybrid signature of a 32-byte digest (the host sends the manifest digest) | `EVT ...`, `MLDSA`, `ECDSA`, `STATS` |
| `PROVE <64 hex>` | no, rate limited | identity proof over a host nonce | `EVT ...`, `PROOF` |
| `KEYGEN` | | makes the first ML-DSA-65 key | `EVT ...`, `PK`, `PK_FP`, `KEYGEN_US`, `ECC_RNG`, `STM32_RNG`, `JITTER`, `ACCEL`, `PCT ok` |
| `KEYGEN FORCE` | yes | replaces the key (the old one is destroyed) | as above, after `EVT CONFIRM` |
| `PK` | | ML-DSA-65 public key | `PK`, `PK_FP` |
| `ECCPK` | | ATECC608 P-256 public key | `ECCPK`, `ECC_FP` |
| `CONFIRM ON` | | button confirmation on | |
| `CONFIRM OFF` | yes | button confirmation off until the next power-up | `EVT CONFIRM ...` |
| `VBIAS [WATCH]` | | boost voltage; `WATCH` repeats until any byte arrives | `VBIAS_MV ... VDD_MV ...` |
| `ANALYZE [n] [rate] [B]` | | noise statistics of n samples (64 to 4096), of source B with `B` | `N`, `MIN`, `CLIPPED`, `MCV_H`, `AUTOCORR1`, `HIST`, and with source B fitted `OTHER`, `CORR_AB` |
| `RAW <n> [rate] [B]` | | streams n raw 16-bit samples (binary) for SP 800-90B, of source B with `B` | `RAW BEGIN n rate`, binary, `RAW END crc32=... health_failures=...` |
| `BENCH [n]` | | times n masked signatures (1 to 100, default 20) with a throwaway key | `BENCH ...`, `EVT BENCH done=.. of=..` |
| `JITTER` | | a fresh run of 256 LSI periods, for diagnosis only (never used for output) | `JIT_LSI`, `JIT_RUN`, `JIT_PERIODS`, `JIT state=..` |
| `ACCEL` | | 16 fresh accelerometer readings, for diagnosis only (never used for output) | `ACC_CHIP`, `ACC_RUN`, `ACC_AXIS`, `ACC state=..` |
| `I2CSCAN` | | I2C addresses that answer | `I2C 0x..` |
| `ATECC INFO` | | secure-element configuration summary | `REVISION`, `SERIAL`, lock states, `ZIRAH_READY` |
| `ATECC CONFIG` | | the 128-byte configuration zone | `CONFIG0` to `CONFIG3` |
| `ATECC RANDOM` | | 32 bytes from the ATECC608 | `RANDOM` |
| `ATECC PROVISION` | | prints a one-time confirmation code | `CONFIRM_CODE` |
| `ATECC PROVISION <code>` | yes | permanent configuration and lock, within 60 s of the code | `STEP ...`, `ECCPK` |
| `REBOOT` | | `OK`, then a system reset | |
| `HELP` | | command summary | text |

Rates are 100 to 500000 samples per second; the default is 20000.

### Button Confirmation

A command marked "yes" first sends `EVT CONFIRM <args> timeout_ms=30000` and waits for the board's button (K1 on the DevEBox, USER on the Discovery). For `SIGN` the arguments are `doc=XXXXXXXX`, the first four digest bytes in upper-case hex, which is what the OLED shows. The field name `doc`, like the context `zirah-doc-v1` and the OLED's `Doc` label on the result screen, is fixed in protocol version 1 and firmware 1.0.0 and carries the digest of whatever file is being signed. The host compares them with its own digest and cancels at once if they differ. For the other commands the argument is `purpose=keygen-force`, `purpose=confirm-off` or `purpose=atecc-provision`.

While waiting, any byte from the host cancels (`ERR CANCELLED by host`). A button already held when the wait starts must be released first, and a press longer than five seconds counts as stuck (`ERR BUTTON_STUCK`). With no press in 30 seconds the reply is `ERR TIMEOUT`. After the press, the operation runs to the end and cannot be cancelled.

### Stages

`EVT` stages, in the order they run: `CONFIRM`, `ENTROPY` (fresh start-up test), `REFRESH` (mask refresh), `SIGN` (masked ML-DSA-65), `VERIFY` (the device verifies its own signature), `ECDSA` (only with a provisioned ATECC608). `KEYGEN` and `PROVE` use `ENTROPY` to `VERIFY`. `BENCH` sends `EVT BENCH done=<k> of=<n>` after every five signatures.

### Signing Replies

```
MLDSA <6618 hex>                  ML-DSA-65 signature, 3309 bytes
ECDSA <128 hex>                   r || s, or: ECDSA NONE <reason>
STATS health_us=.. trng_us=.. mldsa_us=.. verify_us=.. ecdsa_us=.. attempts=.. ecc_rng=0|1 stm32_rng=0|1 jitter=0|1 accel=0|1 sources=1|2
```

`ecc_rng=1` and `stm32_rng=1` mean that 32 bytes from the ATECC608's random generator, and 32 from the STM32's own, went into the entropy conditioner as additional input for this signature, credited with zero entropy (see `docs/DESIGN.md`). `jitter=1` and `accel=1` mean that the LSI clock jitter and the board's accelerometer readings were absorbed into the conditioner blocks of this signature, also credited zero. `sources=2` means the second noise source was fitted and every conditioner block absorbed its samples too. `KEYGEN` reports the same with `ECC_RNG mixed|none`, `STM32_RNG mixed|none`, `JITTER mixed|none` and `ACCEL mixed|none`, and `PROVE` mixes them in the same way without reporting it.

The ML-DSA-65 message is the 32-byte digest itself, signed in FIPS 204 pure mode with the context `zirah-doc-v1` and hedged randomness from the noise source. The ECDSA half signs the same digest. The firmware does not know what the digest stands for: Zirah's host sends `SHA-256("ZIRAH-MANIFEST-v1\n" || manifest)`, the manifest holding the file's SHA-256, name, size, container and times (see `docs/DESIGN.md`, "What Is Signed"), so `doc=` in `EVT CONFIRM` and the OLED show the first four bytes of that manifest digest. If the on-device verification fails, the signature is never sent: the reply is `ERR SELF_CHECK` and signing stays locked until a power cycle.

### Identity Proof

`PROVE <nonce>` signs the message `ZIRAH-PROVE-v1 || UID (12 bytes) || nonce (32 bytes)` under the context `zirah-attest-v1` and replies `PROOF <6618 hex>`. It needs no button, allows at most one proof per 1.5 seconds and 64 per boot (`ERR RATE_LIMIT`). The host verifies it against the device's public key with a fresh random nonce; a proof can never verify as a file signature, because verifiers pin the file-signing context.

## INFO

```
FW 1.0.0
PROTO 1
ALG ML-DSA-65 (FIPS 204), masked c*s1/c*s2, hybrid ECDSA P-256 via ATECC608
UID <24 hex>                         96-bit factory ID, also the USB serial number
FW_HASH <32 hex>                     SHAKE256 (16 bytes) of the firmware image, equal to SHAKE256 of zirah.bin
KAT ok scope=shake256,ml-dsa-65      boot known-answer tests (or: KAT failed)
HARDENING mpu=1 canary=1 stack=<bytes> rdp=0|1|2   rdp: flash readout protection level
VDD_MV <mV>
VBIAS_MV <mV>                        last boost measurement
TEMP_C10 <n>                         die temperature in tenths of a degree C (internal sensor)
TRNG OK                              or NOT_STARTED, FAULT_BIAS, FAULT_STUCK, FAULT_RCT, FAULT_APT,
                                     FAULT_B (source B failed or vanished), FAULT_CORR (A and B correlated),
                                     FAULT_SOURCE (source A off-centre or too smooth: unwired)
H_ASSUMED 1.00 bits/symbol
HEALTH symbols=.. rct_cut=.. apt_cut=.. fails=.. fails_total=..
MONITOR seq=.. valid=0|1 std_x10=..
SOURCE_B absent | present std_x10=.. corr_x1000=.. symbols=.. fails=..
                                     the second noise source on PB1, found at power-up; corr_x1000
                                     is the A-B correlation of the last start-up test, x1000
STM32_RNG ok|fault|off               the STM32's own generator, an extra input credited zero
JITTER live mean_ticks=.. distinct=.. spread=.. mode=.. of=256 | absent | fault
                                     LSI periods in TIM5 ticks at 84 MHz, measured at power-up;
                                     an extra input credited zero
ACCEL live chip=lis3dsh|lis302dl least_distinct=.. of=16 | absent chip=.. | fault chip=..
                                     the board's accelerometer, its noise at power-up; an extra
                                     input credited zero
KEY present|stored|none              stored: in flash but not loaded (entropy unhealthy)
PK_FP <8 hex>                        with KEY present
MASK_REFRESHES <n>
ATECC absent | ready rev=.. config_locked=.. data_locked=.. | not-provisioned rev=..
ECC_FP <8 hex>                       with ATECC ready
BOARD discovery|devebox noise=PA1|PA4 serial=PA2/PA3|PA9/PA10 hse_mhz=..
                                     the board this build is for, its noise pin and serial
                                     pins, and the crystal found at power-up (0 = none, the
                                     internal oscillator); older builds do not send it
OLED present|absent
USB configured|not-configured
CONFIRM on|off
SIGNING enabled|locked
PROVE_LEFT <n>
SIGS <n>                             signatures since power-up
```

Hosts must ignore lines they do not know.

## PEEK

```
PEEK seq=<n> n=512 rate=20000 valid=0|1 mean=.. std_x10=.. min=.. max=.. clipped=.. trng=<state>
     [b=1 b_mean=.. b_std_x10=.. b_clipped=.. corr_x1000=..]
SAMPLES <1536 hex>                   512 samples of source A, 3 hex digits each (12-bit ADC codes)
```

`seq` counts idle blocks since boot; `seq=0` means no block yet and no `SAMPLES` line. The samples are only displayed; they never feed keys or signatures. With source B fitted, the `b_` fields describe its block from the same instants, and `corr_x1000` is a running average of the A-B correlation over the last few blocks, shown only (the fault limit applies in every start-up test).

## ANALYZE with Two Sources

With source B fitted, `ANALYZE` samples both sources at the same instants and adds two lines after the histogram:

```
OTHER B MIN .. MAX .. MEAN .. STD ..   the other source over the same samples
CORR_AB 0.012 (limit 0.300)            their correlation; above the limit the start-up test faults
```

## BENCH

```
BENCH build ntt=asm|c n=<n> cpu_mhz=168 sources=1|2 ecc_rng=0|1 stm32_rng=0|1 jitter=0|1 accel=0|1
BENCH keygen_us value=..             one key generation of the throwaway key
BENCH mask_load_us value=..          splitting it into masked shares
BENCH trng32_us n=.. min=.. median=.. max=.. mean=..    32 bytes from the entropy source
BENCH refresh_us n=.. min=.. median=.. max=.. mean=..   mask refresh, including its own randomness
BENCH sign_us n=.. min=.. median=.. max=.. mean=..      masked ML-DSA-65 signature
BENCH verify_us n=.. min=.. median=.. max=.. mean=..    on-device verification
BENCH attempts n=.. min=.. median=.. max=.. mean=..     rejection-loop iterations
BENCH ntt_cycles signer=.. c=..      one forward NTT: the code the signer uses, and the C reference
BENCH invntt_cycles signer=.. c=..   the same for the inverse NTT
BENCH shake256_1kb_cycles value=..   SHAKE256 of 1024 bytes
```

`JITTER` prints, for diagnosis:

```
JIT_LSI ready=0|1                    the LSI is running
JIT_RUN read=0|1 n=256 mean_ticks=.. lsi_hz=.. std_x10=.. min=.. max=.. distinct=.. mode=.. live=0|1
JIT_PERIODS <16 periods in ticks>    with read=1; this run never reaches the conditioner
JIT state=live|absent|fault
```

`ACCEL` prints, for diagnosis:

```
ACC_CHIP who_am_i=0x.. answered=0|1 chip=lis3dsh|lis302dl|none
ACC_RUN read=0|1 n=16 least_distinct=.. live=0|1     with a chip found
ACC_AXIS x|y|z min=.. max=..          with read=1; these readings never reach the conditioner
ACC state=live|absent|fault chip=..
```

`BENCH` never touches the device key: it generates a throwaway key in RAM, signs fixed messages under its own context (`zirah-bench-v1`), verifies each signature on the device, sends none of them, and reloads the device key with fresh masks at the end. A failed verification locks signing exactly as in `SIGN`. Times are in microseconds from the CPU cycle counter, cycles are CPU cycles at 168 MHz. The simulated device answers `ERR SIMULATED`, because it has no hardware to time.

## Error Codes

| Code | Meaning |
|---|---|
| `BAD_LINE` | empty, too long or non-printable line |
| `BAD_ARG` | missing or malformed argument (the text gives the usage) |
| `UNKNOWN` | unknown command |
| `NO_KEY` | no key yet, run `KEYGEN` |
| `NO_ENTROPY` | a key is stored but cannot be loaded while the entropy source is unhealthy |
| `KEY_EXISTS` | `KEYGEN` with a key present; use `KEYGEN FORCE` |
| `ENTROPY_FAULT` | the entropy source failed its start-up test, nothing was signed or generated |
| `PCT` | the new key failed its pairwise consistency test and was not stored |
| `FLASH` | the key could not be written and verified in flash |
| `SELF_CHECK` | a signature or proof failed on-device verification; withheld, signing locked |
| `LOCKED` | signing is locked after a self-check failure until power-cycle |
| `TIMEOUT` | no button press within 30 seconds |
| `CANCELLED` | the host sent a byte while the device waited for the button |
| `BUTTON_STUCK` | the button was held down |
| `RATE_LIMIT` | identity proofs too fast, or the per-boot budget is used |
| `NO_ECC` | the ATECC608 is not provisioned |
| `BAD_CODE` | wrong or expired provisioning code |
| `ATECC` | the secure element reported an error (the text names it) |
| `NO_SOURCE_B` | `ANALYZE B` or `RAW ... B` without a second noise source |

After a CPU fault the firmware prints `FATAL FAULT <code> CFSR <hex>` on the UART only and halts.

## Host-Side Error Codes

The host core adds its own codes for problems found on the PC, for example `UNTRUSTED_KEY`, `KEY_CHANGED`, `DIGEST_MISMATCH`, `PC_VERIFY_FAILED`, `FILE_CHANGED`, `EXISTS`, `NOT_ZIRAH` and `PORT_BUSY`. The desktop app maps every code to a message in `ui/strings.json`.
