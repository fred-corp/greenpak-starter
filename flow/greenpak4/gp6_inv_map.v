// GreenPAK6: the fabric has no dedicated inverter macrocells (the
// GP4-family GP_INV primitive does not exist there) - fold inverters
// into 2-input LUTs with IN1 tied low.
//
// Load with:  techmap -map greenpak4/gp6_inv_map.v
module GP_INV(IN, OUT);
    input IN;
    output OUT;
    GP_2LUT #(.INIT(4'b0001)) _TECHMAP_REPLACE_ (.OUT(OUT),
        .IN0(IN), .IN1(1'b0));
endmodule
