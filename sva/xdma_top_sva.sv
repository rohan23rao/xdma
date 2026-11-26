// Assertions on the xdma_top AXI4 master, AXI-Stream and CSR interfaces
// (spec 4, 7.2, 9, 14.3). Bound into xdma_top.
`include "xdma_sva_defs.svh"
module xdma_top_sva #(
  parameter int NUM_MM2S   = 2,
  parameter int NUM_S2MM   = 2,
  parameter int ID_W       = 4,
  parameter int MAX_BURST  = 16,
  parameter int FIFO_DEPTH = 64,
  parameter int MAX_OUT_RD = 16,
  parameter int MAX_OUT_WR = 8,
  localparam int NUM_CH = NUM_MM2S + NUM_S2MM,
  localparam int ROW = $clog2(MAX_OUT_RD + 1),
  localparam int WOW = $clog2(MAX_OUT_WR + 1)
) (
  input logic                      clk,
  input logic                      rst_n,
  input logic                      m_axi_awvalid, m_axi_awready,
  input logic [ID_W-1:0]           m_axi_awid,
  input logic [31:0]               m_axi_awaddr,
  input logic [7:0]                m_axi_awlen,
  input logic [2:0]                m_axi_awsize,
  input logic [1:0]                m_axi_awburst,
  input logic                      m_axi_wvalid, m_axi_wready, m_axi_wlast,
  input logic [63:0]               m_axi_wdata,
  input logic [7:0]                m_axi_wstrb,
  input logic                      m_axi_bvalid, m_axi_bready,
  input logic [ID_W-1:0]           m_axi_bid,
  input logic                      m_axi_arvalid, m_axi_arready,
  input logic [ID_W-1:0]           m_axi_arid,
  input logic [31:0]               m_axi_araddr,
  input logic [7:0]                m_axi_arlen,
  input logic [2:0]                m_axi_arsize,
  input logic [1:0]                m_axi_arburst,
  input logic                      m_axi_rvalid, m_axi_rready, m_axi_rlast,
  input logic [ID_W-1:0]           m_axi_rid,
  input logic [NUM_MM2S-1:0]       m_axis_tvalid, m_axis_tready, m_axis_tlast,
  input logic [NUM_MM2S-1:0][63:0] m_axis_tdata,
  input logic                      s_axil_bvalid, s_axil_bready, s_axil_rvalid, s_axil_rready,
  input logic [NUM_CH-1:0]         ch_busy,
  input logic [ROW-1:0]            rd_out,
  input logic [WOW-1:0]            wr_out,
  input logic                      walk_busy,
  input logic [NUM_CH-1:0]         tlb_resp_valid,
  input logic                      ptw_ar_req,
  input logic [NUM_CH-1:0]         desc_req,
  input logic [NUM_MM2S-1:0]       rd_req,
  input logic                      aw_acc,
  input logic [7:0]                aw_acc_len
);
`ifndef FORMAL
  // Elaboration checks on the parameters (spec 3, 9.1).
  initial begin
    if (2 * NUM_CH >= (1 << ID_W)) $fatal(1, "ID_W=%0d cannot hold walker ID %0d", ID_W, 2 * NUM_CH);
    if (MAX_BURST < 1 || MAX_BURST > 256) $fatal(1, "MAX_BURST=%0d out of range", MAX_BURST);
    if (FIFO_DEPTH < MAX_BURST) $fatal(1, "FIFO_DEPTH=%0d < MAX_BURST=%0d: no burst could ever issue",
                                       FIFO_DEPTH, MAX_BURST);
  end
