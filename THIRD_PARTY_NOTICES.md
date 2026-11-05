# Third-Party Notices

Zirah's own source code and documentation are released under the MIT licence in `LICENSE`. This file lists everything in or used by this repository that comes from someone else, and under which terms.

## Included in This Repository

### CRYSTALS-Dilithium Reference Implementation

- Files: `firmware/mldsa/`, the `ref/` code unchanged apart from the build settings in `config.h`.
- Source: <https://github.com/pq-crystals/dilithium>, `ref/` directory.
- Terms: as stated by the authors in `firmware/mldsa/LICENSE.upstream`: public domain (CC0), or alternatively Apache-2.0 or GPL-2.0. The Keccak code is public domain, by the authors credited at the top of the respective files.

### pqm4 Cortex-M4 NTT

- Files: `firmware/mldsa-m4/ntt.S` and `firmware/mldsa-m4/macros.i`, unchanged.
- Source: <https://github.com/mupq/pqm4>, `crypto_sign/ml-dsa-44/m4f/` (shared by `ml-dsa-65/m4f/`). The code is by D. O. C. Greconici, M. J. Kannwischer and D. Sprenkels, "Compact Dilithium Implementations on Cortex-M3 and Cortex-M4", IACR TCHES 2021 (1), based on earlier code by Markus Krausz.
- Terms: CC0 1.0 (public domain dedication), as stated at the top of `ntt.S` ("Now licensed under CC0 with permission of the authors") and in the paper's artifact record at <https://artifacts.iacr.org/tches/2021/a1/>.

### Adafruit GFX Library Font

- File: `firmware/src/font5x7.h` (characters 32 to 126 of `glcdfont.c`, reformatted).
- Source: <https://github.com/adafruit/Adafruit-GFX-Library>
- Terms:

```
Software License Agreement (BSD License)

Copyright (c) 2012 Adafruit Industries.  All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

- Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.
- Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```

### Fonts

- Files: `ui/fonts/ChakraPetch-Medium.ttf`, `ChakraPetch-SemiBold.ttf`, `IBMPlexSans-Variable.ttf`, `IBMPlexMono-Regular.ttf`, `IBMPlexMono-Medium.ttf`, unmodified.
- Source: the Google Fonts repository, <https://github.com/google/fonts> (`ofl/chakrapetch`, `ofl/ibmplexsans`, `ofl/ibmplexmono`).
- Terms: SIL Open Font License 1.1. Chakra Petch is copyright 2018 The Chakra Petch Project Authors. IBM Plex is copyright 2017 IBM Corp. with Reserved Font Name "Plex". The full licence texts are next to the fonts (`ui/fonts/OFL-*.txt`).

### ATECC608 Configuration Values

The slot and key configuration written by `ATECC PROVISION` (`firmware/src/atecc.c`) follows the layout used by SparkFun's ATECCX08a Arduino library (<https://github.com/sparkfun/SparkFun_ATECCX08a_Arduino_Library>). Only the configuration values were used; no SparkFun code is included. Command encodings follow Microchip's CryptoAuthLib (<https://github.com/MicrochipTech/cryptoauthlib>); no CryptoAuthLib code is included.

## Linked into the Firmware (Source Not Included)

### `libopencm3`

- Used for: chip start-up, clocks, GPIO, ADC, USART and USB device support. It is linked into `firmware/zirah.bin`.
- Source: <https://github.com/libopencm3/libopencm3>, commit `ddcbb2889fa018626c604e27ea50e66db5755b78` (fetched by `firmware/get_libopencm3.sh` when rebuilding).
- Terms: GNU Lesser General Public License v3.0 or later. Because the complete source of the rest of the firmware is in this folder and builds against that libopencm3 commit, anyone can rebuild the firmware with a modified libopencm3, which is what the LGPL asks for when a library is linked statically.

## Used During Development (Not Included)

### ELMO

- Used for: the emulated leakage check whose results are in `docs/results/` (four kernel variants, including the shuffled one the firmware uses).
- Source: <https://github.com/sca-research/ELMO>.
- Terms: University of Bristol Open Access Software Licence (BSD-style, copyright 2016 The University of Bristol). It asks that use for scientific publications or commercial purposes be reported to the University of Bristol at OSI-notifications@bristol.ac.uk, quoting reference 2668.

### QEMU

- Used for: the `qemu` test suite, which runs the firmware's Cortex-M4 code on QEMU's `mps2-an386` machine (`tests/qemu/`). It is installed separately (for example `apt install qemu-system-arm`) and is not part of Zirah.
- Source: <https://www.qemu.org/>.
- Terms: GNU General Public License v2.0. The test programs only run inside it.

### NIST SP 800-90B Entropy Assessment

- Used for: the optional formal entropy assessment described in `data/entropy/README.md`.
- Source: <https://github.com/usnistgov/SP800-90B_EntropyAssessment>.
- Terms: software developed by NIST employees is not subject to copyright protection in the United States; see the tool's own licence file.

### Python Packages

Installed by `setup.bat` into the private `.venv` folder, each under its own licence: PySide6 (Qt for Python, LGPL-3.0, used as an unmodified, dynamically loaded library), pyserial (BSD), dilithium-py (MIT), cryptography (Apache-2.0 or BSD), numpy (BSD), matplotlib (matplotlib licence, BSD-style).

### `potracer`

- Used for: tracing the official logo sheet into the vector files in `brand/` (`brand/vectorize_logo.py`). It runs only when the brand assets are regenerated and is not part of Zirah or its installed packages.
- Source: <https://pypi.org/project/potracer/>, a Python port of Peter Selinger's Potrace.
- Terms: GPL-2.0. The traced SVG files are the output of the tool, not a copy of it.
