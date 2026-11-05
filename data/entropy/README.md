# Noise Captures (Optional)

Raw samples from Zirah's noise source can be saved here for analysis. Nothing in the normal use of Zirah needs this.

```
zirah capture --samples 1000000 --out data/entropy/run1 --notes "12 V boost, room temperature"
py analysis\entropy_report.py data\entropy\run1
```

A long capture is fetched in pieces of 50,000 samples, each checked with a CRC-32, so a lost byte stops the capture with a clear error instead of a hang. The capture takes a few minutes for 1,000,000 samples and writes four files:

| File | Content |
|---|---|
| `run1_raw16.bin` | the raw 12-bit ADC values, 2 bytes each |
| `run1_sym8.bin` | the low 8 bits of each sample, the symbols the firmware itself uses |
| `run1_bit0.bin` | the lowest bit of each sample |
| `run1_meta.json` | sample rate, which source, boost voltage, die temperature before and after, firmware version and build hash, and your notes |

With the optional second noise source fitted (`hardware/WIRING.md`), `--source b` captures it instead of the first, for example `zirah capture --source b --out data/entropy/run1b`. Each source needs its own assessment; the firmware credits entropy to source A only.

`entropy_report.py` runs on Windows and saves a time-series plot, a histogram, an autocorrelation plot and basic statistics into `analysis/figures/`. A useful comparison is a second capture with the MT3608 output wire pulled out, which shows how much of the signal really comes from the transistor.

## Formal NIST SP 800-90B Assessment

The official assessment tool from NIST (<https://github.com/usnistgov/SP800-90B_EntropyAssessment>) only builds on Linux, so on Windows it needs WSL2 (`wsl --install -d Ubuntu-24.04` in an administrator PowerShell). Inside Ubuntu:

```bash
cd ~
sudo apt update
sudo apt install -y build-essential git libbz2-dev libdivsufsort-dev libjsoncpp-dev libssl-dev libmpfr-dev libgmp-dev
git clone https://github.com/usnistgov/SP800-90B_EntropyAssessment.git
make -C SP800-90B_EntropyAssessment/cpp non_iid
cd /mnt/c/Users/<you>/Documents/zirah/data/entropy
~/SP800-90B_EntropyAssessment/cpp/ea_non_iid -v run1_sym8.bin 8
~/SP800-90B_EntropyAssessment/cpp/ea_non_iid -v run1_bit0.bin 1
```

The line `min(H_original, 8 X H_bitstring)` in the first result is the assessed min-entropy per 8-bit symbol. These estimators are deliberately pessimistic: even ideal simulated noise scores well below the maximum of 8 bits, so a modest number is normal.

## Results From the DevEBox Board

The 1,000,000 samples measured for the technical report (5 November 2026, 20 pieces of 50,000) gave 6.898 bits per 8-bit symbol on the non-IID track (H_original 7.059, H_bitstring 0.862) and 0.784 bits for bit 0. The IID permutation tests failed 3 of 19, so the non-IID result is the one that applies. The tool outputs are in `data/entropy/results/`, the board's validation report is in `docs/results/`, and the raw captures are in the Zenodo archive of this release, https://doi.org/10.5281/zenodo.23982873.
