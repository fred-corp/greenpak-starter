# greenpak-starter

Open-source GreenPAK / ForgeFPGA flow: HDL -> synthesis -> place &
route -> NVM bitstream -> I2C flash. Starter repo with everything
needed to go from Verilog/VHDL to a GreenPAK image.

## Prerequisites

- **[oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on
  your PATH** - provides `yosys` (the GreenPAK flow uses a compat
  script, so any modern yosys works; no ENABLE_GREENPAK4 build needed)
  and the ghdl yosys plugin for VHDL. NOTE: keep your suite
  internally consistent - if you extract a new suite tarball over an
  old one with mixed component dates, the ghdl plugin may crash;
  re-extract fresh if `make HDL=vhdl` reports an Ada assertion.
- **python3** (3.7+)
- cmake + boost + eigen3 for the one-time nextpnr build
  (macOS: `port install boost eigen3`; Debian/Ubuntu:
  `apt install cmake libboost-all-dev libeigen3-dev`)
- optional: **vsg** (`pip3 install vsg`) for `make lint`
  (VHDL style checks, the same tool TerosHDL uses in VSCode)

Note: oss-cad-suite's `nextpnr-generic` is NOT usable as-is - uarchs
are compiled in, so ours must be built once:

    tools/build-nextpnr.sh     # -> tools/nextpnr/build/nextpnr-generic

## Quickstart

    tools/build-nextpnr.sh                     # one-time
    cp -r templates/basic-project myproject && cd myproject
    make TOP=top PART=SLG46826V                # -> top.bin/.hex/.nvm/.nvm.txt
    make lint                                  # VHDL style check (vsg)
    make flash                                 # dry-run (dummy backend)
    make flash FLASH_BACKEND=i2cdriver FLASH_PORT=/dev/ttyUSB0
    # backends: i2cdriver | flipper | smbus | dummy

`top.nvm.txt` is the Go Configure Software Hub NVM format (same as
the app's own export files): create/open an SLG46826V design in Go
Configure, then import `top.nvm.txt` to see the design reconstructed
from our bitstream.

Pinout constraints go in `pinout.pcf` (`set_io <port> PIN16` or
Vivado-style `set_property PACKAGE_PIN`). VHDL: `make HDL=vhdl`.

The NVM encoding is datasheet-exact and validated in both directions:
the register model decodes real Go Configure designs' compiled images
into their exact intended functions, and `bitstream/verify.py`
re-decodes every assembled image and checks it against the synthesized
netlist (the template Makefile runs it automatically after assembly).

## Layout

```
templates/basic-project   copy-per-design Makefile project
tools/build-nextpnr.sh    one-time uarch build (rebuilds on source changes)
tools/flash               I2C flasher (shadow mode = reversible)
uarch/forgefpga           nextpnr-generic Viaduct uarch (greenpak +
                          forgefpga aliases, JSON chipdb driven)
bitstream/                NVM assembler + round-trip verifier
flow/                     yosys scripts + vendored greenpak4 techlib
                          + design examples (combinational, counter,
                          async-reset counter, pattern generator)
chipdb/                   device databases + generator + extracted
                          register tables (device_regs/) + default
                          NVM images (defaultnvm/)
docs/usage.md             full documentation
```

## Status (honest)

The SLG46826/SLG46824 flow is validated end-to-end:

**Ground-truth validated** (register-for-register against
Go Configure-compiled .gp6 designs):
- combinational: matrix connections, LUT truth tables including the
  multifunction-block LUTs (LUT3_7-13, LUT4_0), pin map, output
  enables (static register or matrix OE field per pad class), pulled
  output pads
- sequential: the DFF mode of the main LUT macrocells AND of the MF
  blocks (mode selects, control bits, initial polarity, port mapping)
- the pattern generator (PGEN) registers - and synthesizable from HDL
  by instantiating `GP_PGEN` (flow/examples/pgen_test.v)

**Round-trip verified** (every assembled image re-decoded and compared
against the yosys netlist for all input combinations; clocked cycle
simulation for sequential designs; the verifier demonstrably catches
corrupted truth tables and connection fields): everything above plus
async-reset DFFs (GP_DFFSR).

The fabric model covers the full logic inventory (4x LUT2 + 7x LUT3
+ 8 MF-block LUTs + DFF modes + PGEN slot). SCL/SDA pads are not
matrix-connectable; IO6/IO7 are output-only.

**Remaining gaps:** the MF-block CNT/DLY counters are decoded but not
synthesizable from HDL yet; latch and nQ DFF variants are assembled
but not exercised by a test design; other GreenPAK families
(GP3/4/5) and ForgeFPGA bitstreams are scaffold-level; explicit
pin-mode configuration (Schmitt/open-drain/pull strength) is
extracted but not exposed to designs. Shadow-mode flashing is
reversible and safe; OTP mode requires the datasheet programming
voltage on VDD (see the flasher warning) and a first-hardware-verify
before trusting it.


## License & Acknowledgements

Made with ❤️, lots of ☕️, and lack of 🛏️

Published under GNU AGPLv3  
[![License: AGPL v3](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.en.html)  
[GNU AGPLv3](https://www.gnu.org/licenses/agpl-3.0.en.html)
