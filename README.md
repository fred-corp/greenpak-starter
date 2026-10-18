# greenpak-starter

Open-source GreenPAK / ForgeFPGA flow: HDL -> synthesis -> place &
route -> NVM bitstream -> I2C flash. Starter repo with everything
needed to go from Verilog/VHDL to a GreenPAK image.

## Prerequisites

- **[oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build) on
  your PATH** - provides `yosys` (the GreenPAK flow uses a compat
  script, so any modern yosys works; no ENABLE_GREENPAK4 build needed).
- **python3** (3.7+)
- cmake + boost + eigen3 for the one-time nextpnr build
  (macOS: `port install boost eigen3`; Debian/Ubuntu:
  `apt install cmake libboost-all-dev libeigen3-dev`)

Note: oss-cad-suite's `nextpnr-generic` is NOT usable as-is - uarchs
are compiled in, so ours must be built once:

    tools/build-nextpnr.sh     # -> tools/nextpnr/build/nextpnr-generic

## Quickstart

    tools/build-nextpnr.sh                     # one-time
    cp -r templates/basic-project myproject && cd myproject
    make TOP=top PART=SLG46826V                # -> top.bin/.hex/.nvm
    make flash                                 # dry-run (dummy backend)
    make flash FLASH_BACKEND=i2cdriver FLASH_PORT=/dev/ttyUSB0
    # backends: i2cdriver | flipper | smbus | dummy

Pinout constraints go in `pinout.pcf` (`set_io <port> PIN16` or
Vivado-style `set_property PACKAGE_PIN`). VHDL: `make HDL=vhdl`
(requires ghdl).

## Layout

```
templates/basic-project   copy-per-design Makefile project
tools/build-nextpnr.sh    one-time uarch build
tools/flash               I2C flasher (shadow mode = reversible)
uarch/forgefpga           nextpnr-generic Viaduct uarch (greenpak +
                          forgefpga aliases, JSON chipdb driven)
flow/                    yosys scripts + vendored greenpak4 techlib
chipdb/                  device databases + generator + extracted
                          register tables (device_regs/)
docs/usage.md             full documentation
```

## Status (honest)

The flow produces images whose LUT truth-table bits land in
verified, Go-Configure-extracted device registers (SLG46826/46824).
Matrix connection bits are still being finalized upstream - see the
main analysis repo. Shadow-mode flashing is reversible and safe;
OTP mode requires datasheet verification before first use.

## License & Acknowledgements

Made with ❤️, lots of ☕️, and lack of 🛏️

Published under GNU AGPLv3  
[![License: AGPL v3](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.en.html)  
[GNU AGPLv3](https://www.gnu.org/licenses/agpl-3.0.en.html)
