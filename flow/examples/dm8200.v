// 4-bit cascadable magnitude comparator (dm8200-style), the user's test design.
module dm8200 (
    input  [3:0] a, b,
    input        gt_in, lt_in, eq_in,   // cascade inputs
    output       gt, lt, eq             // a>b, a<b, a=b
);
    assign eq = &(a ~^ b) & eq_in;
    assign gt = (a > b) | (eq_in & (a == b) & gt_in);
    assign lt = (a < b) | (eq_in & (a == b) & lt_in);
endmodule
