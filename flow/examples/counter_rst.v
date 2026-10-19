// Sequential example with async reset: exercises GP_DFFR cells,
// the nRST matrix routing and the nRST/nSET control bit encoding.
module top(input clk, input rst_n, input a, output y);
    reg [3:0] cnt = 0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) cnt <= 0;
        else cnt <= cnt + 1;
    assign y = cnt[3] ^ a;
endmodule
