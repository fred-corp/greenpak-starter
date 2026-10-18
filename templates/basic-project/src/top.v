// Project template: rename/add sources and list them in the Makefile
// (VSRC). The top module must be named $(TOP) (default: top).
module top (
    input  clk,
    input  a, b,
    output y
);
    assign y = a & b;
endmodule
