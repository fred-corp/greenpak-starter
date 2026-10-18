// Sequential example: 4-bit counter + combinational outputs.
// Exercises the DFF mode of the LUT macrocells (mode selects 1232+,
// DFF control bits, initial polarity) end-to-end.
module top(input clk, input a, b, output y1, y2);
    reg [3:0] cnt = 0;
    always @(posedge clk) cnt <= cnt + 1;
    assign y1 = a & ~b;
    assign y2 = cnt[3] ^ a;
endmodule
