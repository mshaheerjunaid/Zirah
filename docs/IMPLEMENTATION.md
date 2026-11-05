# Zirah Requirements and Implementation

This page lists what Zirah 1.0.0 is required to do and, for each requirement, where it is implemented and how it is checked. "Tested" names a suite of `tests/run_tests.py`; "Inspected" means checked by reading the code or the rendered output, because no automatic test covers it; "Hardware" means it can only be confirmed on the physical board.

## Test Suites

| Suite | Scope | Needs |
|---|---|---|
| `oled` | the firmware's OLED drawing code built for the PC; 43 screens rendered, fails on any pixel drawn off-screen | gcc or clang |
| `mldsa` | `tests/native/test_mldsa.c` against the firmware's ML-DSA code, 1976 checks including the shuffling, ASan and UBSan where available; prints a transcript hash of every key and signature | gcc or clang |
| `entropy` | `tests/native/test_trng.c` against `firmware/src/trng_logic.c`: the SP 800-90B cutoffs, the continuous tests on healthy, stuck and biased synthetic sources, the integer statistics, the A-B correlation on independent and on commonly driven sources, the test that finds the second source, and the checks of the LSI clock jitter (fixed, alternating and mostly fixed periods, periods out of range, 2000 jittery runs without a false alarm) and of the accelerometer (a frozen axis, a silent bus, 8-bit readings with one count of noise), 2234 checks under ASan and UBSan where available | gcc or clang |
| `timing` | `tests/native/dudect.c`: Welch's t-test between a fixed and a random secret on the time of six key-handling kernels (constant-time compare, share product, both NTTs, reduction, mask refresh), with a leaky compare that must be caught | gcc or clang on x86 |
| `kat` | the boot self-test vectors recomputed with dilithium-py | dilithium-py |
| `style` | the Title Case rule over every OLED and desktop string, and over any literal text handed to a widget | Python |
| `host` | 64 unit tests of `host/zirah_core.py`, `host/zirah_embed.py`, `host/zirah_validate.py` and `host/zirah_flash.py` with the simulator, scripted transports and a simulated STM32 bootloader | Python packages |
| `app` | the real desktop window driven offscreen through hashing, signing, the PC checks, saving, verifying and the self-check; the program started and quit the way `zirah-app.bat` runs it; the signal window's drawing ahead checked pixel for pixel against drawing at the sweep start; the drawing time of the signal window at 150 % scaling against a 30 ms budget per frame and a 60 ms budget per pass of the event loop, which one pass alone may exceed | PySide6 |
| `qemu` | `tests/qemu/`: the assembly NTT against the C NTT on an emulated Cortex-M4 (QEMU `mps2-an386`), and the whole `mldsa` program built for the Cortex-M4 with the assembly, whose transcript must equal the PC build's | arm-none-eabi-gcc, qemu-system-arm |
| `firmware` | the ARM build for both boards with every warning counted as a failure, no division instruction in the 12 objects that handle the key, the flash range that `FW_HASH` covers exactly as long as the image and ending below the key sector, and the shipped `zirah.bin`, `zirah-devebox.bin` and their `.sha256` files identical to the fresh builds | arm-none-eabi-gcc, make, libopencm3 |

On the build machine for this release all ten suites passed under Python 3.12 and 3.13 (Linux, PySide6 6.12, GCC 13.3 for the PC and arm-none-eabi-gcc 13.2.1 for the firmware). They were run again on 5 November 2026, after the host fixes made during the DevEBox measurements, on Ubuntu 24.04 with Python 3.13.18, GCC 13.3.0, arm-none-eabi-gcc 13.2.1, QEMU 8.2.2 and PySide6 6.8.2.1, and all ten passed; the `firmware` suite rebuilt `zirah.bin` and `zirah-devebox.bin` byte for byte. On a plain Windows install the three C suites and the firmware suite report SKIP until a C compiler and the ARM toolchain are installed, and the `qemu` suite needs QEMU, which in practice means Linux or WSL2.

