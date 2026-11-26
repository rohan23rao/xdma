// Assertions for xdma_rd_chan (spec 7.1, 7.3, 7.4, 14.3).
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
