# Using Zirah on Windows

Nothing here needs compiling or an online service. The firmware is already built (`firmware/zirah.bin`), and the desktop app, the command line tool and the board do everything else. Each step below shows the desktop app first and the matching command for anyone who prefers a terminal.

## 1. Install Two Programs Once

- **Python 3.10 or newer** from <https://www.python.org/downloads/windows/>. On the first screen tick **Add python.exe to PATH**.
- **STM32CubeProgrammer** from <https://www.st.com/en/development-tools/stm32cubeprog.html> (ST asks for a free login before the download). Accept the ST-LINK driver install when it offers it. It is only needed for the Discovery; the DevEBox board is flashed without it.

Then double-click **`setup.bat`** in the Zirah folder. It creates a private Python environment in `.venv` inside the folder, installs the packages into it (PySide6 is large, so give it a few minutes), adds Zirah to the Start Menu and asks whether you also want a Desktop shortcut. Nothing outside the Zirah folder changes apart from those shortcuts.

## 2. Wire Both Breadboards

Follow `hardware/WIRING.md` with both USB cables unplugged. Leave the 100 uF capacitor (C1) out for now.

## 3. Flash the Board

1. Plug the **mini-USB** cable into the top socket of the Discovery (CN1, the ST-LINK side).
2. Double-click **`flash.bat`**. It checks `zirah.bin` against `zirah.bin.sha256` and writes it to the board. The firmware stays on the chip from then on, and reflashing leaves the key in flash sector 11 alone.

### On the DevEBox STM32F4XX_M Board

This board has no ST-LINK, so it is flashed through the STM32's own serial bootloader and the USB-TTL adapter, and STM32CubeProgrammer is not needed for it. `hardware/WIRING.md` lists the five pins that differ from the Discovery.

1. Wire the adapter: its **TXD to PA10**, its **RXD to PA9**, its **GND to a GND pin**. Leave its 5V and 3V3 pins free. These are the same wires Zirah uses afterwards, so they stay in place, and in step 4 Zirah finds the adapter by itself; skip the micro-USB cable there.
2. Plug the **USB-C** cable into the board for power.
3. Put a wire from **BT0 to 3V3** on the small five-pin SWD header at the top of the board, and press **RST**.
4. Double-click **`flash-devebox.bat`**. It checks `zirah-devebox.bin` against its `.sha256` file, finds the adapter, erases only the sectors the firmware needs (never sector 7, where the key lives), writes the firmware, reads every byte back to compare, and starts it.
5. Take the BT0 wire out and press **RST**. `zirah info` then shows `BOARD devebox`, and the board's LED D2 lights only for faults and button prompts, since the OLED shows the rest.

`zirah info` also prints the crystal the board found (`hse_mhz=8` on the boards sold so far). The firmware measures it at power-up, so a board fitted with a 12, 16 or 25 MHz crystal works too. `hse_mhz=0` means no crystal answered and the board runs from its internal oscillator, which is fine for the serial link but too loose for its USB socket.

## 4. Connect and Start Zirah

1. Plug a **micro-USB data cable** into the bottom socket (CN5) as well. Keep the mini-USB in, because it powers the board.
2. Start **Zirah** from the Start Menu, or double-click `zirah-app.bat`.

The lamp at the top left of the window tells you where things stand. It breathes cyan while Zirah searches for the board and runs the handshake, turns green at **Ready**, amber at **Trust Required** or **Ready with Warnings**, and red at **Not Ready**, with the reason written underneath. The Readiness list below it shows every check behind that verdict. Zirah never shows Ready unless every required check passed on the real device. At this point **Entropy Source Not Healthy** is expected, because the boost voltage has not been set yet.

If you would rather not use the micro-USB socket, a USB-TTL adapter (CP2102, CH340 or FTDI) works as the data link instead. Wire the adapter's **RXD to PA2**, its **TXD to PA3** and its **GND to a GND pin**, and leave its +5V and 3V3 pins unconnected while the mini-USB cable powers the board. Zirah finds a single USB-TTL adapter on its own when no board is plugged into CN5.

The adapter can also power the board on its own: its **+5V to a Discovery 5V pin**, with no mini-USB cable plugged in. Then pull that +5V wire out every time you plug in the mini-USB cable to flash, and put it back afterwards. The two supplies must never be connected at the same time, because the ST-LINK's 5V and the adapter's 5V would then push against each other.

