module top(input clk, input a, input b, output y1, output y2);
  reg [3:0] cnt = 0;
  always @(posedge clk) cnt <= cnt + 1;
  assign y1 = a & ~b;
  assign y2 = cnt[3] ^ a;
endmodule
