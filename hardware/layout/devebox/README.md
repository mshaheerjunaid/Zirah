# DevEBox Drawings

These scripts draw the DevEBox build that the technical report measured:

| Script | Writes |
|---|---|
| `wiring_devebox.py` | `hardware/wiring_devebox.svg`, every connection between the PC, the CP2102 adapter, the DevEBox, Board A, Board B and the MT3608 |
| `schematic_board_a.py` | `hardware/schematic_A_devebox.svg`, Board A as built, with the R16 and R17 divider |
| `schematic_board_b.py` | `hardware/schematic_B_devebox.svg`, Board B, the I2C bus with the OLED and the ATECC608 |

Run them from this folder with `python wiring_devebox.py` and so on. They need matplotlib and schemdraw (`pip install matplotlib schemdraw`) and the Liberation Sans font. Given a folder as the first argument, `wiring_devebox.py` writes the 1000 dpi PNG, PDF and SVG used in the report instead. The breadboard drawings come from `../zirah_layout.py`, which also checks every layout against the circuit netlist.