## Signing Device

| Requirement | Implementation | Checked |
|---|---|---|
| ML-DSA-65 signatures per FIPS 204, pure mode, context `zirah-doc-v1`, hedged | `firmware/src/zirah_mldsa.c`, `main.c` `cmd_sign` | Tested (`mldsa`, `kat`, `host`) |
| Masked secret-key multiplications with per-signature refresh | `zirah_msk_load`, `zirah_msk_refresh`, `zirah_sign_masked_internal` | Tested: masked output equals the reference across refreshes |
| Every pass over a key share in a fresh uniform random order, and a random share order | `prg_perm`, `refresh_poly`, `split_poly`, `poly_pointwise_shuffled` in `zirah_mldsa.c` | Tested (`mldsa`): the order is a permutation, uniform at the first and last position, renewed by every refresh, and no seed changes a signature |
| Cortex-M4 assembly NTT with byte-identical results | `firmware/mldsa-m4/`, `NTT=asm` in the Makefile | Tested (`qemu`) |
| No secret-dependent timing in the key-handling kernels | `secure.c`, `zirah_mldsa.c`, the reference arithmetic | Tested (`timing`) on the PC's x86 build; the ARM objects are checked for the variable-time divider (`firmware`); no Cortex-M4 timing measurement |
| ATECC608 random mixed into every key generation, signature and identity proof, credited zero | `mix_extra_random` in `main.c`, `trng_set_extra_input` | Inspected; Hardware (`ecc_rng=1` in `STATS`, `ECC_RNG mixed` after `KEYGEN`) |
| Readout protection level reported, Readout Protection Off shown as a warning | `board_rdp_level`, `HARDENING ... rdp=` in `INFO`, `readiness()` | Tested (`host`, `test_readout_protection_off_is_a_warning`); the register read on Hardware |
| LSI clock jitter (TIM5 channel 4 capturing the LSI against the 84 MHz timer clock) and the board accelerometer (LIS3DSH or LIS302DL on SPI1) mixed into every conditioner block as further inputs credited zero, checked at power-up and at every read, left out after a failure | `jitter.c`, `accel.c`, `trng_jitter_ok`, `trng_accel_ok`, `trng_get` | Tested (`entropy`) for the checks; the timer capture and SPI reads on Hardware (`JITTER live` and `ACCEL live` in `INFO`, `jitter=1` and `accel=1` in `STATS`) |
| STM32 generator mixed in as a further input credited zero, with its seed, clock and repeat checks | `mcu_rng.c`, `mix_extra_random` in `main.c` | Inspected; Hardware (`stm32_rng=1` in `STATS`, `STM32_RNG ok` in `INFO`) |
| Optional second noise source found at power-up, tested on its own, mixed into every block, credited zero, and required once found | `trng.c` (`probe_b`, `trng_sample_pair`, `trng_startup`), `trng_logic.c` | Tested (`entropy`) for the logic; the ADC2 sampling on Hardware |
| A-B correlation above 0.30 at a start-up test faults the source | `trng_startup`, `trng_corr_x1000`, `ZIRAH_MAX_CORR_X1000` | Tested (`entropy`) on synthetic sources; the threshold is provisional until measured on the board |
| Die temperature reported and written into capture metadata | `adc_temp_c10`, `TEMP_C10`, `cmd_capture` | Tested (`host`) for the parsing; the reading on Hardware |
| The board's build compared with this PC's `zirah.bin` | `fw_hash_compute`, `FW_HASH`, `local_firmware_hash`, the `fwmatch` check | Tested (`host`, `test_firmware_build_is_compared_with_this_pcs_zirah_bin`; `firmware`, the hashed flash range equals the image); the hash itself on Hardware |
| Verification without a board: the file, signature and key file dropped together and sorted by content, the signed and the file's digest side by side, the signer's fingerprints shown for comparison with published ones | `VerifyPage` (`add_files`), `classify_file`, `VerifyReport.signer`, `zirah verify` | Tested (`host`, `test_verify_inputs.py`; `app`, files dropped together in the wrong order) |
| One command that measures the whole board and writes the results up, refusing the simulator | `host/zirah_validate.py`, `zirah validate` | Tested (`host`, `test_validate.py`) for the parsing, the report and the refusal; the measurements on Hardware |
| Benchmarks of the real signing path with a throwaway key, never the device key | `cmd_bench`, `bench_cref.c`, `parse_bench`, `zirah bench`, the Device page | Tested (`host`, `test_benchmark_reply`) for the parsing and the simulator's refusal; the timings on Hardware |
| Every signature verified on the device before release; failure withholds it and locks signing | `sign_and_check` in `main.c`, `zirah_verify_ctx` | Tested (`mldsa` for the verifier); the lock path by inspection |
| Fresh SP 800-90B start-up test before every private-key operation | `entropy_fresh` in `main.c` | Inspected; Hardware |
| A button press (K1 on the DevEBox, USER on the Discovery) for signing, key replacement, confirmation off and ATECC608 provisioning; held or stuck button is never consent | `confirm_press` in `main.c` | Inspected; Hardware. The host side of the flow (prompt, cancel, timeout) is Tested against the simulator |
| Digest prefix echoed for the host to compare | `EVT CONFIRM doc=...` | Tested (`host`, `DigestMismatch`) |
| Boot known-answer tests from independently computed vectors; failure locks signing | `boot_kat`, `kat_vectors.h` | Tested (`kat`, `mldsa`) |
| Key generation with a pairwise consistency test before storing | `cmd_keygen`, `zirah_keypair` | Tested: key generation equals the reference; PCT by inspection |
| Key generation and verification without the reference code's 58 KB stack frames | `zirah_keypair`, `zirah_verify_ctx` with static and CCM work memory | Tested (`mldsa`); `make stack` reports the frames |
| Identity proof under its own context, rate limited | `cmd_prove` | Tested (`host`, `test_identity_proof`) |
| MPU execute-never, stack guard, randomised canary, divide-by-zero trap | `board.c` | Inspected; the `firmware` suite builds with `-fstack-protector-strong` and zero warnings |
| Faults wipe secrets and halt without reset | `zirah_fault_entry`, fault trampolines | Inspected |
| Heap disabled | `_sbrk` in `board.c` | Inspected in the link map |
| Bounded, printable command lines and overflow-safe parsing | `io.c`, `parse_u32` | Inspected; the host side is Tested |
| Verified flash writes and an image that cannot overlap the key sector | `keystore.c` | Inspected |
| Secrets wiped with a store the compiler keeps; constant-time compares | `secure.c` | Tested (`mldsa`, helper tests) |

