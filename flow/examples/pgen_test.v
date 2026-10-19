// PGEN synthesis test: mirrors the LUT2-LUT3.gp6 ground truth
// (GUI pattern "01000001" = LSB-first = 16'h0082, bit range 7:0).
// GP_PGEN is instantiated directly (yosys does not infer it).
module top(input clk, input rst_n, input a, input b, output p, output y);
    GP_PGEN #(.PATTERN_DATA(16'h0082), .PATTERN_LEN(5'd8)) pgen_i (
        .CLK(clk), .nRST(rst_n), .OUT(pgen_out));
    GP_2LUT #(.INIT(4'b0110)) lut_i (.IN0(a), .IN1(b), .OUT(y));
    assign p = pgen_out;
    wire pgen_out;
endmodule
