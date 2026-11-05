# Wiring Zirah

Everything is wired in one sitting, with one exception: the 100 uF capacitor C1 goes in last, after the boost module has been set to 12 V, because a new MT3608 can come out of the box set to almost 30 V and C1 is only rated for 25 V.

The pictures `breadboard_A.svg`, `breadboard_B.svg` and `schematic.svg` show the same thing as this page for the Discovery, and `WIRING_TABLE.md` lists every hole. `breadboard_A_dual.svg` shows Board A with the optional second noise source at the end of this page. For the DevEBox, `wiring_devebox.svg` shows every connection between the boards, `schematic_A_devebox.svg` and `breadboard_A_devebox.svg` show Board A as it was built and measured, and `schematic_B_devebox.svg` and `breadboard_B_devebox.svg` show Board B. Unplug every USB cable while you wire.

## On the DevEBox STM32F4XX_M Board

Zirah also runs on the DevEBox "STM32F4XX_M" core board, which carries an STM32F407VE, the same chip as the Discovery with half the flash. Everything on this page applies to it with five substitutions, because the board has its own LED on PA1 and no ST-LINK:

| On the Discovery | On the DevEBox |
|---|---|
| Discovery **PA1** (noise signal from 16a) | **PA4** |
| Discovery **5V** and **3V** pins | the pins printed **5V** and **3V3** on the long headers |
| USB-TTL adapter on **PA2** and **PA3** | adapter **TXD to PA10**, **RXD to PA9**, GND to a GND pin |
| the blue **USER** button | **K1** |
| power and flashing over the mini-USB (ST-LINK) | power over the **USB-C** socket, flashing through the adapter with `flash-devebox.bat` |

The DevEBox build that was measured for the technical report differs from this page in one more way. The MT3608 modules used there could not be set below about 26 V, so two 10 kohm resistors (R16 and R17) halve the boost output before the 1 kohm and 100 uF filter, and R9 and R10 are 100 kohm and 10 kohm, which keeps the same 11 to 1 ratio for the monitor. `board_a_build.html` is the step-by-step guide for that build on a full-size breadboard, generated from the same layout data as `breadboard_A_devebox.svg` and `WIRING_TABLE.md`.

PA8, PC9, PB0 and PB1 stay as they are. On the measured build the OLED was plugged into Board B's half-size breadboard and the ATECC608 module sat beside it on jumper wires, with the same four connections as below. Its 3V3 is 3.3 V where the Discovery's 3V is about 3.0 V, and the 1.5 V reference then sits near 1.65 V, which is still the middle of the ADC range because the ADC runs from the same supply. The board's own 5V pins are joined straight to the USB-C socket, so never feed 5 V into them from anywhere else, and leave the adapter's 5V and 3V3 pins unconnected. Keep the microSD slot empty, because one of its lines shares PC9 with the I2C bus.

## Reading the Breadboard

Hold each breadboard upright with row 1 at the top. Rows 1 to 30 run down the board. Left of the centre groove are holes a, b, c, d, e and right of it f, g, h, i, j. In one row, a to e are joined together and f to j are joined together, but the two halves are not joined across the groove. The long strip at the far left is the L+ rail (red line) next to the L- rail (blue line), and the far right has R+ and R-. Each rail is joined along its whole length, so "to the L- rail" means any hole on that strip.

## Board B, the Screen and the Secure Element

1. Discovery **3V** to the **L+** rail. Discovery **GND** to the **L-** rail.
2. Discovery **PA8** to hole **10a** (row 10 left becomes the SCL line).
3. Discovery **PC9** to hole **14a** (row 14 left becomes the SDA line).
4. A **4.7 kohm** resistor (yellow, violet, red) from **10b** to the L+ rail, and another from **14b** to the L+ rail.
5. Push the OLED's 4 pins into **3j, 4j, 5j, 6j**, screen hanging off the right edge. Read the label printed beside each pin, then from hole g of the same row run a wire: the GND row to the L- rail, the VCC (or VDD) row to the L+ rail, the SCL (or SCK) row to **10c**, the SDA row to **14c**.
6. Push the ATECC608's 4 pins into **20j, 21j, 22j, 23j** and wire them the same way by label: GND to L-, VCC to L+, SCL to **10d**, SDA to **14d**.

