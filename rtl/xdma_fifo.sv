// xdma_fifo: synchronous flop-based FIFO with occupancy count.
//
// Interface: push/din write when !full; pop reads when !empty. dout shows the
// head entry combinationally (first-word fall-through). clr empties the FIFO
// synchronously (used to discard data when a channel drains).
// Push and pop may happen in the same cycle, including when full (the pop
// makes room only on the next cycle, so push-while-full is still illegal).
// Invariants: count == entries held; full == (count == DEPTH);
// empty == (count == 0); entries leave in the order they entered.
// Storage has no reset (datapath); pointers and count are reset.
module xdma_fifo #(
  parameter int WIDTH = 64,
  parameter int DEPTH = 64,
  localparam int AW = (DEPTH > 1) ? $clog2(DEPTH) : 1,
  localparam int CW = $clog2(DEPTH + 1)
) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             clr,
  input  logic             push,
  input  logic [WIDTH-1:0] din,
  input  logic             pop,
  output logic [WIDTH-1:0] dout,
  output logic             full,
  output logic             empty,
  output logic [CW-1:0]    count
);

  logic [WIDTH-1:0] mem [DEPTH];
  logic [AW-1:0]    wr_ptr, rd_ptr;

  wire do_push = push && !full;
  wire do_pop  = pop && !empty;

  function automatic logic [AW-1:0] inc(logic [AW-1:0] p);
    return (p == AW'(DEPTH - 1)) ? '0 : p + 1'b1;
  endfunction

  always_ff @(posedge clk) begin
    if (!rst_n || clr) begin
      wr_ptr <= '0;
      rd_ptr <= '0;
      count  <= '0;
    end else begin
      if (do_push) wr_ptr <= inc(wr_ptr);
      if (do_pop)  rd_ptr <= inc(rd_ptr);
      case ({do_push, do_pop})
        2'b10:   count <= count + 1'b1;
        2'b01:   count <= count - 1'b1;
        default: count <= count;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (do_push) mem[wr_ptr] <= din;
  end

  assign dout  = mem[rd_ptr];
  assign full  = (count == CW'(DEPTH));
  assign empty = (count == '0);

endmodule
