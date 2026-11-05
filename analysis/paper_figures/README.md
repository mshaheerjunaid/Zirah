# Figures of the Technical Report

These scripts redraw the drawn figures of the Zirah technical report at their printed width of 11.9 cm, as a 1000 dpi PNG next to PDF and SVG versions. They need matplotlib, numpy and scipy, and the Liberation Sans font.

| Script | Figure |
|---|---|
| `architecture.py [folder]` | Figure 1, the parts of Zirah and what crosses the serial link |
| `../../hardware/layout/devebox/wiring_devebox.py folder` | Figure 3, every connection of the DevEBox build |
| `../../hardware/layout/devebox/schematic_board_b.py file.svg` | Figure 4, Board B (needs schemdraw) |
| `../../hardware/layout/devebox/schematic_board_a.py file.svg` | Figure 6, Board A as built (needs schemdraw) |
| `export_breadboard_svg.py` | Figure 7, Board A on the breadboard, from `hardware/layout` |
| `entropy_figures.py path/to/sourceA_1M_raw16.bin [folder]` | Figures 9, 10 and 11, from the capture in the Zenodo dataset |

Figures 2, 5, 8, 12 and 13 are photographs, screenshots and the ELMO leakage plot, kept at the resolution they were taken or made at.
