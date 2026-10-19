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

# GP6 parts (SLG46824/26) synthesize with greenpak_synth_gp6.ys
# (abc -lut 3: the GP6 fabric has no standalone 4-bit LUTs); other
# GreenPAK families use greenpak_synth.ys (abc -lut 4).
ifneq ($(filter SLG46824% SLG46826%,$(PART)),)
GYS = greenpak_synth_gp6.ys
else
GYS = greenpak_synth.ys
endif

.PHONY: all clean flash-hex chipdb

all: $(TOP).hex

# ---- yosys sanity check ------------------------------------------------
yosys-check:
	@command -v $(YOSYS_BIN) > /dev/null || { echo "ERROR: yosys not found"; exit 1; }

# ---- synthesis ---------------------------------------------------------
# HDL: verilog (default) or vhdl (make HDL=vhdl). Verilog sources: VSRC;
# VHDL sources: src/*.vhd (top entity named $(TOP)).
HDL   ?= verilog
VSRC  = src/top.v
VHDSRC = $(wildcard src/*.vhd)

# Yosys used for VHDL must have the ghdl plugin (oss-cad-suite ships it
# as share/yosys/plugins/ghdl.so); standalone 'ghdl --synth' cannot emit
# a yosys netlist. The plugin's compiled-in library prefix does not
# match the oss-cad-suite layout, so point it at the ghdl installation's
# std/ieee libraries (override GHDL_PREFIX_DIR if yours lives elsewhere).
GHDL_PREFIX_DIR ?= $(patsubst %/libexec/,%/lib/ghdl,$(patsubst %/bin/,%/lib/ghdl,$(dir $(realpath $(shell command -v ghdl 2>/dev/null)))))
GHDL_PREFIX_ENV = $(if $(wildcard $(GHDL_PREFIX_DIR)/ieee),GHDL_PREFIX=$(GHDL_PREFIX_DIR),)

ifeq ($(HDL),vhdl)
ifeq ($(FAMILY),greenpak)
$(TOP)_synth.json: $(VHDSRC)
	sed -e 's|^read_verilog -lib greenpak4/cells_sim.v|ghdl --std=08 $(VHDSRC) -e $(TOP)|' \
	    -e '/^read_verilog design.v/d' \
	    -e 's|greenpak4/|$(FLOW)/greenpak4/|g' \
	    -e 's|write_json out.json|write_json $@|' \
	    $(FLOW)/$(GYS) > synth_gp4.ys
	$(GHDL_PREFIX_ENV) $(YOSYS_BIN) -m ghdl -s synth_gp4.ys > $(TOP)_vhdl.log 2>&1 \
	  || { rc=$$?; tail -15 $(TOP)_vhdl.log; \
	       grep -q "netlists.adb\|vhdl import failed\|unhandled signal\|foreign exception" $(TOP)_vhdl.log && { \
	         echo; \
	         echo "The ghdl yosys plugin failed. With oss-cad-suite this usually means"; \
	         echo "mixed component dates (a stale lib/libghdl* next to a newer bin/):"; \
	         echo "  ls -la $$(dirname $$(command -v yosys))/../lib/libghdl*"; \
	         echo "Fix: re-extract a FRESH oss-cad-suite tarball over the old directory."; \
	       }; rm -f $@; exit $$rc; }
else
$(TOP)_synth.json: $(VHDSRC)
	cp $(FLOW)/forgefpga_prims.v $(FLOW)/forgefpga_map.v .
	$(GHDL_PREFIX_ENV) $(YOSYS_BIN) -m ghdl -p "read_verilog -lib forgefpga_prims.v; \
	  ghdl --std=08 $(VHDSRC) -e $(TOP); hierarchy -check -top $(TOP); proc; flatten; \
	  tribuf -logic; deminout; synth -run coarse; memory_map; opt -full; \
	  iopadmap -bits -inpad INBUF O:PAD -outpad OUTBUF I:PAD; \
	  techmap -map +/techmap.v; opt -fast; dfflegalize -cell \\$$_DFF_P_ 0; \
	  abc -lut 4 -dress; clean; techmap -map forgefpga_map.v; clean; \
	  hierarchy -check; write_json $@" > $(TOP)_vhdl.log 2>&1 \
	  || { rc=$$?; tail -15 $(TOP)_vhdl.log; rm -f $@; exit $$rc; }
endif
else
design.v: $(VSRC)
	cat $(VSRC) > $@

ifeq ($(FAMILY),greenpak)
# works with ANY modern yosys (oss-cad-suite, Homebrew, master) - the
# greenpak4 techlib is vendored in flow/greenpak4/ and the pass sequence
# replicates yosys v0.59's synth_greenpak4 (removed from master)
$(TOP)_synth.json: design.v
	sed -e 's|-top top|-top $(TOP)|' \
	    -e 's|greenpak4/|$(FLOW)/greenpak4/|g' \
	    -e 's|write_json out.json|write_json $@|' \
	    $(FLOW)/$(GYS) > synth_gp4.ys
	$(YOSYS_BIN) -s synth_gp4.ys
else
$(TOP)_synth.json: design.v
	cp $(FLOW)/forgefpga_prims.v $(FLOW)/forgefpga_map.v .
	$(YOSYS_BIN) -p "read_verilog -lib forgefpga_prims.v; read_verilog design.v; \
	  hierarchy -check -top $(TOP); proc; flatten; tribuf -logic; deminout; \
	  synth -run coarse; memory_map; opt -full; \
	  iopadmap -bits -inpad INBUF O:PAD -outpad OUTBUF I:PAD; \
	  techmap -map +/techmap.v; opt -fast; dfflegalize -cell \$$_DFF_P_ 0; \
	  abc -lut 4 -dress; clean; techmap -map forgefpga_map.v; clean; \
	  hierarchy -check; write_json $@"
endif
endif

# ---- VHDL style lint (vsg; informational, does not gate the build) ------
# Mirrors the TerosHDL/VSCode vsg linting; 'vsg --fix' applies fixes.
.PHONY: lint
lint:
	@if ! command -v vsg > /dev/null; then \
	  echo "vsg not found (pip3 install vsg)"; exit 1; \
	elif [ -n "$(VHDSRC)" ]; then \
	  vsg -f $(VHDSRC) || \
	    echo "note: style violations do not affect synthesis; fixes: vsg -f $(VHDSRC) --fix"; \
	else \
	  echo "no VHDL sources in src/*.vhd"; \
	fi

# ---- place & route -----------------------------------------------------
PCFOPT = $(if $(wildcard $(PCF)),--vopt pcf=$(PCF),)

$(TOP)_routed.rpt: $(TOP)_synth.json $(CHIPDB) $(PCF)
	$(NEXTPNR) --uarch $(UARCH) --vopt chipdb=$(CHIPDB) $(PCFOPT) \
	  --vopt rpt=$@ --json $< --freq $(FREQ)

# ---- NVM assembly ------------------------------------------------------
$(TOP).nvm $(TOP).nvm.txt $(TOP).bin $(TOP).hex: $(TOP)_routed.rpt $(CHIPDB)
	python3 $(TOOLS)/bitstream/assemble.py \
	  --chipdb $(CHIPDB) --routed $(TOP)_routed.rpt --out $(TOP)
	-python3 $(TOOLS)/bitstream/verify.py --chipdb $(CHIPDB) \
	  --routed $(TOP)_routed.rpt --json $(TOP)_synth.json --image $(TOP).bin

flash-hex: $(TOP).hex
	@echo "flashable image: $(TOP).hex (Intel HEX) / $(TOP).bin"
	@echo "Go Configure import file: $(TOP).nvm.txt"

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
	rm -f *.json *.rpt *.bin *.hex *.nvm *.nvm.txt design.v out.v forgefpga_*.v *.work