## OLED

| Requirement | Implementation | Checked |
|---|---|---|
| Graphics only from real data: live trace of measured samples, No Signal otherwise | `oled_ui.c` `signal_window`, `main.c` `monitor_tick` | Tested (`oled`); Hardware for the live data |
| Stage strip that follows the real firmware stages | `op_stage` in `main.c`, `stage_strip` | Tested (`oled`) |
| Fault screens for the second source and for correlated sources, and a benchmark screen | `fault_lines`, `ui_text.h` | Tested (`oled`, 43 screens) |
| No clipped text on any screen | `tests/native/oled_preview.c` with an off-screen pixel counter | Tested (`oled`) |
| Title Case text | `firmware/src/ui_text.h` | Tested (`style`) |
| Only changed columns are sent over I2C | `oled_flush` | Inspected |
| The official logo at boot | `logo_bitmap.h`, rendered from the vector logo | Inspected (`docs/assets/oled-screens.png`) |

## Host Core

| Requirement | Implementation | Checked |
|---|---|---|
| Strict parsing: exact hex lengths, bounded lines, no control characters | `zirah_core.py` `Link`, `parse_hex` | Tested (`host`) |
| Pinned contexts that a file cannot choose | `CTX_DOC`, `verify_detached` | Tested (`test_context_cannot_be_chosen_by_the_file`) |
| Signing only with trusted keys; changed keys reported, never replaced silently | `TrustStore` | Tested (`test_refuses_untrusted_key`, `test_changed_key_is_reported_not_replaced`) |
| PC-side verification and a second hash before a signature or signed copy is written, and a signed copy read back before it is published | `sign_file`, `publish_new_file` | Tested (`test_file_changed_during_signing_saves_nothing`, `test_embed.py`) |
| The name, size, format, signing time and the file's creation and modification times signed with the content | `make_manifest`, `manifest_digest`, `parse_record` | Tested (`test_signed_name_and_times_cannot_be_edited`, and a forged signing time in every signed-copy format) |
| Signed copies for PDF, Office Open XML, ZIP, PNG and JPEG that open like the original and give back its exact bytes; a separate `.zirah.json` for everything else | `host/zirah_embed.py`, `signing_container` | Tested (`host`, `test_embed.py`: every format round trip, well-formedness, edits, appended data, a record moved to a separate file); opened in LibreOffice, qpdf, python-docx, python-pptx, openpyxl and Pillow by inspection; not tested in Microsoft Office or Acrobat |
| Signed times kept apart from the verifying PC's file-system times and the time of verification | `VerifyReport.times`, the Verify page's When section, `zirah verify` | Tested (`test_report_separates_signed_and_file_system_times`) |
| Version 1 signature files keep verifying | `parse_record` | Tested (`test_verify_inputs.py`) |
| Atomic writes that never overwrite | `write_new_file` | Tested (`test_write_new_file_never_overwrites`, `test_existing_destination_is_not_overwritten`) |
| Hybrid policy: once a trusted ECDSA key exists, both halves are required | `verify_detached` | Tested |
| Simulated results can never pass as hardware signatures | `SimTransport.simulated`, `simulated` flag, in-memory sim trust | Tested (`test_missing_simulated_field_counts_as_simulated`, `test_sign_and_verify`) |
| Cancellation while waiting for the button, with a usable link afterwards | `sign_digest`, `Link` resynchronisation | Tested (`test_cancel_before_button`) |
| Text from the device sanitised before display | `clean_text` | Tested |
| Exclusive serial port on Windows and POSIX | `SerialTransport` | Inspected |

