// Techmap: internal yosys cells to forgefpga prims.
// $lut from `abc -lut 4 -dress` becomes a LUT4 with the truth table
// replicated across unused input combinations (canonical nextpnr-generic
// example flow pattern).

module \$lut (A, Y);
    parameter WIDTH = 0;
    parameter [63:0] LUT = 0;
    input [WIDTH-1:0] A;
    output Y;

    localparam rep = 1 << (4 - WIDTH);
    wire [3:0] padded = A; // zero-extends to 4 bits

    LUT4 #(.INIT({rep{LUT[15:0]}})) _TECHMAP_REPLACE_
        (.I0(padded[0]), .I1(padded[1]), .I2(padded[2]), .I3(padded[3]), .F(Y));
endmodule

module \$_DFF_P_ (input D, C, output Q);
    DFF _TECHMAP_REPLACE_ (.D(D), .Q(Q), .CLK(C));
endmodule
