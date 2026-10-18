-- VHDL template: requires ghdl with the yosys plugin (see docs/usage.md)
library ieee;
use ieee.std_logic_1164.all;

entity top is
    port (
        clk : in  std_logic;
        a   : in  std_logic;
        b   : in  std_logic;
        y   : out std_logic
    );
end entity top;

architecture rtl of top is
begin
    y <= a and b;
end architecture rtl;
