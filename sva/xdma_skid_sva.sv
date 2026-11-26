// Assertions for xdma_skid. Bound into every skid buffer instance.
`include "xdma_sva_defs.svh"
module xdma_skid_sva #(
  parameter int WIDTH = 64
) (
  input logic             clk,
  input logic             rst_n,
  input logic             clr,
  input logic             s_valid,
  input logic             s_ready,
  input logic [WIDTH-1:0] s_data,
  input logic             m_valid,
  input logic             m_ready,
  input logic [WIDTH-1:0] m_data,
  input logic             skid_valid
);
  // Output VALID and payload hold until accepted (AXI master rule).
  `XA(a_skid_valid_hold, !($past(rst_n) && $past(m_valid && !m_ready && !clr)) || m_valid)
  `XA(a_skid_data_hold,  !($past(rst_n) && $past(m_valid && !m_ready && !clr)) || (m_data == $past(m_data)))
  // The skid entry is only used while the output register is full.
  `XA(a_skid_skid_implies_out, !skid_valid || m_valid)
  `XC(c_skid_full, skid_valid && !s_ready)
  `XC(c_skid_stream, s_valid && s_ready && m_valid && m_ready)

`ifdef FORMAL
  // Conservation: beats in minus beats out equals occupancy, so nothing is
  // lost or duplicated. The tracked beat proves order and payload.
  wire s_hs = s_valid && s_ready;
  wire m_hs = m_valid && m_ready;
  wire [1:0] occ = 2'(m_valid) + 2'(skid_valid);
  (* anyseq *) logic pick;
  logic             trk;
  logic [1:0]       trk_pos;
  logic [WIDTH-1:0] trk_data;
  always_ff @(posedge clk) begin
    if (!rst_n || clr) begin
      trk <= 1'b0;
    end else if (!trk) begin
      if (s_hs && pick) begin
        trk      <= 1'b1;
        trk_data <= s_data;
        trk_pos  <= occ - 2'(m_hs);
      end
    end else if (m_hs) begin
      if (trk_pos == 2'd0) trk <= 1'b0;
      else                 trk_pos <= trk_pos - 1'b1;
    end
  end
  `XA(a_skid_trk_inside, !trk || (trk_pos < occ))
  `XA(a_skid_trk_slot, !trk || (trk_pos == 2'd0 ? m_data == trk_data : xdma_skid.skid_data == trk_data))
  `XA(a_skid_order, !(trk && trk_pos == 2'd0) || (m_valid && m_data == trk_data))
  // Occupancy rises only with an input handshake and falls only with an
  // output handshake: no beat is created, dropped or duplicated.
  `XA(a_skid_conserve, !$past(rst_n) || $past(clr) ||
      (int'(occ) == int'($past(occ)) + int'($past(s_hs)) - int'($past(m_hs))))
  `XC(c_skid_trk_out, trk && trk_pos == 2'd1 && m_hs)
`endif
endmodule
