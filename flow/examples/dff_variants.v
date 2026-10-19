// DFF variant exercise: inverted-output (GP_DFFI - the hardware
// Q/nQ select bit), latch (GP_DLATCH - the hardware latch bit) and
// async-reset (GP_DFFR) cells, instantiated directly.
module top(input clk, input rst_n, input d1, d2, d3, output q1, q2, q3);
    GP_DFFI  #(.INIT(1'b0)) ff_i  (.D(d1), .CLK(clk),  .nQ(q1));
    GP_DLATCH #(.INIT(1'b0)) la_i (.D(d2), .nCLK(clk), .Q(q2));
    GP_DFFR  #(.INIT(1'b0)) ff_r  (.D(d3), .CLK(clk), .nRST(rst_n), .Q(q3));
endmodule