Modules differ in pin order, so always go by the printed label. Swapping VCC and GND can kill a module.

## Board A, the Noise Source

Power and rails:

1. Discovery **5V** to the **L+** rail. Discovery **GND** to the **L-** rail.
2. At row 30, a wire from **L+ to R+** and a wire from **L- to R-**.

Boost module (MT3608, sits beside the board, four wires):

3. L+ rail to **VIN+**, L- rail to **VIN-**.
4. **VOUT+** to hole **2a**, **VOUT-** to the L- rail.
5. A wire from **2e to 2f** so the 12 V reaches the right half of row 2.

Voltage monitor:

6. **1 Mohm** (brown, black, green) from **2h to 7h**.
7. **100 kohm** (brown, black, yellow) from **7i** to the **R-** rail.
8. **104** ceramic capacitor from **7j** to the R- rail.
9. A wire from **7g** to Discovery **PB0**.

No 1 Mohm resistor at hand? Fit a 100 kohm in step 6 and a 10 kohm in step 7 instead. The ratio is the same 11 to 1, so the firmware still reads the right voltage.

Noise source:

10. **1 kohm** (brown, black, red) from **2c to 6c**.
11. **104** ceramic capacitor from **6b** to the L- rail. (C1 goes in **6a** later.)
12. **100 kohm** from **6d to 11d**.
13. **2N3904 transistor**: hold it with the flat printed face toward you and the legs pointing down. The legs are E, B, C from left to right. Put **E in 11a, B in 12a, C in 13a**. Once it is in, the flat face points toward the left rails.
14. A wire from **12b** to the L- rail (grounds the base). Nothing else goes in row 13.
15. **104** ceramic capacitor from **11c to 15c**.

1.5 V reference:

16. **100 kohm** from **15d to 19d**.
17. A wire from Discovery **3V** to hole **23a**. If no 3V pin is free, take it from Board B's L+ rail instead, which is the same 3V.
18. **100 kohm** from **23b to 19b**, **100 kohm** from **19c** to the L- rail.
19. **10 uF** capacitor: long leg (+) in **19a**, short leg in the L- rail.

Amplifier (LM358):

20. The chip straddles the groove with its notch (half-moon cut) pointing up toward row 1. Pin 1 goes in **24e**, so its left legs sit in 24e, 25e, 26e, 27e and its right legs in 24f, 25f, 26f, 27f.
21. A wire from **15a to 26a** (noise into the amplifier).
22. A wire from **27a** to the L- rail (chip ground) and from **24j** to the **R+** rail (chip 5 V).
23. **104** ceramic capacitor from **24h** to the R- rail, and a **10 uF** capacitor with + in **24i** and - in the R- rail.
24. Unused half of the chip: a wire from **25g to 26g** and a wire from **27j** to the R- rail.
25. A wire from **24a to 20a**.
26. **100 kohm** from **20c to 25c**, **10 kohm** (brown, black, orange) from **25b to 29b**, and a **10 uF** capacitor with + in **29a** and - in the L- rail. This sets the gain to 11.

Output to the Discovery:

27. **1 kohm** from **20e to 16e**.
28. **101** ceramic capacitor (100 pF) from **16b** to the L- rail.
29. A wire from **16a** to Discovery **PA1**.

## Power Up, Set 12 V, Add C1

1. Plug in both USB cables. Run `zirah vbias --watch`.
2. Turn the brass screw on the MT3608 slowly until the reading is about **12.0 V**. It is a multi-turn screw and may need ten or more turns before anything changes. If the number moves away from 12, turn the other way. It never goes below about 4.8 V.
3. Press Enter, unplug both cables, push the **100 uF** capacitor in: long leg (+) in **6a**, short leg in the L- rail.
4. Plug back in and run `zirah health`. It should say `TRNG OK`.

## Checking the Noise Without an Oscilloscope

`zirah analyze` measures the noise. A good result has STD between roughly 80 and 600 and almost nothing CLIPPED.

