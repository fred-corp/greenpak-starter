// MF-block counter exercise: GP_COUNT8_ADV instantiated directly.
// 11 LUT2s - one more than the 10 non-counter LUT slots - force one
// into a counter block (block mode = LUT -> counter, the LUT output
// feeding the counter input). NOTE: in a shared block the counter's
// EXT_CLK matrix input IS the LUT's IN1 - a shared-block counter
// must use the internal OSC clock (or tie both to the same net).
// The counters here use the 2M OSC1 default (the app's ground-truth
// default clock selection).
module top(input a, b, output c1, c2, c3, output z);
    GP_COUNT8_ADV #(.COUNT_TO(8'h0A)) cnt_i
        (.CLK(), .RST(), .OUT(c1), .UP(), .KEEP(), .POUT());
    GP_COUNT8_ADV #(.COUNT_TO(8'h03)) cnt_j
        (.CLK(), .RST(), .OUT(c2), .UP(), .KEEP(), .POUT());
    GP_COUNT8_ADV #(.COUNT_TO(8'h05)) cnt_k
        (.CLK(), .RST(), .OUT(c3), .UP(), .KEEP(), .POUT());
    wire [10:0] y;
    genvar g;
    generate
        for (g = 0; g < 11; g = g + 1) begin : luts
            GP_2LUT #(.INIT(4'b0110)) lut_g (.IN0(a), .IN1(b),
                                             .OUT(y[g]));
        end
    endgenerate
    assign z = ^y;
endmodule
