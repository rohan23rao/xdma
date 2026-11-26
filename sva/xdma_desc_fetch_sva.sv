// Assertions for xdma_desc_fetch (spec 7.1, 7.3, 7.4, 14.3).
`include "xdma_sva_defs.svh"
// the data path is done (or idle, when draining).
module xdma_desc_fetch_sva (
  input logic       clk,
  input logic       rst_n,
  input logic       fetch_req,
  input logic       fetch_out,
  input logic       r_valid,
  input logic       b_valid,
  input logic       aw_req,
  input logic       dp_idle,
  input logic [3:0] state
);
  `XA(a_df_one_fetch, !(fetch_req && fetch_out))
  `XASM(a_df_r_expected, !r_valid || fetch_out)
  // writeback B only while waiting for one (states WB_WAIT = 4, EWB_WAIT = 7)
  `XASM(a_df_b_expected, !b_valid || state == 4'd4 || state == 4'd7)
  // writeback AW only when no data of this channel is outstanding
  `XA(a_df_wb_after_data, !aw_req || dp_idle)
  `XC(c_df_prefetch_hit, state == 4'd1 && $past(state) == 4'd4)
endmodule
