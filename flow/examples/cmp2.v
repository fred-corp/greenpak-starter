// 2-bit magnitude comparator - the small end-to-end example: fits the
// SLG46826's non-MF logic macrocells (4x LUT2 + 7x LUT3).
module top(input [1:0] a, b, output gt, lt, eq);
    assign eq = &(a ~^ b);
    assign gt = a > b;
    assign lt = a < b;
endmodule
