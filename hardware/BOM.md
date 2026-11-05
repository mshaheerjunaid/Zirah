# Bill of Materials

Everything below is breadboard-only, with no soldering, apart from header pins on modules that ship without them (see the note at the end). Prices are what the parts cost from Digilog.pk in late 2026 and will drift.

## Parts Used in the Build

| Qty Used | Part | Placement | Approx. Cost (PKR) |
|---|---|---|---|
| 1 | STM32F4DISCOVERY (STM32F407VG), already owned | the main board | owned |
| 1 | ATECC608 breakout with pin header | Board B | owned |
| 1 | 1.3 inch I2C OLED, SH1106, 128 x 64 | Board B | 710 |
| 1 | MT3608 boost module (2 recommended, one spare) | beside Board A | 120 each |
| 1 | 2N3904 NPN transistor (10 bought, spares are useful) | Board A, Q1 | 10 each |
| 1 | LM358 dual op-amp, DIP-8 (6 bought) | Board A, U1 | 30 each |
| 2 | 400-point half-size breadboard | Boards A and B | 170 each |
| 2 | 1 kohm resistor | R1, R8 | small |
| 2 | 4.7 kohm resistor | RP1, RP2 (I2C pull-ups) | small |
| 1 | 10 kohm resistor | R6 | small |
| 6 | 100 kohm resistor | R2, R3, R4, R5, R7, R10 | small |
| 1 | 1 Mohm resistor | R9 | small |
| 4 | 100 nF ceramic capacitor, marked 104 | C2, C3, C7, C8 | 5 each |
| 1 | 100 pF ceramic capacitor, marked 101 | C6 | 10 |
| 1 | 100 uF 25 V electrolytic capacitor | C1 | 10 |
| 3 | 10 uF 25 V electrolytic capacitor | C4, C5, C9 | 20 each |
| about 35 | Dupont jumper wires (male-male, male-female) | everywhere | 500 |
| 1 | Mini-USB cable | Discovery ST-LINK port (CN1): power and flashing | 200 |
| 1 | Micro-USB data cable (not a charge-only cable) | Discovery USB OTG port (CN5): the serial link to your PC | usually already owned |

Instead of the Discovery, the DevEBox STM32F4XX_M core board (STM32F407VE) works with a USB-C cable for power and a USB-TTL adapter (CP2102, CH340 or FTDI) for flashing and the serial link; it needs no mini-USB or micro-USB cable. The pins that differ are listed at the top of `hardware/WIRING.md`.

The DevEBox board measured for the technical report also uses two more 10 kohm resistors, R16 and R17, which halve the MT3608's output because the modules used would not go below about 26 V, and it uses 100 kohm for R9 with 10 kohm for R10. `hardware/board_a_build.html` places every part of that build.

R9 and R10 only set a ratio. A 100 kohm for R9 with a 10 kohm for R10 gives the same 11 to 1 and works the same, which helps when no 1 Mohm resistor is at hand.

## Parts for the Optional Second Noise Source

`WIRING.md`, "Optional: A Second Noise Source", adds a second avalanche junction on the unused half of the LM358. Most of it comes from the spares above.

| Qty | Part | Used As | Notes |
|---|---|---|---|
| 1 | 2N3904 | Q2 | from the spares |
| 3 | 100 kohm resistor | R11, R12, R13 | |
| 1 | 10 kohm resistor | R15 | |
| 1 | 1 kohm resistor | R14 | |
| 1 | 100 nF ceramic capacitor (104) | C10 | |
| 1 | 100 pF ceramic capacitor (101) | C11 | may be left out |
| 1 | 10 uF electrolytic capacitor | C12 | may be replaced by a wire to the 1.5 V reference, see `WIRING.md` |
| 6 | jumper wires | W20 to W25 | the two wires W18 and W19 come out |

## Parts Bought but Not Needed for Version 1

| Part | Reason Not Used | Possible Later Use |
|---|---|---|
| LM393 comparators | the ADC samples the amplified noise directly, which gives the NIST tool richer 8-bit symbols than a 1-bit comparator would | a second, comparator-based noise channel to compare against |
| 10 nF capacitors (103) | not required by the final circuit | extra supply decoupling if the noise floor shows switching spikes |
| 24 MHz 8-channel logic analyzer | not required to build | debugging the I2C bus and capturing an ATECC608 transaction trace for the report (PulseView, free, runs on Windows) |
| Extra 2N3904, LM358 | spares | swap Q1 if one transistor gives weak noise; swap U1 if one is damaged; one 2N3904 becomes Q2 of the optional second noise source |

## Optional but Strongly Recommended

| Part | Why |
|---|---|
| Cheap digital multimeter (DT-830 class, about Rs 700) | lets you confirm the boost voltage and check for shorts independently of the firmware's own voltage monitor |
| USB-TTL adapter (CP2102 or CH340) | a backup serial link on PA2/PA3 if the micro-USB serial port gives trouble on your PC |

## Header Pins and the No-Soldering Rule

Check three items the moment you unpack them, because anything without pins needs a one-time visit to a market repair shop (Rs 100 to 300 for all of them together):

1. The ATECC608 breakout: it needs a soldered 4-pin header (VCC, GND, SDA, SCL).
2. The MT3608 module: it needs four pins or wires soldered to VIN+, VIN-, VOUT+ and VOUT-.
3. The OLED: it usually ships with its 4-pin header already soldered; confirm it.

Ask the shop for straight male header pins on all of them.
