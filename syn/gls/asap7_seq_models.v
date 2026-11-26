// Functional (zero-delay) models of the ASAP7 sequential cells that appear
// in the XDMA netlist, for gate-level simulation with Verilator. The vendor
// models use UDP tables, which Verilator does not support (verilator issue
// 5243); OpenROAD-flow-scripts ships equivalent replacements for the
// DFFHQN cells (syn/lib/asap7/verilog/dff.v), this file adds the rest.
// Combinational cells use the vendor models unchanged.
`timescale 1ns/10ps

module DFFHQNx1_ASAP7_75t_R (output reg QN, input D, input CLK);
  always @(posedge CLK) QN <= ~D;
endmodule
module DFFHQNx2_ASAP7_75t_R (output reg QN, input D, input CLK);
  always @(posedge CLK) QN <= ~D;
endmodule
module DFFHQNx3_ASAP7_75t_R (output reg QN, input D, input CLK);
  always @(posedge CLK) QN <= ~D;
endmodule
module DFFHQx4_ASAP7_75t_R (output reg Q, input D, input CLK);
  always @(posedge CLK) Q <= D;
endmodule

// Integrated clock gate, latch_posedge_precontrol: the enable is latched
// while CLK is low, GCLK = CLK & latched enable.
module ICGx1_ASAP7_75t_R (output GCLK, input ENA, input SE, input CLK);
  reg en_l;
  always @(CLK or ENA or SE) if (!CLK) en_l = ENA | SE;
  assign GCLK = CLK & en_l;
endmodule
