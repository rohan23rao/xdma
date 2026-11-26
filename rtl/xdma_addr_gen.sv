// xdma_addr_gen: splits one descriptor (1D or 2D) into AXI bursts.
//
// Implements spec section 7.2 in units of 8-byte beats:
//   beats = min(MAX_BURST, beats left in line, beats left in 4 KB page)
// The line start address advances by a running add of STRIDE (no
// multiplier). Output {va, beats, last} is a valid/ready stream; the payload
// is a function of registered state only, so it is stable while
// out_valid && !out_ready.
// Interfaces: start loads a descriptor (ignored while busy); abort stops
// generation immediately.
// Invariants: every burst has 1..MAX_BURST beats, never crosses 4 KB, bursts
// tile each line exactly, out_last is set on the final burst only.
module xdma_addr_gen #(
  parameter int MAX_BURST = 16,
  parameter int LB_W = 16,                 // LINE_BYTES width
  parameter int LN_W = 16,                 // LINES width
  localparam int BW  = $clog2(MAX_BURST + 1),
  localparam int LBB = LB_W - 3,           // line length in beats
  localparam int MW  = (LBB + 1 > 10) ? LBB + 1 : 10  // min() compare width
) (
  input  logic            clk,
  input  logic            rst_n,
  input  logic            start,
  input  logic [31:0]     addr,
  input  logic [LB_W-1:0] line_bytes,
  input  logic [LN_W-1:0] lines,
  input  logic [31:0]     stride,
  input  logic            abort,
  output logic            busy,
  output logic            out_valid,
  input  logic            out_ready,
  output logic [31:0]     out_va,
  output logic [BW-1:0]   out_beats,
  output logic            out_last
);

  logic [31:0]     line_addr;
  logic [31:0]     stride_q;
  logic [LBB-1:0]  line_beats;
  logic [LBB-1:0]  off;          // beats already emitted in this line
  logic [LN_W-1:0] lines_left;   // lines remaining, including current

  // Burst size: min of the three limits.
  logic [LBB-1:0] rem;
  logic [9:0]     to_4k;         // 1..512 beats to the next 4 KB boundary
  logic [MW-1:0]  lim;
  logic           last_in_line;

  always_comb begin
    out_va = line_addr + {{(32-LBB-3){1'b0}}, off, 3'b000};
    rem    = line_beats - off;
    to_4k  = 10'd512 - {1'b0, out_va[11:3]};
    // Compare at MW bits so neither rem (LBB bits) nor to_4k (up to 512)
    // is truncated.
    lim    = MW'(MAX_BURST);
    if (MW'(rem) < lim)       lim = MW'(rem);
    if (MW'(to_4k) < lim)     lim = MW'(to_4k);
    out_beats    = BW'(lim);
    last_in_line = (MW'(rem) == lim);
    out_last     = last_in_line && (lines_left == LN_W'(1));
  end

  assign out_valid = busy;
  wire hs = out_valid && out_ready;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      busy <= 1'b0;
    end else if (abort) begin
      busy <= 1'b0;
    end else if (start && !busy) begin
      busy <= 1'b1;
    end else if (hs && out_last) begin
      busy <= 1'b0;
    end
  end

  // Datapath registers: enable-coded, no reset.
  always_ff @(posedge clk) begin
    if (start && !busy) begin
      line_addr  <= addr;
      stride_q   <= stride;
      line_beats <= line_bytes[LB_W-1:3];
      off        <= '0;
      lines_left <= lines;
    end else if (hs) begin
      if (last_in_line) begin
        off        <= '0;
        line_addr  <= line_addr + stride_q;
        lines_left <= lines_left - 1'b1;
      end else begin
        off <= off + LBB'(lim);
      end
    end
  end

endmodule
