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

The NVM encoding is datasheet-exact and validated in both directions:
the register model decodes a real Go Configure design's compiled image
into its exact intended function, and `bitstream/verify.py` re-decodes
every assembled image and checks it against the synthesized netlist
(the template Makefile runs it automatically after assembly).

## Layout

```
templates/basic-project   copy-per-design Makefile project
tools/build-nextpnr.sh    one-time uarch build
tools/flash               I2C flasher (shadow mode = reversible)
uarch/forgefpga           nextpnr-generic Viaduct uarch (greenpak +
                          forgefpga aliases, JSON chipdb driven)
bitstream/                NVM assembler + round-trip verifier
flow/                     yosys scripts + vendored greenpak4 techlib
chipdb/                   device databases + generator + extracted
                          register tables (device_regs/) + default
                          NVM images (defaultnvm/)
docs/usage.md             full documentation
```

## Status (honest)

The SLG46826/SLG46824 flow is validated end-to-end for combinational
logic: LUT truth tables, matrix connections (fields + Matrix Input
Numbers from datasheet Tables 21/22), pin map and pad output/OE
fields are datasheet-exact and proven against a real Go
Configure-compiled design as well as by assembler round-trip.

Not yet hardware-correct: sequential logic (DFF/latch mode
registers), designs that need the multifunction blocks (MF LUTs),
and per-pin IO mode configuration. Shadow-mode flashing is reversible
and safe; OTP mode requires the datasheet programming voltage on VDD
(see the flasher warning) and a first-hardware-verify before trusting
it.


## License & Acknowledgements

Made with ❤️, lots of ☕️, and lack of 🛏️

Published under GNU AGPLv3  
[![License: AGPL v3](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.en.html)  
[GNU AGPLv3](https://www.gnu.org/licenses/agpl-3.0.en.html)
