// xdma_top: XDMA multi-channel scatter-gather DMA engine (instantiation and
// wiring only).
//
// Channels 0..NUM_MM2S-1 are MM2S (memory to stream), channels
// NUM_MM2S..NUM_CH-1 are S2MM (stream to memory). Each channel has a
// controller (xdma_desc_fetch) and a data path (xdma_rd_chan / xdma_wr_chan).
// Shared: AR and AW arbiters, W mux, TLB, page-table walker, CSRs.
//
// AXI ID map (spec 9.1, generalized to NUM_CH):
//   c              data bursts of channel c
//   NUM_CH + c     descriptor fetch (AR) and status writeback (AW) of c
//   2 * NUM_CH     page-table walker (AR)
// R and B responses are routed by ID. RREADY and BREADY are always high
// after reset: every R beat has reserved space (reserve-on-issue) and every
// B has a waiting consumer.
// Every AXI / AXI-Stream VALID output comes from a skid buffer register.
module xdma_top
  import xdma_pkg::*;
#(
  parameter int NUM_MM2S    = 2,
  parameter int NUM_S2MM    = 2,
  parameter int ADDR_W      = 32,
  parameter int DATA_W      = 64,
  parameter int ID_W        = 4,
  parameter int MAX_BURST   = 16,
  parameter int FIFO_DEPTH  = 64,
  parameter int MAX_OUT_CH  = 8,
  parameter int MAX_OUT_RD  = 16,
  parameter int MAX_OUT_WR  = 8,
  parameter int TLB_ENTRIES = 8,
  parameter int PREFETCH_EN = 1,
  localparam int NUM_CH = NUM_MM2S + NUM_S2MM
) (
  input  logic                         clk,
  input  logic                         rst_n,
  // AXI4-Lite CSR slave
  input  logic                         s_axil_awvalid,
  output logic                         s_axil_awready,
  input  logic [11:0]                  s_axil_awaddr,
  input  logic                         s_axil_wvalid,
  output logic                         s_axil_wready,
  input  logic [31:0]                  s_axil_wdata,
  input  logic [3:0]                   s_axil_wstrb,
  output logic                         s_axil_bvalid,
  input  logic                         s_axil_bready,
  output logic [1:0]                   s_axil_bresp,
  input  logic                         s_axil_arvalid,
  output logic                         s_axil_arready,
  input  logic [11:0]                  s_axil_araddr,
  output logic                         s_axil_rvalid,
  input  logic                         s_axil_rready,
  output logic [31:0]                  s_axil_rdata,
  output logic [1:0]                   s_axil_rresp,
  // AXI4 memory master
  output logic                         m_axi_awvalid,
  input  logic                         m_axi_awready,
  output logic [ID_W-1:0]              m_axi_awid,
  output logic [ADDR_W-1:0]            m_axi_awaddr,
  output logic [7:0]                   m_axi_awlen,
  output logic [2:0]                   m_axi_awsize,
  output logic [1:0]                   m_axi_awburst,
  output logic [3:0]                   m_axi_awcache,
  output logic [2:0]                   m_axi_awprot,
  output logic                         m_axi_awlock,
  output logic [3:0]                   m_axi_awqos,
  output logic                         m_axi_wvalid,
  input  logic                         m_axi_wready,
  output logic [DATA_W-1:0]            m_axi_wdata,
  output logic [DATA_W/8-1:0]          m_axi_wstrb,
  output logic                         m_axi_wlast,
  input  logic                         m_axi_bvalid,
  output logic                         m_axi_bready,
  input  logic [ID_W-1:0]              m_axi_bid,
  input  logic [1:0]                   m_axi_bresp,
  output logic                         m_axi_arvalid,
  input  logic                         m_axi_arready,
  output logic [ID_W-1:0]              m_axi_arid,
  output logic [ADDR_W-1:0]            m_axi_araddr,
  output logic [7:0]                   m_axi_arlen,
  output logic [2:0]                   m_axi_arsize,
  output logic [1:0]                   m_axi_arburst,
  output logic [3:0]                   m_axi_arcache,
  output logic [2:0]                   m_axi_arprot,
  output logic                         m_axi_arlock,
  output logic [3:0]                   m_axi_arqos,
  input  logic                         m_axi_rvalid,
  output logic                         m_axi_rready,
  input  logic [ID_W-1:0]              m_axi_rid,
  input  logic [DATA_W-1:0]            m_axi_rdata,
  input  logic [1:0]                   m_axi_rresp,
  input  logic                         m_axi_rlast,
  // MM2S streams
  output logic [NUM_MM2S-1:0]          m_axis_tvalid,
  input  logic [NUM_MM2S-1:0]          m_axis_tready,
  output logic [NUM_MM2S-1:0][63:0]    m_axis_tdata,
  output logic [NUM_MM2S-1:0][7:0]     m_axis_tkeep,
  output logic [NUM_MM2S-1:0]          m_axis_tlast,
  // S2MM streams
  input  logic [NUM_S2MM-1:0]          s_axis_tvalid,
  output logic [NUM_S2MM-1:0]          s_axis_tready,
  input  logic [NUM_S2MM-1:0][63:0]    s_axis_tdata,
  input  logic [NUM_S2MM-1:0][7:0]     s_axis_tkeep,
  input  logic [NUM_S2MM-1:0]          s_axis_tlast,
  // interrupt
  output logic                         irq
);

  localparam int CHW = (NUM_CH > 1) ? $clog2(NUM_CH) : 1;
  localparam int ROW = $clog2(MAX_OUT_RD + 1);
  localparam int WOW = $clog2(MAX_OUT_WR + 1);

  // ------------------------------------------------- fixed AXI attributes
  assign m_axi_awsize  = AXI_SIZE_8B;
  assign m_axi_awburst = AXI_INCR;
  assign m_axi_awcache = AXI_CACHE;
  assign m_axi_awprot  = 3'b000;
  assign m_axi_awlock  = 1'b0;
  assign m_axi_awqos   = 4'b0000;
  assign m_axi_arsize  = AXI_SIZE_8B;
  assign m_axi_arburst = AXI_INCR;
  assign m_axi_arcache = AXI_CACHE;
  assign m_axi_arprot  = 3'b000;
  assign m_axi_arlock  = 1'b0;
  assign m_axi_arqos   = 4'b0000;
  assign m_axis_tkeep  = '1;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      m_axi_rready <= 1'b0;
      m_axi_bready <= 1'b0;
    end else begin
      m_axi_rready <= 1'b1;
      m_axi_bready <= 1'b1;
    end
  end

  wire r_hs = m_axi_rvalid && m_axi_rready;
  wire b_hs = m_axi_bvalid && m_axi_bready;

  // ------------------------------------------------------------------- CSR
  logic              translate_en, tlb_flush, tlb_hit_inc, tlb_miss_inc;
  logic [19:0]       pt_root;
  logic [NUM_CH-1:0] ch_start, ch_abort, ch_busy;
  logic [31:0]       ch_desc_head [NUM_CH];
  logic [2:0]        ch_err       [NUM_CH];
  logic [31:0]       ch_cur_desc  [NUM_CH];
  logic [31:0]       ch_err_addr  [NUM_CH];
  logic [11:0]       ev_bytes     [NUM_CH];
  logic [NUM_CH-1:0] ev_stall, ev_desc_done, ev_done_irq, ev_err_irq;

  xdma_csr #(
    .NUM_MM2S(NUM_MM2S), .NUM_S2MM(NUM_S2MM), .MAX_BURST(MAX_BURST),
    .TLB_ENTRIES(TLB_ENTRIES), .DATA_W(DATA_W)
  ) u_csr (
    .clk, .rst_n,
    .s_axil_awvalid, .s_axil_awready, .s_axil_awaddr,
    .s_axil_wvalid, .s_axil_wready, .s_axil_wdata, .s_axil_wstrb,
    .s_axil_bvalid, .s_axil_bready, .s_axil_bresp,
    .s_axil_arvalid, .s_axil_arready, .s_axil_araddr,
    .s_axil_rvalid, .s_axil_rready, .s_axil_rdata, .s_axil_rresp,
    .translate_en, .pt_root, .tlb_flush, .tlb_hit_inc, .tlb_miss_inc, .irq,
    .ch_start, .ch_abort, .ch_desc_head,
    .ch_busy, .ch_err, .ch_cur_desc, .ch_err_addr,
    .ev_bytes, .ev_stall, .ev_desc_done, .ev_done_irq, .ev_err_irq
  );

  // ------------------------------------------------- shared request wires
  logic [NUM_CH-1:0]   desc_req, desc_gnt, wb_req, wb_gnt;
  logic [31:0]         desc_addr [NUM_CH];
  logic [31:0]         wb_addr   [NUM_CH];
  logic [63:0]         wb_data   [NUM_CH];

  logic [NUM_MM2S-1:0] rd_req, rd_gnt;
  logic [31:0]         rd_addr [NUM_MM2S];
  logic [7:0]          rd_len  [NUM_MM2S];
  logic [NUM_S2MM-1:0] wr_req, wr_gnt, w_pop;
  logic [31:0]         wr_addr [NUM_S2MM];
  logic [7:0]          wr_len  [NUM_S2MM];
  logic [63:0]         w_src   [NUM_S2MM];

  // TLB side
  logic [NUM_CH-1:0]   tlb_req, tlb_gnt, tlb_resp_valid;
  logic [19:0]         tlb_vpn [NUM_CH];
  xl_kind_e            tlb_resp_kind;
  logic [19:0]         tlb_resp_ppn;
  logic                walk_busy, walk_start, walk_write, walk_stale;
  logic [19:0]         walk_vpn;
  logic [CHW-1:0]      walk_ch;
  logic                ptw_done, ptw_ok, ptw_r, ptw_w;
  logic [CHW-1:0]      ptw_ch;
  logic [19:0]         ptw_vpn, ptw_ppn;
  logic                ptw_ar_req, ptw_ar_gnt;
  logic [31:0]         ptw_ar_addr;

  // ------------------------------------------------------------ channels
  for (genvar c = 0; c < NUM_CH; c++) begin : g_ch
    desc_t       dp_desc;
    logic        dp_start, dp_run, dp_drain, dp_flush, dp_done, dp_err, dp_idle;
    logic [2:0]  dp_err_code;
    logic [31:0] dp_err_addr, dp_bytes;

    wire r_desc = r_hs && (int'(m_axi_rid) == NUM_CH + c);
    wire b_desc = b_hs && (int'(m_axi_bid) == NUM_CH + c);

    xdma_desc_fetch #(.PREFETCH_EN(PREFETCH_EN)) u_ctl (
      .clk, .rst_n,
      .start(ch_start[c]), .abort(ch_abort[c]), .desc_head(ch_desc_head[c]),
      .busy(ch_busy[c]), .err(ch_err[c]), .err_addr(ch_err_addr[c]), .cur_desc(ch_cur_desc[c]),
      .ev_desc_done(ev_desc_done[c]), .ev_done_irq(ev_done_irq[c]), .ev_err_irq(ev_err_irq[c]),
      .ar_req(desc_req[c]), .ar_addr(desc_addr[c]), .ar_gnt(desc_gnt[c]),
      .r_valid(r_desc), .r_data(m_axi_rdata), .r_resp(m_axi_rresp), .r_last(m_axi_rlast),
      .aw_req(wb_req[c]), .aw_addr(wb_addr[c]), .aw_gnt(wb_gnt[c]), .wb_data(wb_data[c]),
      .b_valid(b_desc), .b_resp(m_axi_bresp),
      .dp_start, .dp_desc, .dp_run, .dp_drain, .dp_flush, .dp_done, .dp_err,
      .dp_err_code, .dp_err_addr, .dp_idle, .dp_bytes
    );

    if (c < NUM_MM2S) begin : g_mm2s
      wire r_data_c = r_hs && (int'(m_axi_rid) == c);
      xdma_rd_chan #(.MAX_BURST(MAX_BURST), .FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT_CH(MAX_OUT_CH)) u_rd (
        .clk, .rst_n, .translate_en,
        .dp_start, .dp_desc, .dp_run, .dp_drain, .dp_flush, .dp_done, .dp_err,
        .dp_err_code, .dp_err_addr, .dp_idle, .dp_bytes,
        .tlb_req(tlb_req[c]), .tlb_vpn(tlb_vpn[c]), .tlb_gnt(tlb_gnt[c]),
        .tlb_resp_valid(tlb_resp_valid[c]), .tlb_resp_kind, .tlb_resp_ppn,
        .walk_resp_valid(ptw_done && int'(ptw_ch) == c), .walk_resp_ok(ptw_ok),
        .walk_resp_stale(walk_stale),
        .walk_resp_ppn(ptw_ppn), .walk_busy,
        .ar_req(rd_req[c]), .ar_addr(rd_addr[c]), .ar_len(rd_len[c]), .ar_gnt(rd_gnt[c]),
        .r_valid(r_data_c), .r_data(m_axi_rdata), .r_resp(m_axi_rresp), .r_last(m_axi_rlast),
        .m_axis_tvalid(m_axis_tvalid[c]), .m_axis_tready(m_axis_tready[c]),
        .m_axis_tdata(m_axis_tdata[c]), .m_axis_tlast(m_axis_tlast[c]),
        .ev_bytes(ev_bytes[c]), .ev_stall(ev_stall[c])
      );
    end else begin : g_s2mm
      localparam int S = c - NUM_MM2S;
      wire b_data_c = b_hs && (int'(m_axi_bid) == c);
      xdma_wr_chan #(.MAX_BURST(MAX_BURST), .FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT_CH(MAX_OUT_CH)) u_wr (
        .clk, .rst_n, .translate_en,
        .dp_start, .dp_desc, .dp_run, .dp_drain, .dp_flush, .dp_done, .dp_err,
        .dp_err_code, .dp_err_addr, .dp_idle, .dp_bytes, .cur_desc(ch_cur_desc[c]),
        .tlb_req(tlb_req[c]), .tlb_vpn(tlb_vpn[c]), .tlb_gnt(tlb_gnt[c]),
        .tlb_resp_valid(tlb_resp_valid[c]), .tlb_resp_kind, .tlb_resp_ppn,
        .walk_resp_valid(ptw_done && int'(ptw_ch) == c), .walk_resp_ok(ptw_ok),
        .walk_resp_stale(walk_stale),
        .walk_resp_ppn(ptw_ppn), .walk_busy,
        .aw_req(wr_req[S]), .aw_addr(wr_addr[S]), .aw_len(wr_len[S]), .aw_gnt(wr_gnt[S]),
        .w_data(w_src[S]), .w_pop(w_pop[S]),
        .b_valid(b_data_c), .b_resp(m_axi_bresp),
        .s_axis_tvalid(s_axis_tvalid[S]), .s_axis_tready(s_axis_tready[S]),
        .s_axis_tdata(s_axis_tdata[S]), .s_axis_tkeep(s_axis_tkeep[S]),
        .s_axis_tlast(s_axis_tlast[S]),
        .ev_bytes(ev_bytes[c]), .ev_stall(ev_stall[c])
      );
    end
  end

  // ------------------------------------------------------------ arbiters
  logic [ROW-1:0] rd_out;
  logic [WOW-1:0] wr_out;
  logic           aw_acc;
  logic [ID_W-1:0] aw_acc_id;
  logic [7:0]     aw_acc_len;

  xdma_ar_arb #(.NUM_MM2S(NUM_MM2S), .NUM_CH(NUM_CH), .ID_W(ID_W), .MAX_OUT_RD(MAX_OUT_RD)) u_ar_arb (
    .clk, .rst_n,
    .walk_req(ptw_ar_req), .walk_addr(ptw_ar_addr), .walk_gnt(ptw_ar_gnt),
    .desc_req, .desc_addr, .desc_gnt,
    .data_req(rd_req), .data_addr(rd_addr), .data_len(rd_len), .data_gnt(rd_gnt),
    .r_last_hs(r_hs && m_axi_rlast), .rd_out,
    .m_axi_arvalid, .m_axi_arready, .m_axi_arid, .m_axi_araddr, .m_axi_arlen
  );

  xdma_aw_arb #(.NUM_MM2S(NUM_MM2S), .NUM_S2MM(NUM_S2MM), .ID_W(ID_W), .MAX_OUT_WR(MAX_OUT_WR)) u_aw_arb (
    .clk, .rst_n,
    .wb_req, .wb_addr, .wb_gnt,
    .data_req(wr_req), .data_addr(wr_addr), .data_len(wr_len), .data_gnt(wr_gnt),
    .b_hs, .wr_out, .acc(aw_acc), .acc_id(aw_acc_id), .acc_len(aw_acc_len),
    .m_axi_awvalid, .m_axi_awready, .m_axi_awid, .m_axi_awaddr, .m_axi_awlen
  );

  xdma_w_mux #(.NUM_MM2S(NUM_MM2S), .NUM_S2MM(NUM_S2MM), .ID_W(ID_W), .MAX_OUT_WR(MAX_OUT_WR)) u_w_mux (
    .clk, .rst_n,
    .aw_push(aw_acc), .aw_id(aw_acc_id), .aw_len(aw_acc_len),
    .s2mm_data(w_src), .s2mm_pop(w_pop), .wb_data,
    .m_axi_wvalid, .m_axi_wready, .m_axi_wdata, .m_axi_wstrb, .m_axi_wlast
  );

  // ---------------------------------------------------------- translation
  xdma_tlb #(.NUM_MM2S(NUM_MM2S), .NUM_CH(NUM_CH), .ENTRIES(TLB_ENTRIES)) u_tlb (
    .clk, .rst_n, .flush(tlb_flush),
    .req(tlb_req), .vpn(tlb_vpn), .gnt(tlb_gnt),
    .resp_valid(tlb_resp_valid), .resp_kind(tlb_resp_kind), .resp_ppn(tlb_resp_ppn),
    .walk_busy, .walk_start, .walk_vpn, .walk_ch, .walk_write,
    .fill_valid(ptw_done && ptw_ok), .fill_vpn(ptw_vpn), .fill_ppn(ptw_ppn),
    .fill_r(ptw_r), .fill_w(ptw_w), .walk_stale,
    .hit_inc(tlb_hit_inc), .miss_inc(tlb_miss_inc)
  );

  xdma_ptw #(.NUM_CH(NUM_CH)) u_ptw (
    .clk, .rst_n, .pt_root,
    .start(walk_start), .start_vpn(walk_vpn), .start_ch(walk_ch), .start_write(walk_write),
    .busy(walk_busy),
    .ar_req(ptw_ar_req), .ar_addr(ptw_ar_addr), .ar_gnt(ptw_ar_gnt),
    .r_valid(r_hs && int'(m_axi_rid) == 2 * NUM_CH), .r_data(m_axi_rdata), .r_resp(m_axi_rresp),
    .done(ptw_done), .done_ch(ptw_ch), .done_ok(ptw_ok), .done_vpn(ptw_vpn),
    .done_ppn(ptw_ppn), .done_r(ptw_r), .done_w(ptw_w)
  );

endmodule
