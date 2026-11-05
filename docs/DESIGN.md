# Zirah Design and Threat Model

This page explains what Zirah is made of, why each part is there, what it protects against, and, just as important, what it does not protect against. The last section lists which claims the measurements can support and which they cannot. Read that section before writing the technical report.

## Purpose

Zirah is a small, self-contained signing device built around an STM32F407 microcontroller. It began as an avalanche-noise true random number generator and grew into a hardware signer around that source, and the hardware is the centre of the project: the desktop app and the command-line tool exist to drive the board and to check what it returns. The device produces a **hybrid signature** for any file:

1. a post-quantum **ML-DSA-65** signature (FIPS 204), computed on the STM32 with first-order masking of the secret-key multiplications, and
2. a classical **ECDSA P-256** signature, computed inside an **ATECC608** secure element whose private key can never leave the chip.

The randomness for keys, masks and hedged signing comes from a **home-built avalanche-noise entropy source** (a reverse-biased 2N3904 base-emitter junction, amplified by an LM358 and sampled by the STM32's ADC), with SP 800-90B health tests on the device and an SP 800-90B min-entropy assessment of captured data.

The PC hashes the file and sends the board only a 32-byte digest of a manifest that describes it, so the file never leaves the PC and files of every type are signed the same way. The project has three linked results:

- a working masked ML-DSA-65 signer whose output is bit-identical to the reference implementation,
- a measured entropy source, and
- a concrete case that adding a secure element does not make the post-quantum key secure, because the ATECC608 cannot hold or use an ML-DSA key and that key ends up in ordinary microcontroller flash, which a debugger can read unless readout protection is on.

## System Overview

```
 +------------------------------  Windows PC  -----------------------------+
 |  ui/ desktop app (PySide6)        host/zirah.py command line tool       |
 |            \                        /                                   |
 |             host/zirah_core.py: protocol, trust store, signing rules    |
 |   sign: SHA-256(manifest of FILE) -> "SIGN <digest>" -> verify on PC -> |
 |         rehash -> write a signed copy, or FILE.zirah.json (atomic)      |
 |   verify: dilithium-py (ML-DSA-65) + cryptography (ECDSA), offline      |
 +------------------------------------+------------------------------------+
                                      | DevEBox: USB-TTL adapter on USART1 (PA9, PA10)
                                      | Discovery: USB CDC on the micro-USB socket CN5
 +------------------------------------v------------------------------------+
 |  STM32F407 @ 168 MHz (libopencm3, bare metal)                           |
 |                                                                         |
 |   command parser --- OLED dashboard (SH1106, I2C) --- button on PA0     |
 |        |                   (K1 on the DevEBox, USER on the Discovery)   |
 |        +-- entropy: ADC1 PA4 (PA1 on the Discovery) -> SP 800-90B       |
 |        |            RCT + APT -> SHAKE256 conditioning                  |
 |        |            ADC2 PB1 -> optional source B, same tests + A-B corr|
 |        |            ADC1 PB0 -> boost voltage monitor (startup gate)    |
 |        |            ATECC608 and STM32 RNG -> extra input, credited zero|
 |        |                                                                |
 |        +-- ML-DSA-65: key in the last flash sector, 7 on the DevEBox and|
 |        |              11 on the Discovery (unmasked at rest)            |
 |        |              s1, s2 held as 2 arithmetic shares in CCM RAM     |
 |        |              shares refreshed before every signature           |
 |        |                                                                |
 |        +-- software I2C (PA8 SCL, PC9 SDA) ---------------+             |
 +-----------------------------------------------------------|-------------+
                                                             |
                        +------------------------------------v-------------+
                        | ATECC608: P-256 key in slot 0, generated inside, |
                        | never readable; signs the same manifest digest   |
                        +--------------------------------------------------+

 Board A: MT3608 boost -> 1 kohm/100 uF filter -> 100 kohm -> 2N3904 B-E
          (avalanche) -> 100 nF -> LM358 non-inverting x11 around mid-rail
          -> RC -> PA4 (PA1 on the Discovery). Designed for a 12 V bias; on
          the measured DevEBox build R16 and R17 halve a boost output of
          about 28 V to the 13.6 to 14.2 V the firmware recorded.
```

Two Boards below explains what differs between the boards. The DevEBox build is drawn in `hardware/wiring_devebox.svg` (every connection between the boards), `hardware/schematic_A_devebox.svg` (Board A as built), `hardware/schematic_B_devebox.svg` (Board B), `hardware/breadboard_A_devebox.svg` (Board A as built) and `hardware/breadboard_B_devebox.svg` (Board B as specified). The Discovery design is in `hardware/schematic.svg`, `hardware/breadboard_A.svg` and `hardware/breadboard_B.svg`. The optional second noise source, the same circuit again on the other half of the LM358, is in the lower part of the schematic and in `hardware/breadboard_A_dual.svg`.

## The Signature

### Which Files Can Be Signed

Any regular file can be signed, whatever its type and size, because the signature never depends on understanding the file. The PC reads the file in 1 MiB pieces through SHA-256, so its size is limited only by the disk, and the device signs 32 bytes derived from that hash and the manifest below. Programs, firmware images, archives, databases, media files and documents are therefore all signed by the same code path, and the format only decides whether the signature can travel inside a signed copy (PDF, Office Open XML, ZIP, PNG and JPEG, see the table further down) or has to sit beside the file as `FILE.zirah.json`.

The host refuses three things. A path that is not a regular file (a folder, a device or a pipe) gets `NOT_A_FILE`. A file that already carries a Zirah block gets `ALREADY_SIGNED`, because signing a signed copy again would nest one signature inside another and make it unclear which original is meant. A file whose size or modification time changes between the start and the end of hashing, or between the first hash and the second one taken after the device has signed, gets `FILE_CHANGED`, and nothing is saved.

What a valid Zirah signature asserts is narrow on purpose: these exact bytes, under this name and size and with these times as the signing PC reported them, were approved by a button press on the device that holds the trusted keys. It says nothing about whether a file is safe to open or run. Operating systems, PDF readers, Office and bootloaders know nothing of Zirah blocks, so a signed program still looks unsigned to Windows Authenticode and a signed firmware image is not checked by the STM32 bootloader; a Zirah signature is checked with Zirah's verifier. Renaming a file does not invalidate its signature, since the verifier compares the content hash, the size and the container and only reports the signed name.

### What Is Signed

The PC computes the file's SHA-256 and builds a manifest: that digest, the file name, the size in bytes, the container the signature travels in (`detached`, `pdf`, `ooxml`, `zip`, `png` or `jpeg`), the signing time from the PC's clock, and the file's creation and last-modified times from the PC's file system, all in UTC. It sends `D = SHA-256("ZIRAH-MANIFEST-v1\n" || manifest)` (32 bytes), where the manifest is written as JSON with sorted keys, no spaces and ASCII escapes, so every verifier rebuilds exactly the same bytes. The file never leaves the PC, and the OLED shows the first four bytes of `D`.

- **ML-DSA-65** signs the 32-byte message `D` in FIPS 204 "pure" mode with the context string `zirah-doc-v1`. The context string binds the signature to this application, so a signature made by Zirah cannot be replayed as a signature for a different protocol that uses the same key with a different context. It keeps the name it was given in the first version, when the tool was described as a document signer, because it is part of every signature already made and the firmware; it covers files of every type. This is pure ML-DSA applied to a digest, not FIPS 204's separate HashML-DSA variant (which adds a hash-function identifier); a verifier must use pure ML-DSA-65 with the same context string. Signing is **hedged**: the 32-byte `rnd` value that FIPS 204 Algorithm 2 draws for the hedged variant comes from the entropy source for every signature.
- **ECDSA P-256** is computed by the ATECC608 over the same digest `D` (Nonce command in pass-through mode to load `D`, then Sign in external-message mode).

Because the file is reduced to digests first, the whole construction relies on SHA-256 collision resistance. That is the usual practice for file signing. The firmware is unchanged by the manifest: it signs whatever 32 bytes the PC sends, as before. Files signed by earlier builds (format `zirah-signature-v1`, where `D` was the file's SHA-256 itself and the name and time were not signed) still verify, and the report marks their time as not signed.

The times in the manifest are statements by the signing PC. They are signed, so nobody can change them afterwards, but they are only as true as that PC's clock and file system were. The firmware keeps no real-time clock, so the device cannot vouch for a time, and an independent time would need an RFC 3161 timestamping service, which needs a network connection and signs with classical algorithms.

### Signature Record and Signed Copies

The signature record (format `zirah-signature-v2`) holds the manifest, the context string, the signing device's ID and firmware version, both public keys with short fingerprints, both signatures, the device timings and a `simulated` flag. The fingerprint shown on the OLED and in the tools is the first 4 bytes of SHAKE256 of the public key, written as 8 hex digits. It is a convenience for comparing keys by eye, not a security mechanism.

Where the format allows, the record travels inside a signed copy of the file (`host/zirah_embed.py`), and the original is never modified:

| Format | Where the record goes | How readers treat it |
|---|---|---|
| PDF | a comment line after the original `%%EOF`, then the original `startxref` offset and `%%EOF` again | readers find the same cross-reference data, as after an incremental update |
| DOCX, XLSX, PPTX and other Office Open XML files | a stored part `zirah/signature.xml`, base64 inside one XML element, with its own central-directory entry | no relationship points to it, so Office applications never load it |
| ZIP (`.zip` only) | the archive comment, after any comment already there | ZIP tools show or ignore comments |
| PNG | a chunk of type `zrAH` before `IEND` (ancillary, private, not safe to copy) | viewers skip unknown ancillary chunks |
| JPEG | after the end-of-image marker, between two markers | viewers stop at the end-of-image marker |

Removing the record gives back the original bytes exactly, and that is what the manifest's digest covers. Other formats, and files whose structure does not allow it (a PDF without a final `startxref`, an archive with ZIP64 records, data already after a JPEG's end), get a separate `FILE.zirah.json` instead, as does any file signed with `--detached`. OpenDocument, EPUB, JAR and APK archives, and Open Packaging files that are not Office files (MSIX, APPX, VSIX, NuGet), are left detached on purpose, because their readers check the archive or sign its parts themselves. LibreOffice was found to refuse an Office file whose archive comment is longer than about 2 KB, which is why Office files use a part rather than the comment. A linearized PDF (fast web view) loses that property in the copy, as it does after any incremental update; it still opens. The signed copies were checked with qpdf, pypdf, Python's zipfile, python-docx, python-pptx, openpyxl, LibreOffice and Pillow; they were not opened in Microsoft Office or Adobe Acrobat during development.

The PC writes the result only after five things: the ML-DSA-65 signature verifies on the PC, the ECDSA signature (if any) verifies on the PC, the file is hashed a second time and still matches the digest in the manifest, the destination does not exist, and, for a signed copy, the finished file has been read back and gives exactly the signed original and the same record. The file is written to a temporary name in the same folder, flushed to disk and then renamed into place without replacing anything, so a crash cannot leave half a file and an existing file is never overwritten. A file that already carries a Zirah signature is refused for signing.

### Verification Policy

Verification happens on the PC with code that is independent of the firmware (dilithium-py for ML-DSA-65, the `cryptography` library for ECDSA). A signature is accepted only if:

1. the file's SHA-256 (of the original, for a signed copy) matches the one in the manifest, and the size and container match where the record was found, so a record lifted out of a signed copy into a separate file, or the other way round, is refused,
2. for a signed copy, nothing follows the record except the structure Zirah wrote, so data appended later (such as a PDF incremental update) is caught,
3. the context is exactly `zirah-doc-v1`; the verifier pins it in code and never takes it from the file,
4. the ML-DSA-65 signature verifies over the manifest digest,
5. the ECDSA signature verifies over the same digest (if present),
6. the public keys match a trusted device, either from the PC's own trust store or from a public-key file the signer exported. Once the trusted entry contains an ECDSA key, a signature **without** an ECDSA half is rejected, so an attacker cannot strip the classical half and present a weaker signature, and
7. the record is marked `"simulated": false`. A missing flag counts as simulated, and the simulator's throwaway keys are never placed in the trust store, so a simulated file cannot pass as a hardware signature even if someone edits the flag.

The verdicts are Valid Hybrid Signature, Valid Post-Quantum Signature (for a device without an ECDSA key), Valid Math, Unknown Signer (the mathematics checks out, but anyone can sign with their own key), Invalid Signature and Simulated Signature.

Verifying needs no board, so the people who check a Zirah signature install the same program and use its Verify page or `zirah verify`. A signed copy needs nothing else; the page also accepts a file, its signature file and an optional key file dropped together and sorts them by content, and its report shows the signed digest next to the file's own, so an edited file is visible at a glance, the fingerprints of the signing keys in large type, and the times in three groups: the signed ones from the manifest, the verifying PC's file-system times, which are not signed, and the moment of verification. A reader without a key file compares those fingerprints with the ones the signer published; with a key file, the report names the trusted entry that matched.

### Trusted Keys

The PC keeps the public keys of the devices it trusts in `%LOCALAPPDATA%\Zirah\trusted-keys.json`. A device is trusted only after the person types the PQ fingerprint shown on its OLED, which ties the keys received over USB to the physical board. Signing refuses to run with a key that is not trusted, and when a device's keys change (a new key, or a freshly provisioned ATECC608), the tools report Key Changed and refuse to sign until the replacement is confirmed the same way.

This is an AND combination: forging a Zirah signature requires breaking both ML-DSA-65 and ECDSA P-256 (or stealing both keys). A quantum attacker who breaks ECDSA still faces ML-DSA, and a future cryptanalytic surprise in ML-DSA still leaves ECDSA, for as long as ECDSA itself holds.

## The Masked ML-DSA-65 Signer

### Starting Point

The ML-DSA code is the pq-crystals reference implementation (vendored in `firmware/mldsa/`, built at mode 3 = ML-DSA-65, K = 6, L = 5), unchanged, except that the device build replaces its C number-theoretic transform with pqm4's Cortex-M4 assembly (see Assembly NTT below). The parameter set is a one-line choice in `firmware/Makefile` (`MLDSA_MODE`); ML-DSA-44 also fits, while ML-DSA-87 does not (see the note at the end of this section). Zirah adds `firmware/src/zirah_mldsa.c`, which mirrors the reference function `crypto_sign_signature_internal` step for step, except that the secret vectors s1 and s2 are never present as whole values during signing.

### Key Representation in RAM

At boot (or after `KEYGEN`) the secret key is unpacked, s1, s2 and t0 are transformed into the NTT domain, s1 and s2 are reduced to the range [0, q), and each is split into two **arithmetic shares** modulo q = 8380417:

```
s_hat = share0 + share1 (mod q),  share1 = r uniform in [0, q),  share0 = s_hat - r
```

The values r come from a SHAKE256 stream seeded with 32 fresh bytes from the entropy source, using rejection sampling of 23-bit values below q so they are exactly uniform. The unsplit secret key is then wiped from RAM. The shares live in the STM32's 64 KB core-coupled memory (CCM).

### Per-Signature Refresh

Before every signature the shares are refreshed with a new uniform r:

```
pass 1, every coefficient:  share0 = share0 - r
pass 2, every coefficient:  share1 = share1 + r
```

The two shares are deliberately updated in **two separate loops**. A single loop that updates `share0[i]` and then `share1[i]` back to back puts both shares of the same coefficient through the same registers and data bus in consecutive instructions. Many cores leak the Hamming distance between consecutive values on such resources, and the distance between two shares of one secret depends on the secret. This "lazy engineering" effect is known to reduce the effective security order of masked software (Balasch et al., CARDIS 2014). The emulated leakage check described below shows the effect directly on this code.

### Shuffling on Top of the Masking

Each of those passes also visits the 256 coefficients of a polynomial in its own random order, a fresh Fisher-Yates permutation drawn from the masking stream (one byte per step, masked to the next power of two and rejected when too large, so the order is exactly uniform). The share-wise products during signing are shuffled the same way, from a 32-byte seed that every refresh renews, and which of the two shares is processed first is random as well. Masking makes each single value independent of the key; shuffling adds a second, independent obstacle, because an attacker who wants to combine the leakage of the two shares of one coefficient, or to average one coefficient over many signatures, no longer knows at which instant that coefficient was handled (Veyrat-Charvillon et al., ASIACRYPT 2012, analyse what shuffling does and does not buy). The order changes nothing in the result, since every coefficient is computed from its own inputs only, and `tests/native/test_mldsa.c` checks that the order is a uniform permutation and that signatures stay byte-identical for any shuffle seed. Only the passes that touch the shares are shuffled; the NTTs themselves run in their fixed order.

### What Happens to the Shares During Signing

The two products that touch s1 and s2 are computed **one share at a time**, in a random share order and with each pointwise product in a random coefficient order:

```
z  = y  + INTT(c_hat * share0_s1) + INTT(c_hat * share1_s1)
r0 = w0 - INTT(c_hat * share0_s2) - INTT(c_hat * share1_s2)
```

Each intermediate `INTT(c_hat * share_i)` is, on its own, independent of the secret. The value only becomes secret-dependent after the second share has been added in, and at that point the result is `z` or `r0`, the same value the unmasked reference code computes.

### What Is and Is Not Masked

| Value | Masked? | Comment |
|---|---|---|
| s1, s2 in RAM | yes, 2 shares | refreshed before every signature, in a random order |
| c * s1, c * s2 products | yes | computed share by share, in a random order |
| z = y + c * s1 after recombination | no | public when the signature is accepted; secret-dependent for rejected attempts |
| r0 = w0 - c * s2 after recombination | no | its norm check is unmasked |
| y (from ExpandMask, SHAKE256 of rho') | **no** | see the limitation below |
| key K, rho' derivation | no | hedged `rnd` randomises rho' per signature |
| t0 and the c * t0 hint computation | no | |
| A (matrix), w, w1, challenge c | no | public or derived from public values |
| Secret key at rest in flash | **no** | stored as packed bytes, see below |

### Honest Limitations of the Masking

- **y is not masked.** Since `z = y + c * s1` and `z` is public, an attacker who learns `y` through a side channel learns `c * s1` and, with the public `c`, s1 itself. Masking c * s1 alone therefore does not give a first-order secure ML-DSA implementation. Fully masked implementations also mask y and its SHAKE-based generation, the decomposition of w, and the rejection checks, which needs masked Keccak and conversions between arithmetic and Boolean masking, at a cost of roughly an order of magnitude (Migliore et al., ACNS 2019; Azouaoui et al., TCHES 2023). Zirah protects the specific operation targeted by attacks on the secret-key multiplication and shows the methodology on it. It does not claim full first-order security.
- **The key is unmasked at rest.** Flash sector 11 holds the packed secret key. Every boot reads it and splits it, so an attacker who can power-cycle the device many times can collect traces of that one unmasked step. Storing the shares themselves in flash (and refreshing them there) would remove this, at the cost of flash wear.
- **First order only.** Two shares stop attacks that combine information from one point in time. Attacks that combine two points, or that exploit the transitions the separate-loop design tries to avoid on a different microarchitecture, are not addressed.
- **Leakage is emulated, not measured.** The TVLA results come from ELMO, an instruction-level leakage model of Cortex-M0/M3 class cores, on a Thumb-1 build of the refresh-and-multiply kernel. The STM32F407 is a Cortex-M4 with a different pipeline, so the emulation is evidence that the code structure is sound, not a measurement of the real chip.

### Correctness Evidence

Masking must not change the signatures. `tests/native/test_mldsa.c` compiles the firmware's own `zirah_mldsa.c` for a PC, under AddressSanitizer and UBSan where available, and checks that the low-stack key generation is byte-identical to the reference `crypto_sign_keypair`, that masked signatures are byte-identical to the reference signer for the same key, message, context and `rnd` across several keys, message lengths, contexts and mask refreshes, and that the on-device verifier accepts every valid signature and rejects altered signatures, other messages, other contexts, other keys and wrong lengths, agreeing with the reference verifier each time, and that the shuffling behaves (1976 checks in total).

The firmware repeats a smaller version of this at every power-up: a known-answer test runs SHAKE256, key generation from a fixed seed, a masked signature and verification, and compares fingerprints that were computed with dilithium-py rather than with the firmware itself (`firmware/src/kat_vectors.h`, checked by `tests/native/kat_reference.py`). If any answer differs, the device refuses to sign.

In normal use the check repeats once more: the device verifies each signature before releasing it, and the PC verifies it again with dilithium-py, which shares no code with the firmware.

### Assembly NTT

The number-theoretic transform and its inverse are the most frequent operations in signing: a rejection attempt runs up to 6 forward and 34 inverse transforms, 22 of the inverse ones over single masking shares, and the on-device verification adds more. The device build (`NTT=asm` in `firmware/Makefile`, the default) links the Cortex-M4 assembly from pqm4 (`firmware/mldsa-m4/ntt.S` and `macros.i`, unchanged) in place of `firmware/mldsa/ntt.c`. It is the code of Greconici, Kannwischer and Sprenkels (TCHES 2021), released under CC0. `make NTT=c` builds the reference C transform instead.

The assembly merges three layers of butterflies at a time and folds the final scaling into the last layer, so its outputs are the same values modulo q but not always the same integers as the C code. That is harmless only if every later step reduces first, which is true in this code: each inverse transform is followed by `reduce32` or `caddq`, and `caddq` needs its input inside (-q, q). The `qemu` test suite checks both points on an emulated Cortex-M4 (QEMU `mps2-an386`), where the firmware's assembly runs as it would on the board:

- `tests/qemu/ntt_check.c` compares the assembly with the C transform on 3000 polynomials from every input range the signer uses. All agree modulo q, and the largest inverse-transform output seen was 4235814, about half of q.
- The whole native test program (`tests/native/test_mldsa.c`) is built for the Cortex-M4 with the assembly and run in QEMU. It absorbs every key and signature it makes into a transcript hash, and that hash must equal the one from the PC build with the C transform. It does, across all 1976 checks, including the known answers computed with dilithium-py.

QEMU also counts the instructions each version executes: about 7600 instead of 29100 for the forward transform and 7800 instead of 32100 for the inverse. Those are instruction counts in an emulator, not clock cycles, and a Cortex-M4 spends more than one cycle on loads, stores and branches, so they show the size of the saving rather than a timing. The real timings come from the `STATS` line the device reports with every signature.

The transform is applied to public values and to single masking shares, which on their own are independent of the key, and it has no branches or memory accesses that depend on the data. The pqm4 authors note in `ntt.S` that its 64-bit multiplies are constant time on the Cortex-M4 but not on the Cortex-M3, which is why the code is used only for this chip.

### Emulated Leakage Check

During development the refresh-and-multiply code was run through ELMO (University of Bristol), which emulates Thumb-1 machine code and outputs a modelled power trace per instruction, and a fixed-versus-random TVLA t-test was computed with 5000 traces per group. The figures are in `docs/results/`. The kernel has four variants: unmasked, naive masking with both shares of a coefficient handled back to back, two-pass masking, and two-pass masking with every pass in its own random order, which is what the firmware does now.

| ELMO Model | Unmasked, Max \|t\| | Naive Masking, Max \|t\| | Two-Pass Masking, Max \|t\| | Two-Pass and Shuffled (Firmware), Max \|t\| |
|---|---|---|---|---|
| Cortex-M3 coefficients | 119.37 | 56.41 | 3.65 | 3.65 |
| Cortex-M0 coefficients | 127.43 | 5.12 | 3.44 | 3.34 |

Values above 4.5 count as detected leakage. The unmasked code leaks heavily, the naive masking leaks (strongly under one model, barely under the other), and both two-pass variants stay below the threshold under both models, with no point above 4.5. The first three columns repeat the earlier run exactly. That the shuffled version scores about the same is expected, because a fixed-versus-random t-test looks for first-order leakage, which the masking already removes; what shuffling adds is protection against attacks that combine several points of a trace or average one coefficient over many traces, and this test does not measure that. The fact that the same naive code gets such different verdicts from two models is itself a reason to confirm the result on real hardware one day.

### Timing Check

Masking and shuffling are about power and electromagnetic leakage; the running time is a separate channel. `tests/native/dudect.c` follows the dudect method (Reparaz, Balasch and Verbauwhede, DATE 2017): for six kernels that handle key material (the constant-time compare, the product of a share with the challenge, both NTTs, `reduce32` with `caddq`, and the whole mask refresh) it times every call with the x86 time-stamp counter while the secret input is, at random, either one fixed value or a fresh random one, and Welch's t-test compares the two groups, also after cutting off the slowest calls at several percentiles. In the runs made for this release none of the kernels went above |t| = 3.3 over 5000 to 1,000,000 measurements each, far from dudect's leak threshold of 10, while a deliberately leaky compare that stops at the first differing byte reached |t| above 1000, which shows the test is sharp enough to see a leak of that small size.

The result is about the C code as compiled for a PC, which is where a secret-dependent branch or table lookup would show up. It says nothing direct about the Cortex-M4, which has no data cache and single-cycle multiplies but a hardware divider whose time depends on its operands. For that, the `firmware` suite disassembles the 12 objects that handle the key and fails if any contains `udiv` or `sdiv`; none does. No timing was measured on the board itself.

### Parameter Set and Why ML-DSA-87 Does Not Fit

Zirah ships at **ML-DSA-65** (NIST security level 3, roughly AES-192 strength). ML-DSA-44 (level 2) also works and is a one-line change in `firmware/Makefile`. ML-DSA-87 (level 5) does not fit on the STM32F407, and this is a RAM limit, not a flag that was left unset.

The signer stores the full public matrix A and uses large polynomial vectors on the stack, and the masking keeps two shares of s1 and s2. Measured on this build:

| Parameter Set | Static RAM | Peak Signing Stack | Total vs 128 KB Main SRAM |
|---|---|---|---|
| ML-DSA-44 | 45 KB | 22 KB | about 53 % |
| ML-DSA-65 (shipped) | 69 KB | 30 KB | about 77 % |
| ML-DSA-87 | 93 KB | 40 KB | about 104 %, overflows by roughly 5 KB |

The STM32F407 has 192 KB of RAM, but it is split into a 128 KB main region and a separate 64 KB core-coupled region, and the large public matrix cannot be split across the two, so the spare core-coupled space cannot absorb the overflow. Making ML-DSA-87 fit would mean rewriting the signer to generate the matrix A row by row instead of storing it whole, and reworking the masking around that. That is a real piece of work, not a configuration change, so it is left as a possible extension.

## Why the ATECC608 Is Not Enough

### Its Role

The ATECC608 holds a P-256 private key that it generated itself in slot 0. Provisioning (`ATECC PROVISION`) writes Zirah's slot and key configuration, locks the configuration zone, generates the key, locks the data zone, and finally locks slot 0 individually. After that, the key can sign external digests but no command can read it. Zirah uses it for the classical half of every signature, and as a second, independent random source: before every key generation, signature and identity proof the firmware reads 32 bytes from the ATECC608's random generator and the conditioner absorbs them into every output block of that operation as additional input. They are credited with zero entropy, so the avalanche source alone must still pass its health tests and supply the full assumed entropy, and a missing or faulty ATECC608 cannot make the output weaker. What the second source adds is cover for a weakness in the avalanche source that the health tests do not catch, such as a subtle bias, because an attacker would then also have to predict the ATECC608's output. The STM32F407's own random generator, which samples free-running ring oscillators inside the chip and needs no wiring, is mixed in the same way as a third input with another 32 bytes (`firmware/src/mcu_rng.c`). The firmware applies the checks its reference manual asks for, the seed and clock error flags and a test that no 32-bit word repeats the one before it, and a generator that fails them is simply left out until the next boot. The signing reply reports which of the two took part (`ecc_rng=1` and `stm32_rng=1` in `STATS`, recorded in the signature file's `device_stats`), and key generation reports `ECC_RNG mixed|none` and `STM32_RNG mixed|none`.

The ATECC608 also illustrates a well-known detail: until the configuration zone is locked, its Random command returns a fixed test pattern rather than random data. Zirah reports this with `ATECC RANDOM`.

The ATECC608's full datasheet is only available from Microchip under NDA. The command encodings used here follow Microchip's open-source CryptoAuthLib and the public summary datasheet.

### Why the ATECC608 Cannot Protect the Post-Quantum Key

- **No lattice arithmetic.** The ATECC608 implements ECDSA and ECDH on P-256, SHA-256 and HMAC, and AES. It has no way to compute an ML-DSA signature.
- **No room.** An ML-DSA-65 secret key is 4032 bytes (ML-DSA-44 is 2560, ML-DSA-87 is 4896). The ATECC608's data zone is 16 slots, the largest of which (slot 8) holds 416 bytes.
- **Storing it there would not help anyway.** Even if the key were split across slots as encrypted data, it would have to be decrypted into STM32 RAM to be used, so the STM32 remains the place where it can be attacked.

So the "secure element inside" label is true for the classical key only. The post-quantum key is in STM32 flash, and at readout protection level 0 any SWD debugger can read that flash like any other memory, the Discovery's on-board ST-LINK as much as an external probe on the DevEBox's SWD header. This was shown on the STM32F4DISCOVERY used before the DevEBox: at level 0 the key was copied out with a debugger over SWD, and the copy produced a valid ML-DSA-65 signature without the board. A copied key cannot produce the ECDSA half, which never leaves the ATECC608, so on its own it cannot make a complete Zirah signature, because the verifier demands both halves from a device whose trusted keys include an ECDSA key. Against an attacker who can also break ECDSA, though, the post-quantum half is the whole of the protection, and that half rests on nothing stronger than the microcontroller's readout protection. The logs of that test are not part of this release. Readout protection level 1 (`zirah rdp-serial` on the DevEBox, `scripts\rdp.ps1` on the Discovery, step 12 of `docs/GUIDE.md`) blocks debugger and bootloader access to the flash, but published research has bypassed readout protection on several STM32 families, so it raises the cost of getting the key rather than removing the possibility. This gap, between "has a secure element" and "keeps its post-quantum key safe", is the main argument of the project.

## The Entropy Source

### Circuit

An MT3608 boost converter makes about 12 V from the board's 5 V. A 1 kohm and 100 uF filter removes most of the converter's switching ripple. A 100 kohm resistor feeds the emitter of a 2N3904 whose base is grounded and whose collector is unconnected, so the base-emitter junction is reverse-biased into avalanche breakdown (typically 7 to 9 V for this part, well above its 6 V rating, which is why only microamps are allowed to flow). Avalanche breakdown produces wideband noise. A 100 nF capacitor passes the noise and blocks the DC, and an LM358 amplifies it by 11 around a 1.5 V reference so that it is centred in the ADC range. An RC filter (1 kohm, 100 pF) sits in front of the ADC input.

On the DevEBox board measured for the technical report the circuit differs in one place. With either of the two MT3608 modules tried, the output could not be brought below about 26 V (a multimeter read 28 to 30 V during the session), and the cause was not found. Two 10 kohm resistors, R16 and R17, therefore halve the boost output before the 1 kohm and 100 uF filter, and the bias supply the firmware measured sat between 13.6 and 14.2 V. The monitor divider there is 100 kohm over 10 kohm, the same 11 to 1 ratio. `hardware/board_a_build.html` places every part of that build.

A second ADC channel (PB0) reads the boost voltage through a 1 Mohm and 100 kohm divider. At start-up the firmware refuses to use the source unless that voltage is between 9 and 20 V, and both analog pins are first pulled low for a moment so that an unconnected pin reads as a fault rather than as plausible floating noise.

### Sampling and Symbols

ADC1 samples PA1 at 20 kHz, paced by the CPU cycle counter, in blocks (64 samples for the generator, 2048 for raw captures). Each 12-bit sample is reduced to an 8-bit symbol by keeping its low 8 bits. The low bits of a noise signal with a standard deviation of hundreds of ADC steps vary far more than the high bits, and 8-bit symbols are what the NIST tool assesses.

### Health Tests

Every symbol goes through the two continuous health tests of SP 800-90B section 4.4, with a false-alarm probability of about 2^-20 per test:

- **Repetition Count Test:** fails if the same symbol repeats `1 + ceil(20 / H)` times in a row.
- **Adaptive Proportion Test:** window of 512 symbols; fails if the first symbol of the window appears too often. The cutoff comes from a table indexed by the assumed entropy H.

At start-up the noise pin is first pulled to ground for 5 ms and released, so that a pin with nothing on it starts near 0 V, and then 1024 samples are taken. They must pass both tests, show a standard deviation of at least 8 ADC steps, average within 700 steps of 2048, where the amplifier centres its output, and have a lag-1 autocorrelation below 0.4 (`trng_source_a_live()`, `FAULT_SOURCE` otherwise). The last two were added after an unwired noise pin on the DevEBox passed the first ones: it gave a mean of 1022, a standard deviation of 15.5 steps and a lag-1 autocorrelation of 0.46, against 2036, 27.6 and -0.06 for the wired source on the first board. A firmware bug added to that, since the DevEBox build still pulled down PA1 instead of PA4 before the test; both are fixed. Any failure puts the source into a fault state (red LED) and the firmware refuses to generate keys, refresh masks or sign until a new start-up test passes.

### Clock Jitter and the Accelerometer

Two further physical inputs need no parts, and both are credited with zero entropy like the ATECC608 and STM32 generators. Each is read once per `trng_get()` call, before any ADC sampling, and absorbed into every conditioner block of that call after the additional input; the block header carries flag `0x20` for the jitter and `0x40` for the accelerometer.

The clock jitter source (`firmware/src/jitter.c`) times the STM32's LSI, a free-running RC oscillator inside the chip near 32 kHz (17 to 47 kHz in the STM32F407 datasheet), against TIM5, which counts the 84 MHz timer clock derived from the board's 8 MHz crystal. TIM5 channel 4 can be connected to the LSI inside the chip through its option register (`TI4_RMP`), which ST provides for calibrating the LSI, so every LSI edge captures the counter and the difference between captures is one LSI period in ticks of about 11.9 ns. The two oscillators share nothing, and the RC oscillator's own noise moves each period by some ticks. 256 periods, about 8 ms, are taken per call. A missed edge sets the over-capture flag, and that period is dropped rather than counted double. `trng_jitter_ok()` asks for every period between 1500 and 6000 ticks, at least 4 distinct values, and no value in more than three quarters of the periods, so a stopped clock, a clock locked to the counter and a period counted from the wrong clock all fail. How many bits of the jitter are unpredictable has not been measured, which is why it is not credited; the `JITTER` command prints the mean, the spread and the standard deviation of a fresh run so the size of the jitter on a given board can be seen.

The accelerometer (`firmware/src/accel.c`) is the LIS3DSH on newer Discovery boards or the LIS302DL on older ones, on SPI1 (PA5, PA6, PA7, chip select PE3), told apart by `WHO_AM_I` (0x3F or 0x3B). The firmware runs the LIS3DSH at 1600 and the LIS302DL at 400 readings a second and takes 16 readings of all three axes per call, so about 10 ms or 40 ms. At rest the low bits of every axis still move with the sensor's mechanical and electronic noise, and movement of the board changes them too, which is one reason they cannot be credited. `trng_accel_ok()` asks for at least 8 readings, at least 2 distinct values on every axis, and not every value at 0 or -1, the pattern of a chip that does not answer. The LIS302DL's 8-bit readings may show no noise at rest, in which case it is left out at power-up as absent.

Both checks are tested on the PC in `tests/native/test_trng.c`, including 2000 jittery runs without a false alarm; whether the timer capture and the SPI reads work is confirmed only on the board, through `JITTER live` and `ACCEL live` in `INFO` and `jitter=1` and `accel=1` in `STATS`. On the first board measured both were live. The LSI ran at about 34.7 kHz, a mean period of 2422 ticks with a standard deviation of 2.3 ticks (about 27 ns), 12 distinct values in 256 periods; the mean moved by about 14 ticks between power-up and a later run, as an RC oscillator's frequency follows temperature. The accelerometer was a LIS3DSH, and with the board at rest 16 readings spanned 163, 197 and 249 counts on x, y and z (z near 15,400 counts, about one g), with 15 distinct values on the least varied axis.

### The Optional Second Source

The LM358 has two amplifiers and source A uses one. The second can carry an identical second source: another 2N3904 fed from the same filtered 12 V through its own 100 kohm, coupled into the second amplifier with the same gain of 11 around the same 1.5 V reference, and read by ADC2 on PB1 (`hardware/WIRING.md`, "Optional: A Second Noise Source"). ADC1 and ADC2 are started a few CPU cycles apart, so the two sources are sampled at practically the same instants.

At power-up the firmware pulls PB1 to ground, lets it go and samples it for 12.8 ms. A wired source B pulls the pin straight back to its 1.5 V reference with fast noise on top, so the firmware counts it as fitted only if the samples sit above about 0.75 V, vary at least as much as a live source must, and change from one sample to the next (a lag-1 autocorrelation below 0.9). A bare pin can creep up after the pull-down or pick up mains hum, but both are smooth at 20,000 samples a second, with an autocorrelation of 0.99 or more, so a board without source B can never mistake its empty pin for one. A source B that is wired but makes no noise is not counted either, and the device runs on source A alone. Once it was found at power-up it is required: if it later fails a health test, stops varying or disappears, the source faults with `FAULT_B` instead of quietly going back to one source.

With source B, every start-up test samples 1024 pairs, runs both sources through their own continuous tests, and computes the Pearson correlation of the two blocks. Two separate junctions give unrelated noise, and over 1024 samples the measured correlation of independent sources has a spread of about 0.031, so values near 0 are expected. A signal that reaches both, such as boost-converter ripple getting past the filter or a tone injected into the supply or the analog path, appears in both blocks at once and raises the correlation. Above 0.30 (`ZIRAH_MAX_CORR_X1000`), which is about ten times that spread and equals a common signal of about 30 % of each source's variance, the source faults with `FAULT_CORR` and nothing is signed until a later start-up test passes. `tests/native/test_trng.c` checks the arithmetic on synthetic noise: 5000 independent pairs of blocks never came above 0.123, while a common tone as strong as the noise, or a common signal of 45 % of the variance, is always caught. The limit is provisional until it is measured on the real board, and `ANALYZE` prints the measured value as `CORR_AB` for exactly that.

Source B does not change the entropy accounting. Each conditioner block still needs its full number of symbols from source A, and source B's symbols, taken at the same instants, are absorbed after them and credited with zero entropy. What source B adds is a second physical source in every block and the correlation check, which turns one of the limits listed below, a signal injected into the supply, into something the device can see.

### Recording the Conditions

`INFO` and `HEALTH` report the die temperature from the STM32's internal sensor, converted with the factory calibration values the chip stores for 30 °C and 110 °C (or the datasheet's typical values if those are missing), and `zirah capture` writes it into the capture's metadata before and after sampling, next to the boost voltage. Avalanche noise depends on temperature, so an entropy assessment should state it. The die runs a few degrees above the air around it, which matters for comparisons between captures rather than as an absolute value.

### Conditioning

Output is produced in 32-byte blocks. Each block is SHAKE256 over a domain label, a block counter, the number of sources (with flags for the clock jitter and the accelerometer) and the length of the additional input, the additional input itself, the jitter periods and accelerometer readings when they are live, and enough symbols from source A that `symbols * H >= 2 * 256` bits, that is, twice as much assessed entropy as output bits, followed by the same number of symbols from source B when it is fitted. H is `ZIRAH_H_ASSUMED_X100 / 100` from `firmware/src/zirah_config.h`. It is set to a deliberately low 1.00 bit per symbol. That is safe for any source that passes the health tests and costs only a few tens of milliseconds per signature, so the shipped `zirah.bin` never needs rebuilding for this. Raising it to a measured SP 800-90B value is possible but requires a rebuild.

### Assessment

`zirah capture` records raw samples, in pieces of 50,000 samples for long captures, and NIST's `ea_non_iid` tool (Linux or WSL2, see `data/entropy/README.md`) assesses the 8-bit symbols and the least significant bit. `analysis/entropy_report.py`, which runs on Windows, draws a time series, histogram and autocorrelation plot. A control capture with the boost disconnected shows how much of the measured variation comes from the avalanche junction rather than from the ADC and supply.

### Results on the DevEBox

One DevEBox with one Board A gave 1,000,000 samples on 5 November 2026, captured as 20 pieces of 50,000 with a CRC-32 check on each and no health-test failure. The samples are contiguous only inside the firmware's 2,048-sample blocks. Their mean was 2052.0 and their standard deviation 95.5 ADC steps, with nothing clipped. NIST's tool (commit 87c104d0) gave these results:

| Track | Result |
|---|---|
| IID tests, 8-bit symbols | chi-square and longest-repeated-substring tests passed; 3 of 19 permutation tests failed (runs about the median, covariance at lags 1 and 2), so the data are not IID |
| Non-IID, 8-bit symbols | H_original 7.059 (t-tuple), H_bitstring 0.862 (compression), assessed min(7.059, 8 x 0.862) = 6.898 bits per symbol |
| Non-IID, bit 0 | 0.784 bits (compression) |

Inside the blocks the lag-1 autocorrelation is 0.096, falling to 0.028 at lag 40, and the spectrum has lines at multiples of about 115.6 Hz holding about 5 % of the variance, plus about 4 % below 50 Hz. The lines are not at a multiple of the 50 Hz mains frequency, and without an oscilloscope their source was not found. The firmware still credits 1.00 bit per symbol: the result covers one board, one session and one supply, and the restart tests were not run.

### Limits of the Entropy Claims

- The assessment covers one board, one transistor, one temperature, one supply, and one capture session. Avalanche noise depends on all of them.
- The measured samples are correlated (lag-1 autocorrelation 0.096) and carry spectral lines of unknown origin near 116 Hz and its harmonics, about 5 % of the variance.
- A full SP 800-90B validation also needs the restart tests (section 3.1.4), an entropy justification of the noise source, and evidence about the conditioning component. Check section 3.1.5.1.1 for whether SHAKE256 as used here counts as a vetted conditioning component; if it does not, a formal validation would treat it as non-vetted.
- The output goes directly to the signer. There is no SP 800-90A DRBG in between, so Zirah is an entropy source with conditioning, not a complete SP 800-90C random bit generator.
- The health tests detect a dead or badly stuck source. They do not detect a subtle bias, an attacker injecting a signal into the supply or the analog path, or slow drift. With the optional second source fitted, a signal that reaches both sources and is strong enough to raise their correlation above 0.30 is caught; a weaker one, a signal that reaches only one source, and a slow drift still are not.

## Firmware Hardening

The firmware treats every part of the signing path as something that can fail, and fails closed.

- **Every signature is checked before it leaves.** After signing, the device runs a full ML-DSA verification of its own signature against its public key. A signature that does not verify may be the product of a fault and is never released; the buffer is wiped and signing locks until a power cycle.
- **Fresh entropy for every private-key operation.** Signing, key generation and identity proofs each start with a new SP 800-90B start-up test. A failing source stops the operation. With a provisioned ATECC608, its random output is mixed in as a second source credited with zero entropy (see above).
- **Physical presence.** Signing, replacing the key, turning off button confirmation and provisioning the ATECC608 each need a press of the board's button (K1 on the DevEBox, USER on the Discovery). A button already held down must be released first and a press must end within five seconds, so a shorted or held button is never taken as consent. Confirmation is always on again after power-up.
- **The digest is echoed.** The device announces the digest prefix it will show on the OLED, and the PC cancels at once if it differs from the digest the PC computed.
- **Memory protection.** The MPU marks SRAM and CCM execute-never and puts a no-access guard region above `.bss`, so a stack overflow faults instead of corrupting data. A stack-protector canary, randomised from the noise source at boot, guards every function with a buffer, and integer division by zero traps.
- **Faults wipe and halt.** Every fault handler switches to a private stack, wipes the CCM (where the masked key and all large work buffers live) and the remaining secret buffers, shows the reason on the OLED and halts with no automatic reset, so a fault cannot be retried for free.
- **No heap.** newlib's `malloc` is linked through `snprintf`, and with this linker script its heap would start on top of the masked key in CCM, so `_sbrk` always fails.
- **Bounded input.** Commands are one printable ASCII line of bounded length; anything else is rejected before parsing, and numeric arguments are checked for overflow.
- **Verified flash writes.** The key store checks the flash status after every write, reads the data back and compares it, erases the sector again on any failure, and the firmware refuses to start if its own image would overlap the key sector.
- **The build is identifiable.** At boot the firmware computes SHAKE256 over its own image in flash and reports the first 16 bytes as `FW_HASH`. That equals SHAKE256 of the `zirah.bin` that was flashed, so the app compares it with the `firmware/zirah.bin` in its own folder and warns Firmware Differs from This PC when the board runs another build. The hash identifies a build; it is not an attestation, since modified firmware could report any value.
- **Benchmarks never use the device key.** `BENCH` times the masked signing path with a throwaway key generated in RAM, so it cannot be used to collect power traces of the device key without a button press, the same reason `PROVE` is rate limited. Its signatures use their own context, are verified on the device and are never sent. The device key is reloaded from flash with fresh masks afterwards.
- **Readout protection is reported.** `INFO` reads the flash option bytes and reports the readout protection level (`rdp=` in the `HARDENING` line). The app and `zirah status` show Readout Protection Off as a warning at level 0, because the ML-DSA key in flash is then readable through the SWD port. The firmware only reports the level and never changes it; `zirah rdp-serial` (DevEBox) or `scripts\rdp.ps1` (Discovery) does that from the PC.
- **Separate identity context.** The identity proof (`PROVE`) signs `ZIRAH-PROVE-v1 || device ID || host nonce` under the context `zirah-attest-v1`, needs no button press, and is rate limited. Because verifiers pin the file-signing context, a proof can never pass as a file signature.

`docs/PROTOCOL.md` describes every command and reply.

### Two Boards

The firmware builds for two boards from one source tree (`make BOARD=discovery` or `make BOARD=devebox`, pins in `firmware/src/board_pins.h`): the STM32F4DISCOVERY with an STM32F407VG and 1 MB of flash, and the DevEBox "STM32F4XX_M" core board with an STM32F407VE and 512 KB. They carry the same die, so the clock, the RAM and CCM, the hardware generator, the MPU, the readout protection and every timing figure are the same; what differs is the board around it. On the DevEBox the user LED is wired to PA1, the pin the Discovery samples the noise on, so the noise moves to PA4 (ADC12_IN4); the key store moves from sector 11 to sector 7, the last 128 KB sector of the smaller flash; the serial link moves to USART1 on PA9 and PA10, the port the STM32F407's ROM bootloader also listens on (ST AN2606), so the same three wires flash the board and then carry its commands; and the USB core runs without VBUS sensing, since PA9 is taken and the board's USB-C socket does not wire VBUS to it. The board has no accelerometer, so that input is reported absent there.

The crystal on a generic board is not certain, and with a wrong guess the serial link runs at the wrong baud rate. The DevEBox build therefore starts the HSE with a timeout and measures it before the PLL is set up, while the CPU still runs from the 16 MHz HSI: TIM11 channel 1 can take HSE / RTCPRE inside the chip, so with RTCPRE = 8 and the input prescaler at 8 each capture spans 64 HSE cycles. The nearest of 8, 12, 16 and 25 MHz within 5 % is used, and anything else falls back to the HSI, which is close enough for the serial link but not for USB. `INFO` reports the result as `hse_mhz`.

Without an ST-LINK the board is flashed through that ROM bootloader with the AN3155 protocol (`host/zirah_flash.py`, `flash-devebox.bat`). Only the sectors the image occupies are erased, sector 7 never, every byte is read back and compared before the image is started, and readout protection level 1 is set through the bootloader's own command (`zirah rdp-serial`). The flasher is tested against a simulated bootloader that keeps the real sector layout, checks every checksum and refuses writes to unerased flash (`tests/host/test_flash.py`); the DevEBox port itself is confirmed only on the board.

## The Desktop App

The desktop app (`ui/`) is a local PySide6 program. It opens no network port and hosts nothing: the window, the device thread and the file handling all run in one process on the PC. One thread owns the serial port, so the live-noise polling can never interleave with a signature, and every rule about trust, verification and files comes from the same `host/zirah_core.py` the command line tool uses.

The connection lamp follows a fixed state machine: Disconnected, Searching, Connecting, Handshake, Checking, then Ready, Ready with Warnings, Trust Required or Not Ready, plus Busy during an operation and Simulation for the simulated device. Ready is only set when every required check passed on real hardware: compatible firmware and protocol, passed boot self-test, a healthy entropy source, a loaded key, unlocked signing and trusted keys. A missing OLED, button confirmation turned off, an unprovisioned ATECC608, inactive hardening or readout protection left at level 0 give Ready with Warnings and say which.

The signal window draws the same 512 raw ADC samples the device's OLED draws, fetched with `PEEK`, with the last few blocks fading like phosphor and a histogram that shares the trace's vertical scale. With no healthy samples it says No Signal and why. The stage rail lights each stage when the device reports it with an `EVT` line, never on a timer.

The trace is written the way an analog oscilloscope writes it. A beam sweeps each new block in from the left at a constant speed over 1.45 seconds, while the previous block stays in place to the right of the beam until the beam reaches it, and the histogram eases from the old distribution to the new one over the same sweep. The device sends a block about every 0.9 seconds, so a block that arrives during a sweep waits for it to finish, and the next sweep starts the moment one ends; the beam therefore moves steadily instead of jumping from block to block. Older blocks fade through a persistence layer in six small steps per sweep rather than all at once. The readouts under the window change when a block's sweep starts, so the numbers always describe the trace being drawn. With Reduce Motion on, each block appears at once.

Drawing cost is kept off the animation path. Qt's raster engine takes more than 100 ms to stroke 512 translucent anti-aliased segments in one go, which at 60 frames a second would hold the processor flat out and make the whole PC feel slow. The signal window therefore draws each new block once, with opaque pens, into its own image, keeps the grid and the fading older blocks in one opaque image, and repaints only the narrow strip the beam moved through plus the histogram on each frame. A block that has to wait for the beam is drawn as soon as it arrives, while the beam has frames to spare, so the frame that starts its sweep only swaps images; the trace uses square line caps, because round caps made Qt stroke the 511 segments about ten times slower. On the test machine at 150 % display scaling a frame averaged 2.1 to 2.7 ms over several runs, the longest single pass of the event loop, which includes a block's arrival and its sweep start, usually took 10 to 25 ms against 35 to 50 ms before those two changes (one run out of about ten had a single 59 ms pause that did not recur in repeats, the kind of stall the operating system causes), and the main thread used about 15 % of one core while the beam runs. Page and tab changes cross-fade between two snapshots instead of re-rendering the page every frame. `tests/ui/perf.py` fails the `app` suite if the signal window needs more than 30 ms a frame on average or more than one pass of the event loop holds the window for more than 60 ms, and `tests/ui/scope_check.py` checks that a block drawn ahead gives exactly the same pixels as one drawn at its sweep start and is drawn again after a resize.

The search for the board lists serial ports every 1.5 s for the first 15 s and every 6 s after that, because enumerating ports is slow on some Windows PCs, and on Windows the window also starts a search the moment a USB device is plugged in. After a connection error, auto-connect retries quietly in the background and announces the same error only once.

File paths come only from native file dialogs or files dropped on the window. The history keeps the time, the kind of operation, the file's base name, the first eight digits of its digest and the result, never contents, full paths, keys or signatures, and can be cleared. Diagnostics exports the conversation with the device with every long hex value elided.

The simulated device (`host/zirah_sim.py`) speaks the same protocol, so the app and the tests exercise the real host code. It is a separate transport class, which is how the host knows it is simulated; the device itself is never asked.

## Threat Model

| Attacker | Capability | Zirah's Answer | Residual Risk |
|---|---|---|---|
| Malicious or infected PC | Send any digest for signing; lie on screen about what is being signed | Physical button press for every signature (K1 or USER); OLED shows the first 8 hex digits of the digest being signed | The PC can show the user a matching fake digest. The button prevents silent signing, but does not prove which file is signed unless the user checks the digest independently. 8 hex digits is only 32 bits, so a determined attacker can also search for a different file whose digest starts the same way. |
| Person with brief physical access and a debugger | Read flash over SWD | ECDSA key safe inside the ATECC608. RDP level 1 stops the debugger reading the ML-DSA key | Without RDP the flash, key included, is readable over SWD. With RDP 1, published bypasses of STM32 readout protection remain. |
| Side-channel attacker with a probe and many traces | Measure power or EM during signing | First-order masking of c * s1 and c * s2, refreshed per signature | y, the recombination, the rejection checks and the boot-time key load are unmasked (see above). |
| Fault-injection attacker | Disturb the clock, supply or EM during signing | Hedged signing defeats the differential fault attacks that need deterministic signatures (Bruinderink and Pessl, TCHES 2018). Every signature is verified on the device before release, a failing one is withheld and signing locks, and CPU faults wipe secrets and halt without resetting | A fault that corrupts both the signature and its check in a consistent way, or one that targets the key load at boot, is not addressed. |
| Attacker on the entropy source | Inject noise or a periodic signal, cool or heat the board | Health tests catch a stuck or strongly repeating source | Subtle manipulation is not detected. |
| Malicious USB host on the serial port | Send malformed or oversized commands | Printable bounded lines only, strict argument parsing, no heap, MPU guard and canary | Firmware bugs that pass these checks. |
| Network attacker | None | Neither the device nor the desktop app listens on a network | none |

## Claims You Can and Cannot Make

| You Can Say | You Cannot Say |
|---|---|
| The masked ML-DSA-65 signer produces signatures byte-identical to the pq-crystals reference implementation and verified by an independent implementation. | Zirah is a first-order secure or side-channel resistant ML-DSA implementation. |
| The secret-key multiplications c * s1 and c * s2 are computed on two arithmetic shares that are refreshed before every signature. | The device resists side-channel attacks in general. |
| Under the ELMO leakage models, the two-pass masked multiplication, with and without shuffling, shows no first-order leakage (TVLA, threshold 4.5, 5000 traces per group), while the unmasked and naively masked versions do. | The real STM32F407 shows no leakage. No physical measurement was made. |
| Every pass over a key share runs in a fresh random coefficient order, and the signatures stay byte-identical. | Shuffling has been shown to stop a particular attack on this device. |
| The assembly NTT gives byte-identical keys and signatures to the C code in every case tested on an emulated Cortex-M4. | The assembly has been verified formally, or its timings were measured in QEMU. |
| With the second source fitted, the device refuses to sign when the two sources correlate above 0.30 at a start-up test. | The device detects every injected or common-mode signal. |
| The ATECC608 and the STM32 generator are mixed into every key, mask refresh and signature, credited with zero entropy. | Those generators raise the assessed entropy, or were assessed themselves. |
| `BENCH` timings come from the CPU cycle counter on the board. | QEMU instruction counts are cycle counts of the real chip. |
| The LSI clock jitter and the accelerometer readings are mixed into the conditioner when they pass their checks, credited zero. | Either adds assessed entropy, or the accelerometer readings are free of influence from movement of the board. |
| The entropy source passed SP 800-90B health tests and the non-IID estimator gave H bits per 8-bit symbol on this board (state the conditions). | The TRNG is NIST certified, validated or compliant. |
| The ATECC608 protects the classical P-256 key; the post-quantum key cannot be placed in it and sits in STM32 flash, readable over SWD at RDP level 0. | The secure element makes the device post-quantum secure. |
| On the earlier Discovery board at RDP level 0, the ML-DSA key was copied out over SWD and used to forge a valid ML-DSA-65 signature, though not the ECDSA half. | That test was repeated on the DevEBox, or its logs are part of this release. |
| On the DevEBox, RDP level 1 was set and the firmware reported level 1 in every check afterwards; the reference manual says this blocks debugger and bootloader reads of the flash. | RDP level 1 makes reading the key impossible, or a debugger read was tried on the DevEBox. |
| Every released signature passed an on-device verification and a PC verification. | The device resists fault attacks. |
| The hybrid signature requires both ML-DSA-65 and ECDSA P-256 to verify. | The hybrid construction is standardised or certified. |
| A valid signature covers the file's content, name and size and the signing time and file times as the signing PC reported them; a signed copy that was edited or extended afterwards fails. | The signing or modification times are independently true, or a failed check shows what was changed or when. |

## References

Check each entry against the publisher's page before citing it in the report.

1. NIST, *FIPS 204: Module-Lattice-Based Digital Signature Standard*, August 2024. <https://doi.org/10.6028/NIST.FIPS.204>
2. M. S. Turan, E. Barker, J. Kelsey, K. McKay, M. Baish, M. Boyle, *NIST SP 800-90B: Recommendation for the Entropy Sources Used for Random Bit Generation*, January 2018. <https://doi.org/10.6028/NIST.SP.800-90B>
3. NIST SP 800-90B Entropy Assessment tool. <https://github.com/usnistgov/SP800-90B_EntropyAssessment>
4. CRYSTALS-Dilithium reference implementation. <https://github.com/pq-crystals/dilithium>
5. V. Migliore, B. Gerard, M. Tibouchi, P.-A. Fouque, "Masking Dilithium: Efficient Implementation and Side-Channel Evaluation", ACNS 2019, LNCS 11464.
6. M. Azouaoui et al., "Protecting Dilithium against Leakage: Revisited Sensitivity Analysis and Improved Implementations", IACR Transactions on Cryptographic Hardware and Embedded Systems, 2023 (4).
7. J. Balasch, B. Gierlichs, V. Grosso, O. Reparaz, F.-X. Standaert, "On the Cost of Lazy Engineering for Masked Software Implementations", CARDIS 2014, LNCS 8968.
8. D. McCann, E. Oswald, C. Whitnall, "Towards Practical Tools for Side Channel Aware Software Engineering: 'Grey Box' Modelling for Instruction Leakages", USENIX Security 2017. Tool: <https://github.com/sca-research/ELMO>
9. G. Goodwill, B. Jun, J. Jaffe, P. Rohatgi, "A Testing Methodology for Side-Channel Resistance Validation", NIST Non-Invasive Attack Testing Workshop, 2011.
10. L. G. Bruinderink, P. Pessl, "Differential Fault Attacks on Deterministic Lattice Signatures", IACR Transactions on Cryptographic Hardware and Embedded Systems, 2018 (3).
11. J. Obermaier, S. Tatschner, "Shedding too much Light on a Microcontroller's Firmware Protection", USENIX WOOT 2017 (attacks on STM32F0 readout protection).
12. Microchip CryptoAuthLib. <https://github.com/MicrochipTech/cryptoauthlib>
13. N. Veyrat-Charvillon, M. Medwed, S. Kerckhof, F.-X. Standaert, "Shuffling against Side-Channel Attacks: A Comprehensive Study with Cautionary Note", ASIACRYPT 2012, LNCS 7658.
14. D. O. C. Greconici, M. J. Kannwischer, D. Sprenkels, "Compact Dilithium Implementations on Cortex-M3 and Cortex-M4", IACR Transactions on Cryptographic Hardware and Embedded Systems, 2021 (1). <https://eprint.iacr.org/2020/1278>
15. pqm4, post-quantum cryptography on ARM Cortex-M4. <https://github.com/mupq/pqm4>
16. O. Reparaz, J. Balasch, I. Verbauwhede, "Dude, is my code constant time?", Design, Automation and Test in Europe (DATE) 2017.
