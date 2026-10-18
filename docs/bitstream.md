# The GreenPAK / ForgeFPGA NVM bitstream model

This document describes the reverse-engineered bitstream model and the
tools that implement it. Everything here was derived from the Go Configure
binaries (GP6, libprinteditor.1.0.0.dylib, DesignProgrammerTool); the
supporting decompilation is in `analysis/extracted/`.

## The model (confirmed)

A device bitstream is a **flat array of bits**. Go Configure calls these
"register IDs" but they are simply absolute indexes into this array
(`SequenceDevelopment::setValueByIndexes(QList<bool> &image,
QList<int> regIds, bool value)` in libprinteditor).

```
register r, bit b  ->  image index (r * 8 + b)
```

Every macrocell property is described by a list of bit indexes plus the
value(s) to encode:

| feature | encoding |
|---------|----------|
| LUT truth table | one bit index per truth-table entry (`LUT_CLASS` keeps a `QList<int> regIds`; `SetLUT::applyChanges` writes each via `NVM_CLASS::setValueToNVM(regId, bit)`) |
| DFF/LATCH options | single regIds (`*(int*)(dff+0x37c) = 0x5cb` etc.) |
| mux / counter / oscillator options | `NvmMap<Enum>` selection tables: enum -> (nvm value, label), the value written into the property's bit list |
| register ranges | `NVMUtils::getNvmAddressRange(a, b)` -> bit indexes a..b |

Per-device bit indexes are assigned as **code literals** in
`Slg<part>ChipPart::set*Registers()` functions, e.g. for SLG47004:

```
LUT #0:  output reg 0x5d4, truth table {const table @0x10325d790}
DFF #0:  set 0x5cb, reset 0x5ca, clock-invert 0x5c9
PGEN:    truth range 0x5e8..0x5eb
```

## The tool chain (extractors)

```
analysis/extract_chip_regs.py        (generalized, all assignment shapes)
    all Slg<part>ChipPart::set*Registers() -> chipregs_raw.json
    SLG46826: 304  SLG47004: 324  SLG47125: 388  SLG47920: 24 events

analysis/extract_regmaps.py          (earlier, narrower version)
    -> regmaps_raw.json (794 events, kept for reference)

analysis/extract_lut_regs.py
    setCombinationFunctionRegisters -> chipdb/device_regs/<DEV>_luts.json
    (curated truth tables, consumed automatically by gen_chipdb.py)

analysis/extract_nvm_maps.py
    NvmMap selection tables (enum -> value + label) -> nvm_maps.json
```

The mux topology corpus (`gp6_decomp_mux/`, 656 functions) contains the
per-device `muxIns`/`muxOuts` implementations
(`Slg47125ChipPart::muxIns()` defines the SLG47125 macrocell input mux
structure) - the source for the matrix connection bits.

## The tool chain (device flow)

```
analysis/extract_regmaps.py
    decompiled set*Registers bodies -> regmaps_raw.json
    (ordered events: literals, __DATA const tables, ranges)
    SLG46826: 107   SLG47004: 194   SLG47125: 273   SLG47920: 220 events

analysis/extract_nvm_maps.py
    NvmMap/NvmArrayMap selection tables (enum -> value + fabric net
    label) -> nvm_maps.json (190 map classes)

chipdb/gen_chipdb.py
    device data -> chipdb JSON including the "nvm" section:
    { bits, default, bel_bits: [{name, truth_table_bits: [idx...]}],
      pip_bits: [{name, bit}] }
    Currently emits scaffold allocations; the curation step
    (see "Curation" below) replaces them with extracted tables.

uarch/forgefpga (nextpnr)
    --vopt rpt=<file> in postRoute() dumps: per-cell BEL binding +
    parameters (INIT), every routed PIP (net + src/dst wires).

bitstream/assemble.py
    chipdb nvm section + routed report -> NVM bit image:
    <out>.bin  packed bytes (register r bit b -> byte r, bit b)
    <out>.hex  Intel HEX of the packed image
    <out>.nvm  raw "0/1" bit string
```

