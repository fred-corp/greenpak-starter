# forgefpga uarch for nextpnr-generic

A `nextpnr-generic --uarch forgefpga` package for Renesas ForgeFPGA
(SLG470xx/471xx/475xx/479xx) and GreenPAK (SLG46xxx) devices.

The fabric is loaded at runtime from a JSON chipdb so that the
reverse-engineering tooling in this repository can emit device data
incrementally (BELs, switch-matrix wires, PIPs, IO pads) without
recompiling nextpnr.

## Wiring into nextpnr

Two files must be dropped into a nextpnr source tree:

    cp forgefpga.cc <nextpnr>/generic/viaduct/forgefpga/forgefpga.cc
    cp constids.inc <nextpnr>/generic/viaduct/forgefpga/constids.inc

and register the sources in `generic/CMakeLists.txt`:

    foreach (f fabulous/constids.inc forgefpga/constids.inc)
        ...

    aux_source_directory(generic/viaduct/forgefpga V_FILES_FOR_generic)
    target_sources(nextpnr-gui PRIVATE ${V_FILES_FOR_generic})  # or the
    # equivalent chain used for the fabulous uarch in your nextpnr revision.

Note: `generic/main.cc` does not need changes; the uarch self-registers via
the `ViaductArch` constructor, so `--uarch forgefpga` appears automatically.

## Usage

    nextpnr-generic --uarch forgefpga \
        --vopt chipdb=chipdb/slg47125.json \
        --json synth.json --freq 10

## Chipdb JSON schema

See the header comment of `forgefpga.cc` and the generator
`chipdb/gen_chipdb.py`.

## Status

- The uarch itself is complete: wires, pips, bels, IO, pack
  (IO buffer removal, constant folding via LUT4), bel buckets.
- Device chipdbs are generated from extracted Go Configure data
  (see docs/findings.md). The GreenPAK switch-matrix crosspoint map and
  the ForgeFPGA LUT fabric coordinates are being lifted from the
  decompiled `Slg470xx/47125MacrocellsEntities` classes
  (analysis/extracted/gp6_decomp_macrocells/).