`endif
  logic past_ok;   // reset was released at least one cycle ago
  always_ff @(posedge clk) past_ok <= rst_n;

  // ---- VALID held until READY, payload stable (AR, AW, W, streams)
  `XA(a_ar_hold, !(past_ok && $past(m_axi_arvalid && !m_axi_arready)) ||
      (m_axi_arvalid && $stable(m_axi_arid) && $stable(m_axi_araddr) && $stable(m_axi_arlen)))
  `XA(a_aw_hold, !(past_ok && $past(m_axi_awvalid && !m_axi_awready)) ||
      (m_axi_awvalid && $stable(m_axi_awid) && $stable(m_axi_awaddr) && $stable(m_axi_awlen)))
  `XA(a_w_hold, !(past_ok && $past(m_axi_wvalid && !m_axi_wready)) ||
      (m_axi_wvalid && $stable(m_axi_wdata) && $stable(m_axi_wlast) && $stable(m_axi_wstrb)))
  for (genvar i = 0; i < NUM_MM2S; i++) begin : g_axis
    `XA(a_axis_hold, !(past_ok && $past(m_axis_tvalid[i] && !m_axis_tready[i])) ||
        (m_axis_tvalid[i] && $stable(m_axis_tdata[i]) && $stable(m_axis_tlast[i])))
  end
  `XA(a_axil_b_hold, !(past_ok && $past(s_axil_bvalid && !s_axil_bready)) || s_axil_bvalid)
  `XA(a_axil_r_hold, !(past_ok && $past(s_axil_rvalid && !s_axil_rready)) || s_axil_rvalid)

  // ---- burst shape: INCR, 8-byte beats, <= MAX_BURST beats, no 4 KB crossing
  `XA(a_ar_shape, !m_axi_arvalid || (m_axi_arsize == 3'd3 && m_axi_arburst == 2'b01 &&
      m_axi_araddr[2:0] == 3'b0 && int'(m_axi_arlen) <= MAX_BURST - 1))
  `XA(a_aw_shape, !m_axi_awvalid || (m_axi_awsize == 3'd3 && m_axi_awburst == 2'b01 &&
      m_axi_awaddr[2:0] == 3'b0 && int'(m_axi_awlen) <= MAX_BURST - 1))
  `XA(a_ar_no_4k, !m_axi_arvalid || (int'(m_axi_araddr[11:0]) + 8 * (int'(m_axi_arlen) + 1) <= 4096))
  `XA(a_aw_no_4k, !m_axi_awvalid || (int'(m_axi_awaddr[11:0]) + 8 * (int'(m_axi_awlen) + 1) <= 4096))

  // ---- ID map (spec 9.1)
  `XA(a_arid_map, !m_axi_arvalid || (int'(m_axi_arid) < NUM_MM2S) ||
      (int'(m_axi_arid) >= NUM_CH && int'(m_axi_arid) <= 2 * NUM_CH))
  `XA(a_awid_map, !m_axi_awvalid || (int'(m_axi_awid) >= NUM_MM2S && int'(m_axi_awid) < 2 * NUM_CH))
  `XA(a_wstrb_full, !m_axi_wvalid || m_axi_wstrb == 8'hFF)

  // ---- outstanding limits; responses only for requests in flight
  `XA(a_rd_out_max, int'(rd_out) <= MAX_OUT_RD)
  `XA(a_wr_out_max, int'(wr_out) <= MAX_OUT_WR)
  `XASM(a_r_has_req, !(m_axi_rvalid && m_axi_rready && m_axi_rlast) || rd_out != '0)
  `XASM(a_b_has_req, !(m_axi_bvalid && m_axi_bready) || wr_out != '0)
  // RREADY / BREADY never drop after reset (reservation makes this possible)
  `XA(a_rready_high, !past_ok || m_axi_rready)
  `XA(a_bready_high, !past_ok || m_axi_bready)

  // ---- W beats per AW = AWLEN + 1, WLAST exactly on the last. The model
  // queues each AW when the arbiter accepts it (W may then lead the bus AW,
  // which AXI4 allows) and checks the W bus against it. AW bus handshakes
  // never run ahead of accepted requests.
  localparam int QD = MAX_OUT_WR + 2;
  logic [QD-1:0][7:0] q_len;   // packed: formal tools read it in properties
  localparam int QCW = $clog2(QD+1);
  logic [QCW-1:0] q_cnt;
  logic [$clog2(QD)-1:0]   q_rd, q_wr;
  logic [7:0] w_cnt;
  wire aw_hs = aw_acc;
  wire w_hs  = m_axi_wvalid && m_axi_wready;
  wire w_end = w_hs && (w_cnt == q_len[q_rd]);
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      q_cnt <= '0; q_rd <= '0; q_wr <= '0; w_cnt <= '0;
    end else begin
      if (aw_hs) begin
        q_len[q_wr] <= aw_acc_len;
        q_wr <= (int'(q_wr) == QD - 1) ? '0 : q_wr + 1'b1;
      end
      if (w_hs) w_cnt <= w_end ? 8'd0 : w_cnt + 1'b1;
      if (w_end) q_rd <= (int'(q_rd) == QD - 1) ? '0 : q_rd + 1'b1;
      q_cnt <= q_cnt + QCW'(aw_hs) - QCW'(w_end);
    end
  end
  `XA(a_w_after_aw, !m_axi_wvalid || q_cnt != '0)
  `XA(a_wlast_pos, !m_axi_wvalid || q_cnt == '0 || (m_axi_wlast == (w_cnt == q_len[q_rd])))

  // ---- covers
  `XC(c_all_channels_busy, &ch_busy)
  `XC(c_ar_4k_split, m_axi_arvalid && m_axi_arready && int'(m_axi_arid) < NUM_MM2S &&
      (int'(m_axi_araddr[11:0]) + 8 * (int'(m_axi_arlen) + 1) == 4096) && int'(m_axi_arlen) < MAX_BURST - 1)
  `XC(c_aw_4k_split, m_axi_awvalid && m_axi_awready && int'(m_axi_awid) < NUM_CH &&
      (int'(m_axi_awaddr[11:0]) + 8 * (int'(m_axi_awlen) + 1) == 4096) && int'(m_axi_awlen) < MAX_BURST - 1)
  `XC(c_rd_out_max, int'(rd_out) == MAX_OUT_RD)
  `XC(c_wr_out_max, int'(wr_out) == MAX_OUT_WR)
  `XC(c_walker_busy_while_hit, walk_busy && (tlb_resp_valid != '0))
  `XC(c_ar_conflict_walk_desc, ptw_ar_req && (desc_req != '0))
  `XC(c_ar_conflict_walk_data, ptw_ar_req && (rd_req != '0))
  `XC(c_ar_conflict_desc_data, (desc_req != '0) && (rd_req != '0))
  `XC(c_r_interleave, m_axi_rvalid && m_axi_rready && !m_axi_rlast && $past(m_axi_rvalid && m_axi_rready) &&
      $past(m_axi_rid) != m_axi_rid && !$past(m_axi_rlast))
endmodule
