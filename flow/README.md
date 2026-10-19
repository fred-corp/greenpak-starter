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

Sequential designs work the same way (examples/counter.v): GP_DFF/
GP_DFFR/GP_DFFS cells place into the DFF mode of the LUT macrocells,
the assembler sets the mode select (Table 59: 1232+/1236+/1282/1339+)
plus the control bits (latch select, Q/nQ, nRST/nSET, initial
polarity), and ties unused reset inputs to VDD (they are active low -
the GND default would hold the macrocell in permanent reset). verify.py
simulates clocked cycles and compares against the yosys netlist.

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

- Sequential logic is encoded and round-trip verified (examples/
  counter.v with GP_DFF, examples/counter_rst.v with GP_DFFSR async
  reset: DFF mode selects, control bits, initial polarity, unused
  resets tied to VDD). The MF-block DFF mode is GROUND-TRUTH
  VALIDATED (SLG46826-MF-DFF-test.gp6): the "Single DFF" mode bit is
  exactly the app's 0x358 register (1339 for 8BIT_1 etc.), the DFF
  port mapping is CLK=IN0 / nSET-nRST=IN1 / D=IN2, and the control
  bits are [7:4] of the block's LUT truth byte - all as modeled.
  The main-macrocell DFF registers remain datasheet-derived.
- MF-block LUTs are GROUND-TRUTH VALIDATED (SLG46826-MF-DFF-test.gp6):
  the 16BIT_0 block's 4-bit LUT truth decodes at bits 1288-1303 and
  its inputs are matrix outputs OUT30-33. New knowledge: the block
  mode value (LSB-first over registers 1280,1281,1283,1284,1282,
  1285,1286) is 3 for "LUT -> CNT/DLY" (LUT feeding the counter);
  "Single 4-bit LUT" = 0, "Single DFF" = 16, "Single CNT/DLY" = 1.
- The MF counter/CNT-DLY macrocell (mode, clock source, counter
  data) is DECODED (analysis/simulate_nvm.py prints it) but not
  synthesizable from HDL yet.
- IO pin modes: the per-pin registers are extracted (device_regs/
  SLG46826_pinmodes.json) and output pads get their pull resistors
  cleared to floating - exactly what the ground-truth designs do.
  Output enables follow the ground truth per pad class: pads WITH a
  static output-enable register (IO0/IO2/IO3/IO6/IO7) set that
  register and leave the matrix OE field at default; pads WITHOUT
  one (IO1/IO4/IO5/IO8-IO14) set the matrix "Digital Output OE"
  field to VDD. IO6/IO7 have a static register but NO matrix OE
  field at all. Everything else uses default-NVM settings (digital
  input, push-pull 1x). Explicit pin-mode configuration (Schmitt,
  low-voltage, open-drain, pull strength) is not exposed to designs
  yet.
- DFF cell variants not exercised by a test design: nQ outputs
  (*I cells), latches (GP_DLATCH*). GP_DFFSR is exercised by
  counter_rst and assembled as nRST (rst_set bit = 0).
- abc does not exploit cross-output structure as well as Go Configure's
  mapper (the app fits the plain 4-bit comparator in 11 cells); large
  designs may need manual structuring.
