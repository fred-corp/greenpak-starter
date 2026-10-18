module top(input a, input b, input c, output y1, output y2);
  assign y1 = a & ~b;
  assign y2 = (a ^ b) | c;
endmodule
