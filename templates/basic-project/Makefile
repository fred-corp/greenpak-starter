# Renesas GreenPAK / ForgeFPGA open flow - project template
#
# Copy this directory for each design. Set PART, TOP and FAMILY, then:
#   make            # synth + place/route + NVM assembly
#   make flash-hex  # outputs <TOP>.bin / .hex (Intel HEX) / .nvm
#
# Yosys resolution order (greenpak4 support is required for FAMILY=greenpak;
# stock oss-cad-suite / Homebrew yosys do NOT have it):
#   1. $(YOSYS) from the environment / make invocation
#   2. the yosys built by tools/build-yosys.sh (repo tools/yosys/yosys)
#   3. the copy bundled inside Go Configure
#   4. whatever 'yosys' is on PATH (will fail for greenpak unless it
#      has ENABLE_GREENPAK4)
#
# Requirements: see docs/usage.md (nextpnr-generic with the forgefpga/
# greenpak uarch, python3, GHDL for VHDL).

PART   ?= SLG46826V
TOP    ?= top
# greenpak | forgefpga
FAMILY ?= greenpak
# target clock in MHz (GreenPAK runs slow)
FREQ   ?= 1
# port-to-pin constraints (set_io / XDC style)
PCF    ?= pinout.pcf

# Path to the greenpak-starter checkout (override with TOOLS=... if you
# cloned it elsewhere; the template works from any directory)
TOOLS  ?= $(HOME)/Downloads/greenpak-starter
FLOW    = $(TOOLS)/flow
CHIPDB  = $(TOOLS)/chipdb/$(shell echo $(PART) | tr 'A-Z' 'a-z' | sed 's/[a-z]*$$//').json
NEXTPNR ?= $(HOME)/Downloads/nextpnr/build/nextpnr-generic
ifeq ($(wildcard $(NEXTPNR)),)
NEXTPNR := $(TOOLS)/tools/nextpnr/build/nextpnr-generic
endif

APP_YOSYS = /Applications/Go Configure Software Hub.app/Contents/MacOS/external/yosys/v59/yosys
YOSYS_BIN = $(firstword $(foreach y,$(YOSYS) $(TOOLS)/tools/yosys/yosys $(APP_YOSYS),$(if $(wildcard $y),$y,$(if $(shell command -v yosys),yosys,))))

ifeq ($(FAMILY),greenpak)
UARCH = greenpak
else
UARCH = forgefpga
endif

.PHONY: all clean flash-hex chipdb

all: $(TOP).hex

# ---- yosys sanity check ------------------------------------------------
yosys-check:
	@command -v $(YOSYS_BIN) > /dev/null || { echo "ERROR: yosys not found"; exit 1; }

# ---- synthesis ---------------------------------------------------------
# HDL: verilog (default) or vhdl (requires ghdl). For Verilog, list your
# sources in VSRC (top module must be named $(TOP)).
HDL   ?= verilog
VSRC  = src/top.v
VHDSRC = src/*.vhd

ifeq ($(HDL),vhdl)
$(TOP)_synth.json: $(VHDSRC)
	ghdl -a $(VHDSRC)
	ghdl --synth --std=08 $(TOP) -o $@
else
$(TOP)_synth.json: $(VSRC)
	cat $(VSRC) > design.v
ifeq ($(FAMILY),greenpak)
# works with ANY modern yosys (oss-cad-suite, Homebrew, master) - the
# greenpak4 techlib is vendored in flow/greenpak4/ and the pass sequence
# replicates yosys v0.59's synth_greenpak4 (removed from master)
	sed -e 's|-top top|-top $(TOP)|' \
	    -e 's|greenpak4/|$(FLOW)/greenpak4/|g' \
	    -e 's|write_json out.json|write_json $@|' \
	    $(FLOW)/greenpak_synth.ys > synth_gp4.ys
	$(YOSYS_BIN) -s synth_gp4.ys
else
	cp $(FLOW)/forgefpga_prims.v $(FLOW)/forgefpga_map.v .
	$(YOSYS_BIN) -p "read_verilog -lib forgefpga_prims.v; read_verilog $(VSRC); \
	  hierarchy -check -top $(TOP); proc; flatten; tribuf -logic; deminout; \
	  synth -run coarse; memory_map; opt -full; \
	  iopadmap -bits -inpad INBUF O:PAD -outpad OUTBUF I:PAD; \
	  techmap -map +/techmap.v; opt -fast; dfflegalize -cell \$$_DFF_P_ 0; \
	  abc -lut 4 -dress; clean; techmap -map forgefpga_map.v; clean; \
	  hierarchy -check; write_json $@"
endif
endif

# ---- place & route -----------------------------------------------------
PCFOPT = $(if $(wildcard $(PCF)),--vopt pcf=$(PCF),)

$(TOP)_routed.rpt: $(TOP)_synth.json $(CHIPDB) $(PCF)
	$(NEXTPNR) --uarch $(UARCH) --vopt chipdb=$(CHIPDB) $(PCFOPT) \
	  --vopt rpt=$@ --json $< --freq $(FREQ)

# ---- NVM assembly ------------------------------------------------------
$(TOP).nvm $(TOP).bin $(TOP).hex: $(TOP)_routed.rpt $(CHIPDB)
	python3 $(TOOLS)/bitstream/assemble.py \
	  --chipdb $(CHIPDB) --routed $(TOP)_routed.rpt --out $(TOP)

flash-hex: $(TOP).hex
	@echo "flashable image: $(TOP).hex (Intel HEX) / $(TOP).bin"

# ---- flashing over I2C (third-party tools) ------------------------------
# BACKEND: i2cdriver | flipper | smbus | dummy (dry-run)
# mode: shadow = run the design live (default, reversible);
#       otp    = program the NVM (irreversible, needs FORCE=1)
FLASH_BACKEND ?= dummy
FLASH_PORT    ?= /dev/ttyUSB0
FLASH_ADDR    ?= 0x08
FLASH_MODE    ?= shadow
FLASHER = $(TOOLS)/tools/flash/greenpak_flash.py

flash: $(TOP).bin
	python3 $(FLASHER) --backend $(FLASH_BACKEND) --port $(FLASH_PORT) \
	  --addr $(FLASH_ADDR) --mode $(FLASH_MODE) $(TOP).bin

# ---- device chipdb (regenerate if needed) ------------------------------
chipdb:
	python3 $(TOOLS)/chipdb/gen_chipdb.py $(FAMILY) $(PART) > $(CHIPDB)
	@echo "note: for real register data pass --nvm <defaultNVM txt>"

clean:
	rm -f *.json *.rpt *.bin *.hex *.nvm design.v out.v forgefpga_*.v *.work
