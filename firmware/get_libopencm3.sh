#!/usr/bin/env bash
# Downloads libopencm3 at the exact commit Zirah was built with, then builds
# it for the STM32F4 family. Only needed if you want to rebuild zirah.bin
# (Linux or WSL2). Safe to run more than once.
set -euo pipefail
cd "$(dirname "$0")"
COMMIT=ddcbb2889fa018626c604e27ea50e66db5755b78
if [ ! -d libopencm3/.git ]; then
  rm -rf libopencm3
  git clone https://github.com/libopencm3/libopencm3.git libopencm3
fi
git -C libopencm3 fetch -q origin "$COMMIT" 2>/dev/null || true
git -C libopencm3 checkout -q "$COMMIT"
make -C libopencm3 -j"$(nproc)" TARGETS=stm32/f4 > /dev/null
echo "libopencm3 $COMMIT ready (stm32/f4)"
