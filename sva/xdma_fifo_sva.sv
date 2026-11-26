// Assertions for xdma_fifo. Bound into every FIFO instance.
`include "xdma_sva_defs.svh"
module xdma_fifo_sva #(
  parameter int WIDTH = 64,
  parameter int DEPTH = 64,
  localparam int AW = (DEPTH > 1) ? $clog2(DEPTH) : 1,
  localparam int CW = $clog2(DEPTH + 1)
) (
  input logic             clk,
  input logic             rst_n,
  input logic             clr,
  input logic             push,
  input logic [WIDTH-1:0] din,
  input logic             pop,
  input logic [WIDTH-1:0] dout,
  input logic             full,
  input logic             empty,
  input logic [CW-1:0]    count,
  input logic [AW-1:0]    wr_ptr,
  input logic [AW-1:0]    rd_ptr,
  input logic [WIDTH-1:0] mem [DEPTH]
);
  // Users must never push into a full FIFO or pop an empty one.
  `XASM(a_fifo_no_overflow,  !(push && full && !clr))
  `XASM(a_fifo_no_underflow, !(pop && empty && !clr))

  // count is bounded and the flags agree with it.
  `XA(a_fifo_count_le_depth, count <= CW'(DEPTH))
  `XA(a_fifo_full_flag,  full  == (count == CW'(DEPTH)))
  `XA(a_fifo_empty_flag, empty == (count == '0))
  // Write pointer sits count entries after the read pointer.
  `XA(a_fifo_ptr_gap, int'(wr_ptr) == (int'(rd_ptr) + int'(count)) % DEPTH)
  // count moves by exactly push - pop.
  `XA(a_fifo_count_step, !$past(rst_n) || $past(clr) ||
      (int'(count) == int'($past(count)) + int'($past(push && !full)) - int'($past(pop && !empty))))

  `XC(c_fifo_full, full)
  `XC(c_fifo_push_pop_full, full && push && pop)

`ifdef FORMAL
  // Tracked-entry check: pick any one pushed entry, follow how many entries
  // are ahead of it, and require it to reach the head unchanged.
  (* anyseq *) logic pick;
  logic             trk;
  logic [CW-1:0]    trk_pos;
  logic [WIDTH-1:0] trk_data;
  wire do_push = push && !full;
  wire do_pop  = pop && !empty;
  always_ff @(posedge clk) begin
    if (!rst_n || clr) begin
      trk <= 1'b0;
    end else if (!trk) begin
      if (do_push && pick) begin
        trk      <= 1'b1;
        trk_data <= din;
        trk_pos  <= count - CW'(do_pop);
      end
    end else if (do_pop) begin
      if (trk_pos == '0) trk <= 1'b0;
      else               trk_pos <= trk_pos - 1'b1;
    end
  end
  wire [AW-1:0] trk_slot = AW'((int'(rd_ptr) + int'(trk_pos)) % DEPTH);
  `XA(a_fifo_trk_inside, !trk || (trk_pos < count))
  `XA(a_fifo_trk_stored, !trk || (mem[trk_slot] == trk_data))
  `XA(a_fifo_order, !(trk && trk_pos == '0) || (dout == trk_data))
  `XC(c_fifo_trk_pop, trk && trk_pos == '0 && pop)
`endif
endmodule
