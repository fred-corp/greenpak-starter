# Using the open GreenPAK / ForgeFPGA flow

End-to-end: HDL source -> synthesis -> place & route -> NVM bitstream
image -> programmer file. This documents the current state of the tool
chain in this repository; read docs/bitstream.md for the
reverse-engineering background and docs/findings.md for what is
validated vs scaffold.

## Layout

```
flow/               yosys synthesis scripts + prims + techmaps
uarch/forgefpga/    ~/Downloads/nextpnr/build/nextpnr-generic Viaduct uarch (drop into nextpnr)
chipdb/             device databases (gen_chipdb.py + device_regs/)
bitstream/          NVM assembler (routed design -> .bin/.hex/.nvm/
                    .nvm.txt Go Configure import format)
templates/          copy-per-project template (Makefile + HDL stubs)
analysis/           reverse-engineering tooling + decompiled corpora
formats/            .gp design-file parser
docs/               findings, bitstream model, Ghidra workflow
```

## Quickstart (Verilog)

One-time setup (see Prerequisites below for details):

    # yosys: any on PATH works (Homebrew/oss-cad-suite)
    # nextpnr: one-time build with our uarch (oss-cad-suite's binary
    # lacks --uarch greenpak/forgefpga since uarchs are compiled in)
    tools/build-nextpnr.sh

Then per design:

    cp -r templates/basic-project myproject && cd myproject
    echo "VSRC = src/top.v src/mine.v" > sources.mk  # or edit Makefile
    make TOP=top PART=SLG46826V
    # -> top.bin, top.hex (Intel HEX), top.nvm (bit image),
    #    top.nvm.txt (Go Configure import format)
    make flash FLASH_BACKEND=dummy    # dry-run; see the Flashing section

`top.nvm.txt` uses the exact format of Go Configure Software Hub's
own NVM export files (one line per NVM bit, `index<TAB><TAB>value<TAB>
<TAB>//`, CRLF line endings - byte-identical structure to the app's
exports, verified against a real one). The file carries no device
identifier: open/create a design for the same part in Go Configure
before importing it.

## Quickstart (VHDL)

VHDL goes through the ghdl yosys plugin (`yosys -m ghdl`); yosys alone
cannot read VHDL, and standalone `ghdl --synth` only writes a VHDL
netlist - it cannot produce a yosys JSON.

    make TOP=top PART=SLG46826V HDL=vhdl

Requirements:

- a yosys with the ghdl plugin. oss-cad-suite ships it
  (`share/yosys/plugins/ghdl.so`); with a custom yosys, build
  ghdlsynth against your GHDL.
- for oss-cad-suite, the plugin needs `GHDL_PREFIX` pointing at the
  suite's std/ieee libraries (the template Makefile sets this
  automatically from `ghdl`'s location; override with
  `GHDL_PREFIX_DIR=...`).
- IMPORTANT: the plugin and lib/libghdl must come from the SAME
  oss-cad-suite tarball. If the plugin crashes with an Ada assertion
  (`netlists.adb` / `unhandled signal`), your suite has mixed
  component dates - re-extract a fresh tarball over the old directory.

Style linting (the same vsg tool TerosHDL uses in VSCode,
informational - style does not gate the build):

    make lint                    # vsg report on src/*.vhd
    vsg -f src/top.vhd --fix     # apply fixes

## Choosing a part

    python3 chipdb/gen_chipdb.py --list          # 168 known parts
    make chipdb FAMILY=greenpak PART=SLG46533V  # regenerate a chipdb

Part families: GreenPAK 3-6 use yosys `synth_greenpak4` cells
(GP_2LUT/GP_3LUT/GP_4LUT/GP_DFF...); ForgeFPGA (SLG47xxx/479xx) uses
generic LUT4 cells. Family selection via `FAMILY=` in the Makefile.

## Prerequisites

**yosys** - any modern yosys works for BOTH flows, including
oss-cad-suite and Homebrew:

- The GreenPAK flow uses `flow/synth_greenpak4_compat.ys` - the exact
  pass sequence of yosys v0.59's `synth_greenpak4` (which was removed
  from yosys master) as a plain script, with the greenpak4 techlib
  vendored in `flow/greenpak4/`. Verified working on stock yosys
  0.70+: dm8200 synthesizes to GP_2LUT/GP_3LUT/GP_4LUT cells and
  routes with the pin constraints honored.
