// Formal-only helper: forces reset in the first cycle so every proof starts
// from the reset state. Reset may be asserted again at any later cycle.
module xdma_fv_reset (input logic clk, input logic rst_n);
  logic init_done = 1'b0;
  always_ff @(posedge clk) init_done <= 1'b1;
  fv_reset_first: assume property (@(posedge clk) init_done || !rst_n);
endmodule
