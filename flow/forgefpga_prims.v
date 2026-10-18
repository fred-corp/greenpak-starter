// ForgeFPGA macrocell primitives for the nextpnr --uarch forgefpga flow.
// Cell and port names mirror the BEL names in the chipdb JSON
// (chipdb/gen_chipdb.py) and the yosys greenpak4 cell library style.
module LUT4(input I0, input I1, input I2, input I3, output F);
    parameter [15:0] INIT = 16'h0000;
    assign F = INIT[{I3, I2, I1, I0}];
endmodule

module DFF(input CLK, input D, output Q);
    parameter [0:0] INIT = 1'b0;
    always @(posedge CLK)
        Q <= D;
endmodule

module INBUF(input PAD, output O);
    assign O = PAD;
endmodule

module OUTBUF(input I, output PAD);
    assign PAD = I;
endmodule

module IOBUF(input I, input EN, inout PAD);
    assign PAD = EN ? I : 1'bz;
endmodule