Flow:

    Verilog -> yosys (flow/*_synth.ys) -> nextpnr-generic --uarch forgefpga
        --vopt chipdb=<part>.json --vopt rpt=design.rpt --json out.json
    -> bitstream/assemble.py --chipdb <part>.json --routed design.rpt --out fpga

## Curation: from regmaps_raw.json to flashable images

The assembler writes whatever the chipdb `nvm` section says, so making
images bit-exact is pure data work:

1. **LUT truth tables** - `analysis/extract_lut_regs.py` parses the
   `setCombinationFunctionRegisters` bodies per device and emits
   `chipdb/device_regs/<DEV>_luts.json` (handles const-table lists,
   `QPodArrayOps::emplace` lists, output registers, DFF config writes
   and pipe-delay/ripple-counter/PGEN features). `gen_chipdb.py`
   automatically uses these tables for the chipdb `nvm` section when
   they exist (`"source": "extracted (device_regs)"`).

   Verified end-to-end for SLG46826V: dm8200's GP_2LUT/GP_3LUT truth
   bits now assemble into the REAL device registers:

   ```
   LUT2 #0..#3: truth bits 1152..1167   (0x480..0x467)
   LUT3 #0..#5: truth bits 1192..1231   (0x4a8..0x4cf)
   DFF/LATCH sharing: same NVM bits serve truth table in LUT mode and
   set/clock config in latch mode (extracted output shows the overlap)
   ```

   Remaining TODOs for full LUT coverage: the SLG46826 combination block
   yields 3 LUT2 + 6 LUT3 + PGEN + pipe-delay; the device's remaining
   LUTs/counters live in `setMultifunctionalRegisters` (101 raw events
   already extracted) and need the same parser patterns.

   UPDATE: the MF block is extracted
   (`chipdb/device_regs/SLG46826_mf.json`, 8 macrocells, per-field
   register lists with exact bit-order preserved, const-table
   stitching across overlapping `_DAT` labels). MF1-7 carry a 4-bit
   field + one shared single reg (the same structure family as the
   validated LUT2+DFF macrocells); MF0 a 7-bit field. Open
   interpretation: whether these fields are truth tables or input-mux
   registers needs a design that actually uses MF LUTs (the DM8200
   design does not - its logic fits in the combination block).
2. **Matrix input selections** - three pillars are now extracted; the
   final combination step remains:

   | pillar | file | content |
   |--------|------|---------|
   | selection tables | `analysis/extracted/nvm_maps.json` | per macrocell input: enum -> (value, label) e.g. "TIMER1 OUT" |
   | selection registers | `analysis/extracted/chipregs_raw.json` | per macrocell input: regId bit lists (slots/fields) |
   | source inventory | `chipdb/device_regs/<DEV>_mux_sources.json` | ordered matrix source nets (enum index -> net) |

   `analysis/extract_mux_sources.py` scrapes the decompiled
   `muxIns/muxOuts` bodies; coverage: SLG46801 (27/43), SLG47001
   (31/65), SLG47011 (73/104), SLG47105 (52/89), SLG47125 (46/83),
   SLG51000-51003 PMICs. Each source entry names the driving macrocell
   (item offset + pin accessor, e.g. `LutDffLatchPwmChopper::outQPin`).

   Open question RESOLVED: the `NvmMap`/`NvmArrayMap` tuple int IS the
   per-selection NVM bit pattern, written across the property's regId
   list. Proven by maps with distinct per-selection values (verified
   against const tables in the binary): `MatrixIn31..36Selection` =
   {GPIO2 OUT: 9, I2C OUT0: 8, Scaler Product OUT8: 2, ...};
   `ForgeK2PadDefinitions::GpioPUEnable` = [1, 2, 3, 8, 1, 9, 7, 6];
   `OSC::PreDivider` is an identity map (selection k -> value k).
   `nvm_maps.json` now carries cleaned `{key, value, label}` triples
   (190 map classes). One assumption to verify on hardware: bit order
   LSB-first into the regId list (bit 0 of value -> regIds[0]).
3. **IO / analog options** - remaining set*Registers groups (pads,
   oscillator, ACMP, Vref...) - same event format.
4. **Programmer container** - plain packed bytes are what the NVM image
   is; DesignProgrammerTool's writers (dpt_decomp_formats/) show the
   exact file containers (.hex and friends) if the Renesas programmer
   needs headers/checksums (`NVM_CLASS::checkSum32bit`).

## Device variants

GreenPAK6 variants share one ChipPart class in Go Configure (same
register map and NVM size); differences are component-inventory only:

- **SLG46824** uses the SLG46826 register map (both 2048-bit NVM) but
  **lacks the temperature sensor** (and possibly further resources).
  `chipdb/device_regs/variants.json` records this; `gen_chipdb.py`
  resolves variant -> base register tables automatically, but designs
  for variants must not use variant-missing macrocells until the
  per-variant component inventories are extracted (revision-specific
  item creation is not yet decompiled).

## Validation against real designs

`analysis/validate_design.py` decodes a Go Configure design file's
embedded compiled NVM (`<nvmData>`, the app reconstructs the entire
design from it) at the register positions our extraction predicts.

Validated with a real SLG46826V design (DM8200 comparator migration,
created in Go Configure 6.55):

- LUT2 truth tables decode as 0x9 (XOR - the comparator's bit
  difference function) at bits 1152-1163, exactly the extracted slots
- PGEN slot 0x718 decodes 0x1; the design's XML caption for that
  macrocell is literally "2-bit LUT3/PGEN" - slot identification
  confirmed
- LUT3 tables 0xB2 (cascade gt/lt mux), 0x80, 0x82 at bits
  1192-1231 - exactly the extracted SLG46826 layout
- The encoding inside nvmData confirms the LSB-first bit order
  (INIT bit 0 -> truth_regs[0])
- The design XML's LUT captions give the complete SLG46826 LUT
  inventory: 2-bit LUT0-3 (LUT/DFF/LATCH shared macrocells),
  3-bit LUT0-5 + LUT6 (pipe delay/ripple counter) in the combination
  block, MF0 = 4-bit LUT0 and MF1-7 = 3-bit LUT7-13 in the
  multifunctional block - matching the decompiled register assignment
  structure one to one.

Our yosys synthesis of the same dm8200 Verilog produces the same XOR
truth tables (0x9) with a different decomposition (synthesis vs.
manual migration), so whole-image bit equality is not expected across
decompositions - per-macrocell encoding, positions and bit order are
the validated properties.

## The matrix register formula (GP6, fully decoded)

`PIN_CLASS::registers(MatrixIndex)` computes each pin's connection
registers on the fly (verified against `ConnectivityMatrixBase`):

```
muxSize = 5/6/8 by pin type (or from MUX_SIZE[family] const table
          @0x1031d1630; SLG46826 family 0x20 -> 6)
for position in range(muxSize):
    reg(position) = ConnectivityMatrixBase::REG_ADDRESS_MUX(
        muxId, position, matrixIndex)
             = MUX_SIZE[family] * muxId + position + base + offset
    base/offset: 0 for SLG46826 (family 0x20); special-cased for
    families 0x22/0x23/0x25 (0x28/0x38/0x150/0x158/0x170 bases,
    matrixIndex==1 adds 0x400 unless revision type 0x2a)
```

So for SLG46826: **reg = 6 x muxId + sourcePosition** - each matrix
connection is one register bit at an arithmetic position, with the
driving source encoded by the value at that position.

`muxId` is decoded: `ConnectivityMatrixBase::resetPinsMuxes` iterates
the device's MUX_IN/MUX_OUT inventories (the lists already extracted
to `chipdb/device_regs/<DEV>_mux_sources.json`) and calls
`PIN_CLASS::setMux(pins, index, MatrixIndex)` with the loop index -
**muxId = the pin's index in the device's mux inventory**.

Complete GP6 matrix model:
```
per matrix-input pin (inventory index i, 6-bit field at regs 6i..6i+5):
    field value = index of the driving source within the pin's mux
                  source list (the per-pin crosspoint data)
```
The one remaining data source: the per-pin mux source lists (which
sources each input pin can select) - built by `CHIP::SetCMatrixGlobals`
for GP6 legacy (decompiled in gp6_decomp_cmatrix/).

RE trail for the legacy crosspoints (follow `gp6_decomp_cmatrix/`):
`CHIP::SetCMatrixGlobals` -> `SetCMatrixChipPartGlobals` (falls
through for SLG46826 since `useLegacyCMatrixGlobals()==true`) ->
`SetCMatrixLegacyGlobals` -> `LegacyGlobals::globals(CHIP*)` ->
`globalsImpl(CHIP*)` @0x1008704a0 builds the `GlobalPins` structure
via +0xe8 vtable virtuals on per-device objects, then feeds
`ConnectivityMatrixBase` whose `resetPinsMuxes` assigns muxIds and
per-pin source lists.

UPDATE: the full legacy corpus is decompiled
(`gp6_decomp_legacy/`, 689 files): `globalsImpl` (11,158 lines - the
legacy matrix builder) plus every macrocell's `initPins` (MsAcmp,
PhaseLockedLoop, PLL47910Ba, ForgeK2FpgaCore, ExtendedPatternGenerator,
...). The crosspoint extraction is a parsing task over this corpus:
per-macrocell initPins defines each pin and its mux source structure;
globalsImpl assembles the global matrix lists.

## Empirical matrix observations (DM8200_migrate.gp6)

Decoding the app-compiled NVM with the formula gives 28 non-zero
mux fields (pins 0-28) driven by 20 distinct sources:

- 10 sources drive 2 pins each, 8 drive 1 pin - matching the
  comparator's multi-fanout nets (e.g. a bit-difference XOR feeding
  both the cascade LUT and an output stage)
- The design's 4 GND connections (type0/n5 -> LUT inputs n8-n11/pin1)
  do NOT appear as matrix fields: no source drives 4 pins. Hypothesis:
  constant inputs are absorbed into the LUT truth tables at compile
  time (or set via a dedicated constant register), not routed through
  the matrix - consistent with yosys constant folding in our flow.
- The 4-to-1 multi-driver pin (LUT n3/pin1 from n4/n5/n6/n7 pin1) is
  GreenPAK wired logic - needs the connectionType semantics to decode
  (likely one matrix field + wired-mode register, or separate fields
  per wired contributor).

Remaining mapping for byte-exact assembly:
1. input pin inventory order (muxId 0-28 -> which macrocell input) -
   from globalsImpl's component iteration order
2. per-pin source list order (source index -> net) - globalsImpl's
   source list construction
3. constant-input handling (GND absorption) semantics
4. wired multi-driver semantics

## Known conventions / gotchas

- GreenPAK6 (SLG46826) uses the "legacy CMatrix" path
  (`CHIP::SetCMatrixGlobals`); SLG47xxx/479xx have per-part
  `setCMatrix()` implementations.
- Some devices encrypt their NVM (`NVMEncryption::encryptNvm`) - the
  ForgeFPGA SLG479xx OTP bitstreams are candidates; check
  `FpakBitstreamDataValidator` before flashing generated files.
- The defaultNVM txt files contain several address sections; register
  counts derived from them are upper bounds.
- The `--write` JSON of nextpnr does not contain placement; use the
  uarch `--vopt rpt=` report instead.

## Datasheet cross-check (SLG46826V DS rev 3.18, Table 59)

The official register map confirms the extracted model bit-for-bit:
- bits 5:0 = "Matrix OUT0" -> 6-bit matrix fields, LSB-first from bit 0
  (reg = 6*muxId + source) - formula confirmed
- the map names every muxId: Matrix OUT0 = IN0 of LUT2_0, OUT1 = IN1
  of LUT2_0, OUT2/3 = LUT2_3/PGen, ..., OUT42-48 = MULTFUNC (MF)
  LUT/Delay/Counter inputs - the full pin inventory, authoritative
- 1155:1152 = LUT2_0/DFF0 setting, 1159:1156 = LUT2_1, 1163:1160 =
  LUT2_2, 1167:1164 = LUT2_3_VAL/PGEN data - all match the extracted
  truth tables exactly
- bits 1232/1233/1234 = LUT2_x or DFFx Select - exact match with the
  extracted output registers (0x4d0-0x4d2)
- one extractor discrepancy found: datasheet LUT3_0/DFF3 setting =
  bits 1191:1184; the extractor's slot 0x720 produced an over-inclusive
  16-bit run (1168-1183) - the LUT3 slots shift by one; to fix in
  extract_lut_regs.py consecutive-run handling

## Datasheet cross-checks (SLG47004, SLG46824, SLG47910)

SLG47004 (ForgeFPGA, dial_s_a0011480684, Table 68):
- matrix fields identical in structure: bits 5:0 = "OUT0: IN0 of
  LUT2_0" (6-bit LSB-first fields from bit 0)
- "2-Bit LUT0 is defined by registers [1483:1480]" - EXACT match
  with the extracted truth bits [1480..1483]; LUT1 [1487:1484],
  LUT2 [1491:1488] all match; output select register [1492] matches
  the extracted out=1492
- open item: SLG47004's register map shows matrix fields from bit 0,
  but REG_ADDRESS_MUX special-cases family 0x25 with base 0x28 -
  needs the DefinedRevisions mapping for SLG47004 to resolve which
  family code it is.

SLG46824 (ds 2v0):
- register map IDENTICAL to SLG46826: "2-Bit LUT0 defined by
  reg<1155:1152>", output select reg<1232>, PGEN reg<1167:1164> -
  the shared-register-map variant handling is datasheet-confirmed
  (only the component inventory differs, e.g. no temperature sensor)

SLG47910 (r19ds0120):
- different architecture: the register map describes SPI/OTP boot
  configuration (24-bit base address, load-from-OTP/SPI control
  bits), not the GreenPAK-style matrix - the ForgeFPGA 479xx
  bitstream needs its own model (the ForgeK2 decompiled classes
  remain the source)
