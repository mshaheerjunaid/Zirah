# Zirah firmware for the STM32F4DISCOVERY (STM32F407VG) and the DevEBox
# STM32F4XX_M core board (STM32F407VE)
#
# You do NOT need to build anything to use Zirah: zirah.bin in this folder is
# the ready-made firmware, flashed with flash.bat in the top folder.
#
# Rebuilding is only needed if you change the source code. It needs Linux or
# WSL2 Ubuntu with arm-none-eabi-gcc, make and python3:
#   bash get_libopencm3.sh     (once, downloads and builds libopencm3)
#   make                       (builds build/zirah.bin)
#   make release               (copies it over zirah.bin in this folder)
#   make BOARD=devebox release (the DevEBox build, zirah-devebox.bin)

PROJECT      := zirah
OPENCM3_DIR  ?= libopencm3
# BOARD picks the board (see src/board_pins.h):
#   discovery = STM32F4DISCOVERY, STM32F407VG, 1 MB flash (default, zirah.bin)
#   devebox   = DevEBox STM32F4XX_M, STM32F407VE, 512 KB flash (zirah-devebox.bin)
BOARD ?= discovery
ifeq ($(BOARD),discovery)
DEVICE       := stm32f407vgt6
BUILD        := build
OUT          := $(PROJECT).bin
BOARD_DEF    :=
else ifeq ($(BOARD),devebox)
DEVICE       := stm32f407vet6
BUILD        := build-devebox
OUT          := $(PROJECT)-devebox.bin
BOARD_DEF    := -DZIRAH_BOARD_DEVEBOX
else
$(error BOARD must be discovery or devebox)
endif

include $(OPENCM3_DIR)/mk/genlink-config.mk

PREFIX  ?= arm-none-eabi-
CC      := $(PREFIX)gcc
OBJCOPY := $(PREFIX)objcopy
SIZE    := $(PREFIX)size

SRC_APP := $(wildcard src/*.c)
# NTT picks the number-theoretic transform:
#   asm = pqm4's Cortex-M4 assembly (mldsa-m4/, the default and what the
#         shipped zirah.bin uses)
#   c   = the reference C code (mldsa/ntt.c)
# Both give byte-identical keys and signatures (tests/qemu checks this).
NTT ?= asm
ifeq ($(NTT),asm)
SRC_NTT := mldsa-m4/ntt_m4.c
ASM_NTT := mldsa-m4/ntt.S
NTT_DEF := -DZIRAH_NTT_ASM
else ifeq ($(NTT),c)
SRC_NTT := mldsa/ntt.c
ASM_NTT :=
NTT_DEF :=
else
$(error NTT must be asm or c)
endif
SRC_PQC := mldsa/fips202.c $(SRC_NTT) mldsa/packing.c mldsa/poly.c \
           mldsa/polyvec.c mldsa/reduce.c mldsa/rounding.c mldsa/sign.c \
           mldsa/symmetric-shake.c
SRCS    := $(SRC_APP) $(SRC_PQC)
OBJS    := $(patsubst %.c,$(BUILD)/%.o,$(SRCS)) $(patsubst %.S,$(BUILD)/%.o,$(ASM_NTT))

# MLDSA_MODE picks the ML-DSA parameter set:
#   2 = ML-DSA-44 (smaller, faster)
#   3 = ML-DSA-65 (what the shipped zirah.bin uses, NIST security level 3)
# ML-DSA-87 (mode 5) does not fit in the STM32F407's RAM with this design.
MLDSA_MODE ?= 3
# ZIRAH_EXTRA lets you pass -D overrides, e.g.
#   make ZIRAH_EXTRA="-DZIRAH_H_ASSUMED_X100=341"
# Hardening flags (see docs/DESIGN.md, "Firmware Hardening"):
#   -fstack-protector-strong  canary in every frame with an array or address
#                             taken; the guard value is randomised at boot
#                             and a mismatch wipes secrets and halts
#   -Wformat=2                catches format-string mistakes at build time
#   -fstack-usage             writes per-function stack use (make stack)
CFLAGS  := $(ARCH_FLAGS) -O2 -g -std=gnu11 \
           -Wall -Wextra -Wno-unused-parameter -Wshadow -Wformat=2 \
           -Wvla -Wpointer-arith \
           -fstack-protector-strong -fstack-usage \
           -ffunction-sections -fdata-sections -fno-common \
           -DDILITHIUM_MODE=$(MLDSA_MODE) $(NTT_DEF) $(BOARD_DEF) -Isrc -Imldsa $(ZIRAH_EXTRA)
FLAGS_ID := $(CFLAGS) NTT=$(NTT) BOARD=$(BOARD)
CPPFLAGS += -MMD -MP

LDFLAGS += -T$(LDSCRIPT) $(ARCH_FLAGS) -nostartfiles -Wl,--gc-sections \
           -Wl,-Map=$(BUILD)/$(PROJECT).map --specs=nano.specs \
           -Wl,--print-memory-usage
LDLIBS  += -Wl,--start-group -lc -lgcc -lm -lnosys -Wl,--end-group

all: $(BUILD)/$(PROJECT).bin $(BUILD)/$(PROJECT).hex
	@$(SIZE) $(BUILD)/$(PROJECT).elf

# Rebuild everything when the compiler flags change (e.g. a new ZIRAH_EXTRA).
FLAGS_FILE := $(BUILD)/cflags.txt
$(FLAGS_FILE): FORCE
	@mkdir -p $(BUILD)
	@echo '$(FLAGS_ID) $(CPPFLAGS)' | cmp -s - $@ || echo '$(FLAGS_ID) $(CPPFLAGS)' > $@

$(BUILD)/%.o: %.c $(FLAGS_FILE)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(CPPFLAGS) -c $< -o $@

$(BUILD)/%.o: %.S $(FLAGS_FILE)
	@mkdir -p $(dir $@)
	$(CC) $(ARCH_FLAGS) -g -Imldsa-m4 -c $< -o $@

$(BUILD)/$(PROJECT).elf: $(OBJS) $(LDSCRIPT) $(LIBDEPS)
	$(CC) $(OBJS) $(LDFLAGS) $(LDLIBS) -o $@

$(BUILD)/$(PROJECT).bin: $(BUILD)/$(PROJECT).elf
	$(OBJCOPY) -O binary $< $@

$(BUILD)/$(PROJECT).hex: $(BUILD)/$(PROJECT).elf
	$(OBJCOPY) -O ihex $< $@

$(LIBDEPS):
	$(MAKE) -C $(OPENCM3_DIR) TARGETS=stm32/f4

release: $(BUILD)/$(PROJECT).bin
	cp $< $(OUT)
	sha256sum $(OUT) > $(OUT).sha256

# The 15 largest stack frames (static analysis from -fstack-usage).
stack: $(BUILD)/$(PROJECT).elf
	@cat $$(find $(BUILD) -name '*.su') | awk -F'\t' '{print $$2 "\t" $$3 "\t" $$1}' | sort -n -r | head -15

clean:
	rm -rf $(BUILD) generated.$(DEVICE).ld

.PHONY: all release clean stack FORCE
FORCE:

include $(OPENCM3_DIR)/mk/genlink-rules.mk
-include $(OBJS:.o=.d)