- A yosys that still has the compiled-in `synth_greenpak4`
  (e.g. Go Configure's bundled `external/yosys/v59/yosys`) also works
  via `flow/greenpak_synth.ys`.
- `tools/build-yosys.sh` (builds yosys v0.59 with ENABLE_GREENPAK4)
  remains as an optional fallback; note it is a v0.59 Makefile build
  (greenpak4 was removed from master, and master switched to CMake).
- The ForgeFPGA (LUT4) flow works with any yosys.

Known compat-script limitations vs the compiled-in command: the
`greenpak4_dffinv` optimization (inverted-clock DFF mapping) is not
replicated - use posedge clocks or map inverted DFFs manually.

**nextpnr-generic with the forgefpga/greenpak uarch** - oss-cad-suite
ships nextpnr-generic, but uarchs are COMPILED IN, so its binary has
no `--uarch forgefpga`/`greenpak`. One-time source build:

    tools/build-nextpnr.sh
    # -> tools/nextpnr/build/nextpnr-generic

(clones nextpnr, drops in uarch/forgefpga/, registers it in
generic/CMakeLists.txt, builds. Needs cmake, boost, eigen3 - macOS:
`port install boost eigen3`.)

**python3** (3.7+), and **GHDL** only if you use VHDL
(`brew install ghdl`; yosys cannot read VHDL natively).

## Validation against Go Configure

Any design made in Go Configure contains its own compiled NVM
(`<nvmData>` inside the .gp file). Compare our flow's output against
the app's compilation:

    python3 analysis/validate_design.py some_design.gp6 chipdb/device_regs

The harness decodes the app's LUT truth tables and matrix connection
fields at the register positions extracted from the app binaries, so a
design created in the GUI is a test vector for our register model.

## Pinout constraints (mapping ports to package pins)

Create a `pinout.pcf` in your project (the template ships one). Two
line syntaxes are accepted, so it works for both nextpnr and Vivado
users:

```
set_io gt PIN16
set_property PACKAGE_PIN PIN17 [get_ports lt]
```

- `<port>` is a top-level port name or a vector bit (`a[0]`)
- `<BEL>` is a package-pin BEL from the chipdb - GreenPAK IO BELs are
  named by physical pin (PIN2..PIN20 for SLG46826/46824; the real
  pinout was extracted from Go Configure's own pad captions), so
  constraints read like a datasheet
- pass it with `--vopt pcf=pinout.pcf` (the template Makefile does this
  automatically when pinout.pcf exists)

Unconstrained IOs are auto-placed on free pins. Verified: dm8200's
outputs constrained to PIN16/17/18 and inputs to PIN2/3 land exactly
there in the routed report.

## What is real vs scaffold (be honest with yourself)

GROUND-TRUTH VALIDATED (register-for-register against Go
Configure-compiled .gp6 designs), SLG46826/SLG46824:
- LUT truth tables: main macrocells and multifunction blocks
- matrix connections (6-bit field per matrix output = Matrix Input
  Number, datasheet Tables 21/22)
- DFF mode: main-macrocell mode selects + control bits, and MF-block
  DFF mode + port mapping
- PGEN registers (synthesizable: instantiate GP_PGEN -
  flow/examples/pgen_test.v)
- pin modes: output pulls cleared, per-pad-class output enables
ROUND-TRIP VERIFIED (every assembled image re-decoded and compared
against the yosys netlist for all input combinations; clocked
cycles for sequential designs): everything above plus async-reset
DFFs (GP_DFFSR)
DECODED, not synthesizable from HDL yet:
- MF-block CNT/DLY counters (mode/clock/data registers)
SCAFFOLD:
- other GreenPAK families (GP3/4/5) chipdbs; ForgeFPGA bitstream
- latch and nQ DFF variants are assembled but not exercised by a
  test design

Assembled SLG46826/SLG46824 images contain the full design encoding
at ground-truth-validated register positions; the remaining
hardware-side caveat is first-silicon verification of the flasher
sequence (shadow mode is reversible and safe to experiment with).

## What Go Configure files help most

Every .gp design file the app saves contains the compiled NVM, so
files created in the GUI double as ground truth. Most useful:
- one design per device family (SLG46826 done: combinational, MF
  LUT/DFF/counter, main-macrocell DFF and PGEN all covered; SLG47004/
  SLG47125/SLG47920 designs would validate the ForgeFPGA path)
- designs that use features not yet covered: pipe delay, ripple
  counters, ACMPs, OSC configs, different IO modes (each validates
  one more register group)
- the exported programmer files (.hex/.gpb) for a design if available
  (validates the container format)

## Flashing over I2C (`make flash`)

`tools/flash/greenpak_flash.py` drives third-party I2C hardware to
load the assembled image:

    make flash                                   # dry-run (dummy backend)
    make flash FLASH_BACKEND=i2cdriver FLASH_PORT=/dev/ttyUSB0
    make flash FLASH_BACKEND=flipper  FLASH_PORT=/dev/ttyUSB1
    make flash FLASH_BACKEND=smbus    FLASH_PORT=1        # Linux /dev/i2c-1

- **shadow mode (default)** writes the 256-byte register shadow -
  the design runs immediately and it is fully reversible (lost on
  power-down). This is the right mode for development.
- **otp mode** programs the NVM: irreversible; requires `--force`.
  The control-register constants are the common GreenPAK4-6 shape -
  **verify against your device datasheet's "I2C programming" chapter
  before first use.** The authoritative sequence can also be lifted
  from the decompiled `BootloaderProgrammer` / `nvmSequence` classes
  in `analysis/extracted/` (not yet transcribed).
- Backends: I2CDriver (`pip install i2cdriver`), Flipper Zero with an
  I2C Tools CLI app (line protocol - adjust WRITE_TMPL/READ_TMPL in
  the script if your app's commands differ), Linux smbus2/periphery,
  and dummy (dry-run).
