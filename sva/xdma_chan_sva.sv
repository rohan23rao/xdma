// Assertions on the per-channel data paths (spec 7.3, 7.4, 14.3).
`include "xdma_sva_defs.svh"

// MM2S: reserve-on-issue keeps space for every R beat in flight.
module xdma_rd_chan_sva #(
  parameter int FIFO_DEPTH = 64,
  parameter int MAX_OUT_CH = 8,
  localparam int CW = $clog2(FIFO_DEPTH + 1),
  localparam int OW = $clog2(MAX_OUT_CH + 1)
) (
  input logic          clk,
  input logic          rst_n,
  input logic [CW-1:0] fifo_count,
  input logic [CW-1:0] reserved,
  input logic [OW-1:0] outstanding,
  input logic          r_valid,
  input logic          r_last,
  input logic          ar_req,
  input logic          ar_gnt,
  input logic          dp_drain
);
  // Every beat in flight has a FIFO slot.
  `XA(a_rd_reserve_fits, int'(fifo_count) + int'(reserved) <= FIFO_DEPTH)
  // An R beat never finds the FIFO full: RREADY can stay high.
  `XA(a_rd_rready_no_full, !r_valid || int'(fifo_count) < FIFO_DEPTH)
  // R data only for bursts that were issued; counters never underflow.
  `XASM(a_rd_r_expected, !r_valid || (reserved != '0 && outstanding != '0))
  `XA(a_rd_out_max, int'(outstanding) <= MAX_OUT_CH)
  `XA(a_rd_no_issue_drain, !(ar_req && dp_drain))
  `XC(c_rd_fifo_full, int'(fifo_count) == FIFO_DEPTH)
  `XC(c_rd_out_max, int'(outstanding) == MAX_OUT_CH)
endmodule

// S2MM: AW-after-data; data of every issued AW is already in the FIFO.
module xdma_wr_chan_sva #(
  parameter int FIFO_DEPTH = 64,
  parameter int MAX_OUT_CH = 8,
  localparam int CW = $clog2(FIFO_DEPTH + 1),
  localparam int OW = $clog2(MAX_OUT_CH + 1)
) (
  input logic          clk,
  input logic          rst_n,
  input logic [CW-1:0] fifo_count,
  input logic [CW-1:0] committed,
  input logic [OW-1:0] outstanding,
  input logic          w_pop,
  input logic          b_valid,
  input logic          s_axis_tready,
  input logic          dp_run,
  input logic          dp_drain,
  input logic          aw_req
);
  `XA(a_wr_data_before_aw, fifo_count >= committed)
  `XA(a_wr_pop_committed, !w_pop || committed != '0)
  `XASM(a_wr_b_expected, !b_valid || outstanding != '0)
  `XA(a_wr_out_max, int'(outstanding) <= MAX_OUT_CH)
  `XA(a_wr_no_issue_drain, !(aw_req && dp_drain))
  // TREADY only while the channel was running and not draining.
  `XA(a_wr_tready_run, !(s_axis_tready && $past(rst_n)) || $past(dp_run && !dp_drain))
  `XC(c_wr_fifo_full, int'(fifo_count) == FIFO_DEPTH)
  `XC(c_wr_out_max, int'(outstanding) == MAX_OUT_CH)
endmodule

// Channel controller: one descriptor read at a time; writeback only after
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