## Desktop App

| Requirement | Implementation | Checked |
|---|---|---|
| A local program started from a BAT file, with no console, no hosting and no network port | `zirah-app.bat`, `ui/zirah_app.py` (PySide6) | Inspected |
| One instance at a time | named mutex on Windows, file lock elsewhere | Inspected |
| Errors inside the window are logged without secrets and reported, and the app keeps running | `install_error_log` in `ui/zirah_app.py` | Inspected; checked by hand with a deliberately failing slot |
| Connection states that are never falsely Ready | `manager.py` state machine, `readiness` in the core | Tested (`host`, readiness tests) |
| Live avalanche-noise view from real samples, clearly marked when simulated | `widgets.py` `Scope`, `PEEK` | Tested (`app`); Hardware for real samples |
| Smooth drawing that does not load the PC: each noise block drawn once into cached images, ahead of its sweep when it has to wait, only the strip around the beam repainted, under 30 ms a frame and at most one pass of the event loop over 60 ms | `Scope` image layers, `Scope._prepare` and partial updates, `crossfade` for page changes | Tested (`app`, `tests/ui/perf.py` and `tests/ui/scope_check.py`, 2.1 to 2.7 ms a frame and a longest pass of usually 10 to 25 ms on the build machine) |
| A slow, steady sweep: each block written by a beam over 1.45 s at constant speed, blocks queued so the beam never jumps, older blocks fading in small steps, readouts in step with the trace | `Scope.push`, `_start`, `_on_sweep`, `shown` signal | Inspected in the rendered screens; Tested (`app`) for the frame time |
| Second source, correlation, extra generators, die temperature, build check and benchmark in the Device page | `page_device.py`, `manager.bench` | Tested (`app` renders the page); Hardware for the values |
| Quitting stops the device thread and exits without an error | `DeviceManager.stop`, `MainWindow.closeEvent` (asks first if an operation is running) | Tested (`app`, `tests/ui/lifecycle.py`, Python 3.12 and 3.13) |
| The trust step never shows the fingerprint it expects, so the typed value must come from the OLED | `trust_dialog.py`, `cmd_trust` in `host/zirah.py` | Inspected |
| The signed file is never modified; the signature goes into a signed copy or a separate file, a file that is already signed is refused, and the app says which | `sign_file`, `page_sign.py` | Tested (`host`, `test_pdf_becomes_a_signed_copy_and_cannot_be_signed_twice`; `app`, the PDF in the sign flow becomes a signed copy that verifies by itself) |
| Unreadable files and unwritable folders give a clear message instead of an internal error | `hash_file`, `write_new_file` (`READ_FAILED`, `WRITE_FAILED`) | Tested (`host`) |
| Port search that backs off, starts at once when a USB device is plugged in, and retries quietly after an error | `manager.py` `_tick`, `MainWindow.nativeEvent` | Inspected; Windows only for the plug-in notice |
| Stage rail driven only by device events | `StageRail`, `EVT` lines | Tested (`app`) |
| File signing with drag and drop, metadata, destination, progress, cancel, success and failure views | `page_sign.py` | Tested (`app`) |
| Verification with a report of every check | `page_verify.py` | Tested (`app`) |
| Self-check, keys and trust, entropy health, firmware details, button confirmation | `page_device.py`, `trust_dialog.py` | Tested (`app` runs the self-check) |
| Minimal history with a clear control and no sensitive data | `store.py`, `page_history.py` | Inspected |
| Exportable diagnostics without secrets | `manager.diagnostics`, `diag_dialog.py` | Inspected |
| Animations: launch sequence, lamp, phosphor trace, stage rail, tabs, toggles, toasts, seal, digest reveal; Reduce Motion turns them off | `widgets.py`, `Motion` in `theme.py` | Inspected in the rendered screens |
| Keyboard use with visible focus only after keyboard navigation | `KeyboardFocus` in `widgets.py`, tab order | Inspected |
| The project palette as tokens; Chakra Petch, IBM Plex Sans and IBM Plex Mono bundled | `theme.py`, `ui/fonts` | Inspected |
| Title Case for every interface string | `ui/strings.json` | Tested (`style`) |
| The official logo, sharp at any size | `brand/vectorize_logo.py`, `widgets.BrandLockup`, multi-size icons | Inspected |