Command line: open a command window in the Zirah folder (click the folder's address bar in File Explorer, type `cmd`, press Enter) and run `zirah status`.

## What the OLED Shows

The idle screen is a small instrument of its own. The top bar shows ZIRAH and the device state (READY, NO KEY or LOCKED). Below it the live trace draws the raw noise samples the firmware just measured; it is the same block the app's signal window shows, and with no healthy samples the screen says NO SIGNAL instead of drawing anything. The bottom lines show the fingerprints of your two public keys (`PQ` and `EC`), the boost voltage and the number of signatures since power-up. An exclamation box in the top bar means button confirmation has been switched off.

While signing, the screen first shows **SIGN?** with the first eight digits of the file's digest and a countdown. After you press the button (K1 on the DevEBox, USER on the Discovery) it steps through ENTROPY, MASKS, ML-DSA, VERIFY and ECDSA as they actually run, and ends with **SIGNED** and the result of each half. `docs/assets/oled-screens.png` shows the main screens.

## 5. Set the Boost to 12 V and Add C1

1. Run `zirah vbias --watch` and turn the screw on the MT3608 until it reads about 12.0 V, then press Enter.
2. Unplug both cables, fit the 100 uF capacitor (long leg in hole 6a, short leg in the L- rail) and plug back in.
3. In the app, open **Device** and press **Run Health Test**, or run `zirah health`. The entropy source should report OK, and the signal window starts drawing live noise.
4. `zirah analyze` prints noise statistics. If they look wrong, see the table at the end of `hardware/WIRING.md`.

## 6. Set Up the ATECC608 Once, Permanently

Run `zirah atecc provision`, type `I UNDERSTAND`, and press the board's button when asked. The chip generates its own ECDSA key and locks itself for good. Locking cannot be undone, and that is the point: nobody can read that key out afterwards. This step is deliberately only in the command line tool, where it needs a typed confirmation, a one-time code and the button.

## 7. Make Your Post-Quantum Key Once

In the app, open **Device** and press **Generate Key**, or run `zirah keygen`. The board makes an ML-DSA-65 key from the noise source, proves it with a pairwise consistency test (sign and verify through the masked path), and only then stores it. Replacing an existing key needs a press of the board's button and destroys the old key.

## 8. Trust the Device

Signing only works with keys you have checked against the device itself. Press **Trust This Device** in the app, read the **PQ** line on the OLED and type those eight characters. The app deliberately does not show the value it expects, so what you type has to come from the board. Zirah compares it with the key it received over USB and pins the keys on this PC. Command line: `zirah trust`.

If the keys ever change (a new key, or a freshly provisioned ATECC608), Zirah says **Key Changed** and refuses to sign until you confirm the replacement in the same way. A key never changes silently.

## 9. Sign and Verify Files

- **Sign.** Drop a file onto the Sign page, or press **Choose File**. Zirah hashes it and shows where the result will go. Press **Sign with Zirah**, compare the eight digits on the OLED with the ones the window now shows, and press the board's button within 30 seconds, K1 on the DevEBox or USER on the Discovery. **Cancel** works until you press the button. The stage rail lights each step as the device reports it, the PC checks both halves, and the result is saved next to the file. Zirah never overwrites an existing file and never changes the file you signed. Command line: `zirah sign C:\path\to\file.pdf`.
- **Verify.** Open **Verify** and drop the file. A signed copy carries its own signature, and for anything else Zirah finds the `.zirah.json` file next to it; you can also drop the file, its signature file and a key file together, and each lands in its own slot. Press **Verify Signature**. The report checks the content, the pinned context, both signatures, whether the signer is one you trust and whether the file came from hardware rather than Simulation Mode, and it lists the times described below. Verifying does not need the board. Command line: `zirah verify C:\path\to\file.pdf`.
- **Share your public keys.** Press **Export Public Keys** in the Device page, or run `zirah export-keys my-keys.json`, and send that file to the people who should verify your signatures. They drop it into the **Signer's Public Keys** slot of the Verify page, or pass `--trusted my-keys.json` on the command line. Without it, the Verify page uses the keys trusted on that PC. The file holds public keys only.
- **Checking without a board.** Anyone who should check your signatures installs Python and runs `setup.bat`, then uses the **Verify** page; it needs no board, no STM32CubeProgrammer and no flashing.

### What Zirah Can Sign

Zirah signs any regular file, whatever its type or size: a contract or a CV, a spreadsheet, a photo, a recording, a program you built, a firmware image, a source archive or a disk image. The board never reads the file. Your PC streams it through SHA-256, so even a file of many gigabytes needs no extra memory, and the board signs the digest of a manifest that holds that hash together with the file's name, size and times. The type of file only decides where the signature is kept.

| Kind of file | What you get next to it |
|---|---|
| PDF | `name.signed.pdf`, a signed copy |
| Word, Excel and PowerPoint files (`.docx`, `.xlsx`, `.pptx` and the other Office Open XML endings, with macros or as templates) | `name.signed.docx` and so on, a signed copy |
| ZIP archives (`.zip`) | `name.signed.zip`, a signed copy |
| PNG images and JPEG photos | `name.signed.png` or `name.signed.jpg`, a signed copy |
| Anything else, such as `.exe`, `.msi`, `.bin`, `.hex`, `.txt`, `.csv`, `.json`, `.py`, `.mp4`, `.wav`, `.iso`, `.odt`, `.epub`, `.apk`, `.doc` or a file with no extension | `name.ext.zirah.json`, a separate signature file |

Zirah refuses a folder or a device, a file that already carries a Zirah signature (sign the original instead) and a file that some other program changes while Zirah reads it. A file of a signed-copy type whose insides leave no safe place for the signature, such as a PDF without a final `startxref` or a ZIP archive with ZIP64 records, gets a separate signature file instead, and the Sign page says which of the two it will make before you press anything.

A signature from Zirah tells the person checking it that these exact bytes were approved on your board. It says nothing about whether a program is safe to run, and Windows, PDF readers, Office and microcontroller bootloaders do not look for Zirah signatures, so a signed `.exe` still shows as unsigned to Windows. The check happens in Zirah's Verify page or with `zirah verify`.

### Where the Signature Goes

For PDF files, Office files (DOCX, XLSX, PPTX and their relatives), ZIP archives, PNG images and JPEG photos, Zirah writes a **signed copy** next to the original, for example `CV.signed.pdf`, with the signature inside it. The copy is meant to open like the original, because the signature sits in a place readers skip: after the end of a PDF, in an extra part of an Office file that nothing in the file points to, in the comment of a ZIP archive, in a private chunk of a PNG and after the end of a JPEG. `CV.pdf` itself is never touched. Signed copies were checked during development with LibreOffice, qpdf and the usual Python libraries for PDF, Office, ZIP and image files, not yet with Microsoft Office or Adobe Acrobat, so open one in your own programs before you rely on it. Send the signed copy; it is all the other person needs, apart from your public keys.

Every other kind of file, such as plain text, CSV or source code, has no place a program would skip, so for those Zirah writes `file.txt.zirah.json` beside the file as before, and the two travel together. `zirah sign --detached` makes a separate signature file for any type, and so does choosing a `.zirah.json` name in the Sign page's **Change** dialog. Signature files made by earlier Zirah builds keep verifying.

Two things to know about signed copies. Saving one again from Word, Acrobat or any other program changes the file, so afterwards it either fails the check or carries no signature at all; that is the right answer, because it is no longer the file you signed. And Zirah refuses to sign a signed copy again; sign the original instead.

### What the Signature Covers, and the Times

The device signs a digest of a short manifest rather than of the file alone. The manifest holds the file's SHA-256, its name and size, the format it travels in, the moment you signed it and the file's creation and last-modified times as your PC reported them. So none of those can be changed afterwards without the signature failing, and the eight digits on the OLED are the start of that manifest digest, which is why the window shows them only once you press **Sign with Zirah**.

The Verify report shows the times in three groups and says which is which:

- **Signed At, Created When Signed and Modified When Signed** are part of the signature. They are what your PC's clock and file system said at that moment, so they prove what your PC reported, not an independent time. The firmware keeps no clock of its own, so the board cannot vouch for the time itself.
- **Created on This PC and Modified on This PC** come from the verifying PC's file system and are not signed. Copying, downloading or emailing a file usually resets them. When a file fails the check and its last change is dated after the signing time, the report says so, which often points to when it was edited, but anyone with the file can set those dates.
- **Verified At** is the verifying PC's clock at the moment of the check.

A failed check proves that a file differs from what was signed. It cannot show what was changed, because the original is not inside the signature.

### When Someone Else Checks Your Signature

Say you sign `CV.pdf` and send it to someone, who forwards it to a third person. That third person needs Zirah on their PC, `CV.signed.pdf` exactly as you sent it and your public keys. In the **Verify** page they drop both and press **Verify Signature**. If anyone changes even one byte of the CV on the way, the report shows **File Differs from the Signed Digest**, with the file's own digest in red under the signed one, which is the point: an edited file can never pass as one you signed. Someone who edits your file and wants to vouch for the new version has to sign it with their own key.

A key file can be swapped as easily as any other file, so publish the **PQ** fingerprint from your OLED somewhere people already trust, such as your website, your LinkedIn profile or the CV itself. The Verify report shows the fingerprints of the keys that made the signature in large type under **Signer Fingerprints**, so even someone with no key file can compare them with the ones you published. When they match, the signature came from your board. `zirah verify` prints the same fingerprints and both digests.

### Why Not a Standard PDF Signature

A PDF can also carry a signature in the PDF signature format that Acrobat shows in its signature panel. That format ties the signature to an X.509 certificate from a certificate authority, and ML-DSA support in it is still being added to the PDF standard and to PDF readers, so Acrobat could not check a post-quantum Zirah signature today. The Zirah block in a signed copy is invisible to Acrobat and checked by Zirah instead.

## 10. Run the Self-Check

The **Self-Check** in the Device page talks to every part of the board without signing a file: the link, firmware and protocol versions, the boot known-answer tests, a fresh entropy health test, the live noise statistics, the trusted keys, an identity proof in which the device signs a random challenge under a separate context that can never be mistaken for a file signature, the firmware hardening, the flash readout protection level and the ATECC608. Command line: `zirah status` and `zirah prove`.

The Firmware cell of the Device page also shows the **Build**: the board reports a hash of the firmware it runs, and Zirah compares it with the `firmware/zirah.bin` in its own folder. **Differs, Run flash.bat** means the board runs another build than the one this copy of Zirah ships with, and **Firmware Differs from This PC** appears as a warning under the lamp until you flash it.

## 11. Measure the Device

The **Benchmark** cell of the Device page times the whole masked signing path on the board: press **Run Benchmark**, and within a minute it shows the minimum, median and maximum time of each stage over 20 signatures, the number of rejection attempts, the key generation time and how many CPU cycles the assembly NTT takes against the C code. The numbers are saved as a JSON file in `%LOCALAPPDATA%\Zirah\benchmarks`. The benchmark signs with a throwaway key it makes for the purpose and never touches your device key, so it needs no button press and leaves your key and settings as they were. Command line: `zirah bench` (or `zirah bench --count 100` for more signatures and steadier numbers).

Every real signature also reports its own timings in the Sign page and in the signature file's `device_stats`.

For a write-up, `zirah validate` runs every hardware check in one go: the readiness checks, a fresh start-up health test, the statistics of each noise source, ten A-B correlation measurements when the second source is fitted, an identity proof and the benchmark. It takes about a minute and writes `validation-<date>.md` and `.json` into `%LOCALAPPDATA%\Zirah\validation`, with every number as the board reported it. It refuses to run in Simulation Mode. `--bench-count 0` skips the benchmark.

## 12. Enable Readout Protection

Do this last, after your final firmware update and your measurements, because level 1 stops you from reflashing the board until you go back to level 0, which erases the key.

The ATECC608 cannot hold the post-quantum key, so it sits in ordinary chip flash, and at readout protection level 0 anyone with the ST-LINK port can read that flash.

1. The app shows **Readout Protection Off** in the checklist while the board is at level 0, and the Device page shows the level under Firmware. From the command line, `powershell -ExecutionPolicy Bypass -File scripts\rdp.ps1 -Level Status` reports it through the ST-LINK.
2. `scripts\rdp.ps1 -Level 1` turns protection on. Unplug and replug both cables. Signing keeps working, while the debugger can no longer read the flash.
3. While level 1 is on you cannot reflash the board. `scripts\rdp.ps1 -Level 0` removes it but erases the whole chip, key included; afterwards you flash again, generate a new key and trust it again. Export your old public keys first if you still need to verify old signatures. Never use level 2, which is permanent.

On the DevEBox, which has no ST-LINK, the serial bootloader sets it instead, with the same wiring and BT0 wire as for flashing: `zirah rdp-serial --level 1`, then take the BT0 wire out and press RST. `zirah rdp-serial --level 0` removes it and, exactly as above, erases the whole chip first; it asks you to type ERASE before doing anything.

## Simulation Mode

Switch on **Simulation Mode** in the app (or pass `--sim` to `zirah`) to try the workflow with no board. A ribbon across the window says so, the signal window shows simulated samples with a watermark, a simulated button appears where the real one would be needed, and signed copies get `.signed-sim` in their name and signature files the suffix `.zirah-sim.json`, all with `"simulated": true`. A simulated signature always verifies as **Simulated Signature**, and its throwaway key is never added to your trusted keys.

## Optional: A Second Noise Source

Two inputs need no parts at all, and are mixed into every random block, credited with no entropy. **LSI Clock Jitter** times the STM32's internal RC oscillator against the crystal-derived clock, and **Board Accelerometer** reads the low bits of the board's own accelerometer, which move with sensor noise even at rest. The Device page shows each as Mixed In when its check passed at power-up. `.\zirah jitter` and `.\zirah accel` show what each one measures.

The second avalanche source below is the stronger addition, because the board also checks that the two avalanche sources do not move together.

`hardware/WIRING.md`, "Optional: A Second Noise Source", adds a second avalanche junction on the unused half of the LM358, mostly from spare parts. The firmware finds it at power-up on its own. With it, every start-up test also checks that the two sources do not move together, which catches a signal reaching both of them, and every random block mixes in both. The Device page shows **Second Noise Source: Live on PB1** and the A-B correlation, and the signal window a **Source B Live** badge.

## Optional Extras

- `zirah capture --samples 1000000 --out data/entropy/run1` saves raw noise samples (add `--source b` for the second source), and `.venv\Scripts\python analysis\entropy_report.py data\entropy\run1` draws plots of them. The formal NIST SP 800-90B assessment of those files is described in `data/entropy/README.md`.
- `zirah term` opens a direct terminal to the board. Type `HELP` there for every command, or see `docs/PROTOCOL.md`.
- **Diagnostics** in the app shows a report you can export when asking for help. It contains no keys, signatures, file contents or file hashes, and the device ID is shortened unless you choose to include it.

## If Something Goes Wrong

| Problem | Fix |
|---|---|
| `flash.bat` says no ST-LINK found | Try another mini-USB cable or port, and check that both CN3 jumpers on the board are fitted. |
| The lamp keeps searching | The micro-USB cable is charge-only or in the wrong socket; it must be CN5. `zirah ports` lists what Windows sees. |
| The Port Is in Use | Close other programs that hold the port (a serial terminal, the Arduino IDE, a second Zirah). |
| Entropy Source Not Healthy, No Live Noise (`FAULT_SOURCE`) | The noise pin (PA1, or PA4 on the DevEBox) reads like a pin with nothing on it: off the middle of the range, or too smooth. Check the wire from 16a, R8 at 20e to 16e, and the LM358's power. `zirah analyze` shows MEAN near 2000 and AUTOCORR1 near 0 when it is right. |
| Entropy Source Not Healthy, Bias Fault | The boost is not between 9 and 20 V, or the PB0 wire is in the wrong hole. Run `zirah vbias`. |
| Stuck Signal | No noise reaches PA1. Check the transistor direction, the LM358 notch and the wires 15a to 26a and 16a to PA1. |
| Source B Fault | The second noise source was found at power-up and has since failed or come loose. Reseat its wires and power-cycle, or pull the PB1 wire out and power-cycle to run on one source. |
| A-B Correlated | The two noise sources moved together at the last start-up test, so a signal is reaching both. Check that C1 (100 uF) is in, that the boost is near 12 V, and that the two sources' wires do not run side by side for long. `zirah analyze` shows the value as `CORR_AB`. |
| No Signature Found | The file carries no Zirah signature: it is not a signed copy, or it was saved again in another program after signing. If it came with a `.zirah.json` file, drop that too. |
| Already Signed | The file is a signed copy. Verify it, or sign the original file instead. |
| LSI Clock Jitter: No Jitter Found | The LSI periods were fixed, out of range, or did not arrive. Run `.\zirah jitter` and look at `lsi_hz` (17,000 to 47,000 is normal) and `distinct`. |
| Board Accelerometer: Not Answering or No Noise | `.\zirah accel` shows `who_am_i`: `0x3f` or `0x3b` means the chip answered and its readings did not move; anything else means it did not answer on SPI1. Check that nothing is plugged into PA5, PA6, PA7 or PE3. |
| Firmware Differs from This PC | The board runs another build than this folder's `zirah.bin` (or `zirah-devebox.bin` on the DevEBox). Run `flash.bat`, or `flash-devebox.bat` on the DevEBox. |
| `flash-devebox.bat` says no answer from the bootloader | The BT0 wire is not on 3V3, RST was not pressed after fitting it, or TX and RX are swapped: the adapter's TXD goes to PA10 and its RXD to PA9. |
| OLED blank | Press RESET after wiring. Check GND, VCC, SCL and SDA by label. `zirah i2cscan` should list 0x3C (screen) and 0x60 (ATECC608). |
| No Button Press in Time | Nothing was signed. Start again and press the button (K1 or USER) within 30 seconds. |
| Signing Is Locked | A signature failed the device's own check and was withheld. Power-cycle the board and run the Self-Check. |
| Zirah Is Already Running | Only one copy runs at a time; use the open window. |
| Something Went Wrong in the Window | Zirah keeps running and writes the details to `last-error.txt` in `%LOCALAPPDATA%\Zirah`. Send that file with the Diagnostics report when asking for help. |
