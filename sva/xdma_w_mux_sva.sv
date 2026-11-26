// Assertions for xdma_w_mux: W beats leave in AW order, exactly AWLEN+1 per
// burst, WLAST on the last one, never for an AW the arbiter has not
// accepted. A model of the AW lengths still owed W data is compared with
// the W output.
`include "xdma_sva_defs.svh"
module xdma_w_mux_sva #(
  parameter int ID_W       = 4,
  parameter int MAX_OUT_WR = 8
) (
  input logic            clk,
  input logic            rst_n,
  input logic            aw_push,
  input logic [ID_W-1:0] aw_id,
  input logic [7:0]      aw_len,
  input logic            m_axi_wvalid,
  input logic            m_axi_wready,
  input logic            m_axi_wlast,
  input logic            q_empty
);
  // The engine never has more than MAX_OUT_WR writes in flight.
  localparam int QD = MAX_OUT_WR + 2;
  logic [QD-1:0][7:0] q_len;   // packed: formal tools read it in properties
  logic [$clog2(QD+1)-1:0] q_cnt;
  logic [$clog2(QD)-1:0]   q_rd, q_wr;
  logic [7:0] w_cnt;
  wire w_hs  = m_axi_wvalid && m_axi_wready;
  wire w_end = w_hs && (w_cnt == q_len[q_rd]);
  `XASM(a_wm_env_limit, !aw_push || int'(q_cnt) < MAX_OUT_WR)

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      q_cnt <= '0; q_rd <= '0; q_wr <= '0; w_cnt <= '0;
    end else begin
      if (aw_push) begin
        q_len[q_wr] <= aw_len;
        q_wr <= (int'(q_wr) == QD - 1) ? '0 : q_wr + 1'b1;
      end
      if (w_hs) w_cnt <= w_end ? 8'd0 : w_cnt + 1'b1;
      if (w_end) q_rd <= (int'(q_rd) == QD - 1) ? '0 : q_rd + 1'b1;
      q_cnt <= q_cnt + (aw_push ? 1'b1 : 1'b0) - (w_end ? 1'b1 : 1'b0);
    end
  end
  `XA(a_wm_after_aw, !m_axi_wvalid || q_cnt != '0)
  `XA(a_wm_wlast, !m_axi_wvalid || q_cnt == '0 || (m_axi_wlast == (w_cnt == q_len[q_rd])))
  `XC(c_wm_two_bursts, w_end && q_cnt == 2)
  `XC(c_wm_long_burst, w_end && q_len[q_rd] == 8'd3)
endmodule
