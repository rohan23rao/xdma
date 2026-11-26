// Assertions for xdma_addr_gen.
//
// A small byte-based reference walk (ref_*) follows the descriptor
// independently of the design's beat-based counters and checks every burst
// against it: contiguous within a line, no gaps or overlaps, correct line
// starts (running STRIDE add), correct last flag and total beat count.
`include "xdma_sva_defs.svh"
module xdma_addr_gen_sva #(
  parameter int MAX_BURST = 16,
  parameter int LB_W = 16,
  parameter int LN_W = 16,
  localparam int BW  = $clog2(MAX_BURST + 1),
  localparam int LBB = LB_W - 3
) (
  input logic            clk,
  input logic            rst_n,
  input logic            start,
  input logic [31:0]     addr,
  input logic [LB_W-1:0] line_bytes,
  input logic [LN_W-1:0] lines,
  input logic [31:0]     stride,
  input logic            abort,
  input logic            busy,
  input logic            out_valid,
  input logic            out_ready,
  input logic [31:0]     out_va,
  input logic [BW-1:0]   out_beats,
  input logic            out_last,
  input logic [31:0]     line_addr,
  input logic [LBB-1:0]  off,
  input logic [LN_W-1:0] lines_left,
  input logic [31:0]     stride_q,
  input logic [LBB-1:0]  line_beats
);
  wire load = start && !busy && !abort;
  wire hs   = out_valid && out_ready;

  // Descriptor fields are legal when loaded (section 6 validation happens
  // upstream in the fetcher).
  `XASM(m_ag_lb_aligned, !load || (line_bytes[2:0] == 3'b0 && line_bytes != '0))
  `XASM(m_ag_lines_nz,   !load || (lines != '0))
  `XASM(m_ag_addr_aligned, !load || (addr[2:0] == 3'b0 && stride[2:0] == 3'b0))

  // Reference walk, in bytes.
  logic [LB_W-1:0] ref_lb;
  logic [LN_W-1:0] ref_lines;
  logic [31:0]     ref_stride;
  logic [31:0]     ref_line_addr;
  logic [LB_W-1:0] ref_off;      // bytes emitted in the current line
  logic [LN_W-1:0] ref_line;     // index of the current line
  logic [LB_W+LN_W-1:0] ref_total; // beats emitted so far

  wire [LB_W:0] burst_bytes = (LB_W+1)'(out_beats) << 3;

  always_ff @(posedge clk) begin
    if (load) begin
      ref_lb        <= line_bytes;
      ref_lines     <= lines;
      ref_stride    <= stride;
      ref_line_addr <= addr;
      ref_off       <= '0;
      ref_line      <= '0;
      ref_total     <= '0;
    end else if (hs) begin
      ref_total <= ref_total + (LB_W+LN_W)'(out_beats);
      if ((LB_W+1)'(ref_off) + burst_bytes == (LB_W+1)'(ref_lb)) begin
        ref_off       <= '0;
        ref_line      <= ref_line + 1'b1;
        ref_line_addr <= ref_line_addr + ref_stride;
      end else begin
        ref_off <= ref_off + LB_W'(burst_bytes);
      end
    end
  end

  // Burst size and 4 KB rule.
  `XA(a_ag_beats_min, !out_valid || (out_beats >= BW'(1)))
  `XA(a_ag_beats_max, !out_valid || (out_beats <= BW'(MAX_BURST)))
  `XA(a_ag_no_4k_cross, !out_valid || ({1'b0, out_va[11:0]} + 13'(burst_bytes) <= 13'd4096))
  // Contiguous tiling of each line: the burst starts where the reference
  // says and never runs past the end of the line.
  `XA(a_ag_addr, !out_valid || (out_va == ref_line_addr + 32'(ref_off)))
  `XA(a_ag_in_line, !out_valid || ((LB_W+1)'(ref_off) + burst_bytes <= (LB_W+1)'(ref_lb)))
  // last_of_desc exactly on the final burst of the final line.
  `XA(a_ag_last, !out_valid || (out_last ==
      ((ref_line == ref_lines - 1'b1) && ((LB_W+1)'(ref_off) + burst_bytes == (LB_W+1)'(ref_lb)))))
  // Total beats = LINE_BYTES * LINES / 8 when the last burst is accepted.
  `XA(a_ag_total, !(hs && out_last) ||
      ((LB_W+LN_W)'(ref_total + (LB_W+LN_W)'(out_beats)) ==
       (LB_W+LN_W)'((LB_W+LN_W)'(ref_lb >> 3) * (LB_W+LN_W)'(ref_lines))))
  // No bursts once the descriptor is finished.
  `XA(a_ag_stop_after_last, !($past(rst_n) && $past(hs && out_last && !load)) || !out_valid)
  // Payload stable while stalled.
  `XA(a_ag_stable, !($past(rst_n) && $past(out_valid && !out_ready && !abort)) ||
      (out_valid && out_va == $past(out_va) && out_beats == $past(out_beats) && out_last == $past(out_last)))

  // Inductive helpers: design state matches the reference walk.
  `XA(a_ag_inv_line_addr, !busy || (line_addr == ref_line_addr))
  `XA(a_ag_inv_off, !busy || ((LB_W)'({off, 3'b000}) == ref_off))
  `XA(a_ag_inv_lines, !busy || (lines_left == ref_lines - ref_line))
  `XA(a_ag_inv_stride, !busy || (stride_q == ref_stride))
  `XA(a_ag_inv_addr_aligned, !busy || (line_addr[2:0] == 3'b0 && stride_q[2:0] == 3'b0))
  `XA(a_ag_inv_line_beats, !busy || ({line_beats, 3'b000} == ref_lb))
  `XA(a_ag_inv_line_idx, !busy || (ref_line < ref_lines))
  `XA(a_ag_inv_off_lt, !busy || (ref_off < ref_lb))
  `XA(a_ag_inv_total, !busy || (ref_total ==
      (LB_W+LN_W)'((LB_W+LN_W)'(ref_line) * (LB_W+LN_W)'(ref_lb >> 3)) + (LB_W+LN_W)'(ref_off >> 3)))
  `XA(a_ag_inv_aligned, !busy || (ref_off[2:0] == 3'b0 && ref_lb[2:0] == 3'b0 && ref_lb != '0))

  // Covers: a burst cut short by a 4 KB boundary, a negative stride line step.
  `XC(c_ag_4k_split, hs && (out_va[11:0] + 12'(burst_bytes) == 12'd0) &&
      ((LB_W+1)'(ref_off) + burst_bytes < (LB_W+1)'(ref_lb)) && (out_beats < BW'(MAX_BURST)))
  `XC(c_ag_neg_stride, hs && ref_stride[31] && (ref_line == 1) && (ref_off == '0))
  `XC(c_ag_multi_line_done, hs && out_last && (ref_lines > 2))
endmodule