## Windows Scripts

| Requirement | Implementation | Checked |
|---|---|---|
| Separate setup step, private environment, clear errors | `setup.bat`, `scripts/shortcut.ps1` | Inspected; Windows only |
| Path-safe launchers (spaces and brackets in the folder name) | quoted paths, no paths inside parenthesised blocks, values passed to PowerShell through the environment | Inspected; Windows only |
| Verified firmware flashing | `flash.bat`, `scripts/flash.ps1` with the SHA-256 check | Inspected; Hardware |
| Readout protection status and control | `scripts/rdp.ps1 -Level Status|1|0` | Hardware |
| Flashing and readout protection without an ST-LINK (DevEBox), through the ROM bootloader: only the image's sectors erased, the key sector never, every byte read back | `flash-devebox.bat`, `zirah flash`, `zirah rdp-serial`, `host/zirah_flash.py` | Tested (`host`, `test_flash.py`, against a simulated bootloader); Hardware |
| DevEBox STM32F4XX_M port: noise on PA4, serial on PA9/PA10, key in sector 7, K1 and LED D2, USB without VBUS sensing, the crystal measured at power-up | `board_pins.h`, `board.c`, `io.c`, `make BOARD=devebox` | Built (`firmware`); Hardware |

## Not Claimed

No formal certification, no independent audit and no physical side-channel or fault-injection measurement exist for Zirah. The limits in `docs/DESIGN.md` ("Claims You Can and Cannot Make") apply to everything above.
