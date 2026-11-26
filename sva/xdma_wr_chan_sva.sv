// Assertions for xdma_wr_chan (spec 7.1, 7.3, 7.4, 14.3).
`include "xdma_sva_defs.svh"
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
