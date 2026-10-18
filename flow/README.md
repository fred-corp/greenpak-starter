# flow - synthesis and place & route

Both flows were verified end-to-end on this machine (counter with DFFs and
pure-combinational design, placed and routed to completion).

## GreenPAK (SLG46xxx) - yosys greenpak4 cells

Requires a yosys with the greenpak4 arch enabled (`synth_greenpak4`)
- stock oss-cad-suite / Homebrew builds do NOT have it. Either use the
  app's bundled `external/yosys/v59/yosys`, or build one:

    ../tools/build-yosys.sh     # -> tools/yosys/yosys

Then run the flow with that binary:

    yosys -s greenpak_synth.ys

Chipdb:

    python3 chipdb/gen_chipdb.py greenpak SLG46826V > slg46826.json

Place & route:

    $NEXTPNR --uarch forgefpga \
        --vopt chipdb=slg46826.json --json out.json --freq 1

Do not pass `--no-iobs`; the current nextpnr master trips an IdString
assertion with it, and the GP_* pad buffers are already inserted by yosys.

## ForgeFPGA (SLG47xxx/479xx) - generic LUT4 cells

    cd flow
    yosys -s forgefpga_synth.ys           # reads design.v, writes out.json

Chipdb:

    python3 chipdb/gen_chipdb.py forgefpga SLG47125V > slg47125.json

Place & route:

    $NEXTPNR --uarch forgefpga \
        --vopt chipdb=slg47125.json --json out.json --freq 10

## Example

    cd flow
    cp examples/forgefpga_counter.v design.v
    yosys -s forgefpga_synth.ys
    $NEXTPNR --uarch forgefpga --vopt chipdb=../chipdb/slg47125.json \
        --json out.json --freq 10

## dm8200 (4-bit cascadable comparator)

The synth scripts expect a module named `top`, so wrap the example:

    cd flow
    cat examples/dm8200.v > design.v
    cat >> design.v << 'EOF'
    module top(input [3:0] a, b, input gt_in, lt_in, eq_in, output gt, lt, eq);
        dm8200 u(.a(a), .b(b), .gt_in(gt_in), .lt_in(lt_in), .eq_in(eq_in),
                 .gt(gt), .lt(lt), .eq(eq));
    endmodule
    EOF

Verified utilization:

- ForgeFPGA cells (../chipdb/slg47920.json, --freq 10): 12x LUT4, 14 IOB
- GreenPAK cells (../chipdb/slg46826.json, --freq 1): 14 logic cells
  (6x GP_2LUT + 8x GP_3LUT with greenpak_synth_gp6.ys) - routes and
  round-trip-verifies on all 2048 input combinations

The SLG46826 fabric model is the full datasheet-confirmed inventory:
4x LUT2 + 7x LUT3 macrocells plus the 8 multifunction-block LUTs
(LUT3_7..13 in MULTFUNC_8BIT_1..7, LUT4_0 in MULTFUNC_16BIT_0; the MF
block mode select "Single LUT" is the all-zero default, so no mode
bits are needed). Smaller LUT functions may be placed in wider LUT
macrocells (unused input stays unconnected = GND), as in Go Configure.

Pad model: SCL/SDA pins are not connectable to the matrix (no BEL);
IO6/IO7 are output-only (GP_OBUF only, no GP_IBUF placement).

## GreenPAK6 full flow (synth -> route -> NVM image -> verify)

    cd flow
    cat examples/dm8200.v > design.v      # any combinational top
    cat >> design.v << 'EOF'
    module top(input [3:0] a, b, input gt_in, lt_in, eq_in, output gt, lt, eq);
        dm8200 u(.a(a), .b(b), .gt_in(gt_in), .lt_in(lt_in), .eq_in(eq_in),
                 .gt(gt), .lt(lt), .eq(eq));
    endmodule
    EOF
    yosys -s greenpak_synth_gp6.ys   # abc -lut 3: GP6 has no standalone LUT4s
    $NEXTPNR --uarch greenpak --vopt chipdb=../chipdb/slg46826.json \
        --json out.json --freq 1 --vopt rpt=routed.json
    python3 ../bitstream/assemble.py --chipdb ../chipdb/slg46826.json \
        --routed routed.json --out top
    python3 ../bitstream/verify.py --chipdb ../chipdb/slg46826.json \
        --routed routed.json --json out.json --image top.bin

The assembler writes a 256-byte NVM image using the datasheet-exact
encoding: matrix output n's 6-bit connection field at bits [6n, 6n+5]
contains the Matrix Input Number of its driving source (datasheet
Tables 21/22), LUT truth tables at the Table 59 bit ranges, pad
output-enable fields set to VDD for driven pins. Outputs: top.bin
(raw bytes), top.hex (Intel HEX), top.nvm (bit string), top.nvm.txt
(Go Configure Software Hub import format - identical structure to the
app's own NVM export files).

verify.py decodes the image back into a netlist (through the same
datasheet tables) and simulates it against the yosys netlist for every
input combination - the encoding is validated in both directions (the
app-compiled ground truth validates decode, see analysis/
simulate_nvm.py; this validates encode). The verifier is honest: it
detects corrupted truth tables and matrix fields (tested by bit
flips), and only checks the logic the design actually uses.

Current limitations (honest status):

- Combinational logic only: DFF/latch mode of the LUT macrocells (DFF
  registers, mode selects 1232+/1236+, initial polarity, Q/nQ select)
  is not encoded yet, so sequential designs do not produce
  hardware-correct images.
- MF-block LUT truth ranges and sources are datasheet-derived but NOT
  ground-truth validated (the reference .gp6 design does not use MF
  LUTs); a Go Configure design using MF LUTs would close that gap.
- IO pin mode registers (777+: input Schmitt/low-voltage, output
  push-pull/open-drain) are not written; pads use the default-NVM
  settings.
- abc does not exploit cross-output structure as well as Go Configure's
  mapper (the app fits the plain 4-bit comparator in 11 cells); large
  designs may need manual structuring.
- IO pin mode registers (input Schmitt/low-voltage, output push-pull/
  open-drain, 777+: per-pin config) are not written yet; pads use the
  default-NVM settings.