| What You See | What to Change (Unplug First) |
|---|---|
| STD below about 20 | raise the gain (10 kohm at 25b to 29b swapped for 4.7 kohm gives 22x), or swap the 100 kohm at 6d to 11d for 470 kohm, or try another 2N3904 |
| many CLIPPED or STD above 700 | lower the gain (swap the 100 kohm at 20c to 25c for 10 kohm, gain 2) |
| STD near 2 to 5 | no noise reaches the ADC: check the transistor direction, the 12 V, the 104 at 11c to 15c and the wire 15a to 26a |
| LM358 gets warm | it is in backwards: unplug at once and turn it round |

## Optional: A Second Noise Source

Zirah works with one noise source. A second one, built from parts left over from the first, makes the entropy source harder to fool: the firmware then samples both at the same instants, runs the health tests on each, mixes both into every block, and at every start-up test measures how much the two move together. Two separate transistors produce unrelated noise, so a strong correlation means a signal is reaching both, such as ripple from the boost converter or something injected on purpose, which the health tests on one source cannot see. Above a correlation of 0.30 the device refuses to sign (`FAULT_CORR`). Entropy is still credited to the first source only, so the second can add protection but never replaces the first.

It uses the second half of the LM358, which is already on the board but parked by two wires, and the free right half of Board A. `breadboard_A_dual.svg` and the second table in `WIRING_TABLE.md` show every hole.

Parts: one more **2N3904** (you bought spares), three **100 kohm**, one **10 kohm**, one **1 kohm**, one **104**, one **101**, one **10 uF** and six jumper wires.

With both USB cables unplugged:

1. Pull out the two wires that park the second half of the LM358: **25g to 26g**, and **27j** to the R- rail.
2. A wire from **6e to 6f**, which brings the filtered 12 V to the right half of row 6.
3. **100 kohm** from **6g to 10g**.
4. The second **2N3904**, held the same way as the first one (flat face toward you, legs down, E, B, C from left to right), turned so the legs go down column j: **E in 10j, B in 11j, C in 12j**. The flat face then points toward the centre groove.
5. A wire from **11i** to the **R-** rail (grounds the base). Nothing else goes in row 12 on the right.
6. **104** ceramic capacitor from **10h to 14h**.
7. A wire from **19e to 19f**, which brings the 1.5 V reference to the right half of row 19.
8. **100 kohm** from **14g to 19g**.
9. A wire from **14j to 27g** (noise into pin 5 of the LM358).
10. A wire from **25j to 22j** (pin 7, the output, to row 22).
11. **100 kohm** from **22h to 26h**, **10 kohm** from **26i to 29i**, and a **10 uF** capacitor with + in **29j** and - in the R- rail. This sets the gain to 11, as for the first source.
12. **1 kohm** from **22g to 17g**, a **101** ceramic capacitor from **17h** to the R- rail, and a wire from **17j** to Discovery **PB1**, the pin next to PB0 on the same header.

No spare 101? Leave it out; never put a 104 there, which would filter away most of the noise. No spare 10 uF? Put the lower end of the 10 kohm in **19h** instead of 29i and skip the capacitor; the noise gain stays 11.

Plug back in. The firmware looks for the second source at power-up, and counts it only when it finds live noise on PB1, so restart the board after wiring it. The app's Device page then shows **Second Noise Source: Live on PB1**, the signal window shows a **Source B Live** badge with the correlation, and `zirah info` prints `SOURCE_B present`. If the Device page still says **Not Fitted**, no noise reaches PB1: check the second transistor's direction, the wires 14j to 27g and 17j to PB1, and that the two parking wires are really out. Check a working second source with:

- `zirah analyze --source b`: the same STD window as for the first source (roughly 80 to 600, almost nothing clipped). If not, the table above applies, with the parts of the second source (the 100 kohm at 22h to 26h for the gain, the wire 14j to 27g for the signal).
- `zirah analyze`: the last line, `CORR_AB`, should be near zero, typically between -0.10 and 0.10.

If the board reports **Source B Fault** after a while, a wire of the second source has come loose; it was found at power-up, so the firmware fails closed instead of quietly dropping it. Reseat the wires and power-cycle, or pull the PB1 wire out and power-cycle to go back to one source.
