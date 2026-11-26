// xdma_tb: simulation wrapper around xdma_top for the cocotb testbench.
// Fixed at 2 MM2S + 2 S2MM channels; breaks the packed stream arrays out into
// one named port group per channel so cocotbext-axi can bind to them.
module xdma_tb #(
  parameter int ID_W = 4,
  parameter int MAX_BURST = 16,
  parameter int FIFO_DEPTH = 64,
  parameter int MAX_OUT_CH = 8,
  parameter int MAX_OUT_RD = 16,
  parameter int MAX_OUT_WR = 8,
  parameter int TLB_ENTRIES = 8,
  parameter int PREFETCH_EN = 1
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        s_axil_awvalid,
  output logic        s_axil_awready,
  input  logic [11:0] s_axil_awaddr,
  input  logic [2:0]  s_axil_awprot,
  input  logic        s_axil_wvalid,
  output logic        s_axil_wready,
  input  logic [31:0] s_axil_wdata,
  input  logic [3:0]  s_axil_wstrb,
  output logic        s_axil_bvalid,
  input  logic        s_axil_bready,
  output logic [1:0]  s_axil_bresp,
  input  logic        s_axil_arvalid,
  output logic        s_axil_arready,
  input  logic [11:0] s_axil_araddr,
  input  logic [2:0]  s_axil_arprot,
  output logic        s_axil_rvalid,
  input  logic        s_axil_rready,
  output logic [31:0] s_axil_rdata,
  output logic [1:0]  s_axil_rresp,
  output logic        m_axi_awvalid,
  input  logic        m_axi_awready,
  output logic [ID_W-1:0] m_axi_awid,
  output logic [31:0] m_axi_awaddr,
  output logic [7:0]  m_axi_awlen,
  output logic [2:0]  m_axi_awsize,
  output logic [1:0]  m_axi_awburst,
  output logic [3:0]  m_axi_awcache,
  output logic [2:0]  m_axi_awprot,
  output logic        m_axi_awlock,
  output logic [3:0]  m_axi_awqos,
  output logic        m_axi_wvalid,
  input  logic        m_axi_wready,
  output logic [63:0] m_axi_wdata,
  output logic [7:0]  m_axi_wstrb,
  output logic        m_axi_wlast,
  input  logic        m_axi_bvalid,
  output logic        m_axi_bready,
  input  logic [ID_W-1:0] m_axi_bid,
  input  logic [1:0]  m_axi_bresp,
  output logic        m_axi_arvalid,
  input  logic        m_axi_arready,
  output logic [ID_W-1:0] m_axi_arid,
  output logic [31:0] m_axi_araddr,
  output logic [7:0]  m_axi_arlen,
  output logic [2:0]  m_axi_arsize,
  output logic [1:0]  m_axi_arburst,
  output logic [3:0]  m_axi_arcache,
  output logic [2:0]  m_axi_arprot,
  output logic        m_axi_arlock,
  output logic [3:0]  m_axi_arqos,
  input  logic        m_axi_rvalid,
  output logic        m_axi_rready,
  input  logic [ID_W-1:0] m_axi_rid,
  input  logic [63:0] m_axi_rdata,
  input  logic [1:0]  m_axi_rresp,
  input  logic        m_axi_rlast,
  output logic        irq,
  input  logic        loop_en,   // 1: MM2S port 0 drives S2MM port 0 (memory copy)
  output logic        m_axis0_tvalid,
  input  logic        m_axis0_tready,
  output logic [63:0] m_axis0_tdata,
  output logic [7:0]  m_axis0_tkeep,
  output logic        m_axis0_tlast,
  output logic        m_axis1_tvalid,
  input  logic        m_axis1_tready,
  output logic [63:0] m_axis1_tdata,
  output logic [7:0]  m_axis1_tkeep,
  output logic        m_axis1_tlast,
  input  logic        s_axis0_tvalid,
  output logic        s_axis0_tready,
  input  logic [63:0] s_axis0_tdata,
  input  logic [7:0]  s_axis0_tkeep,
  input  logic        s_axis0_tlast,
  input  logic        s_axis1_tvalid,
  output logic        s_axis1_tready,
  input  logic [63:0] s_axis1_tdata,
  input  logic [7:0]  s_axis1_tkeep,
  input  logic        s_axis1_tlast
);

  logic [1:0]       mv, mr, ml, sv, sr, sl;
  logic [1:0][63:0] md, sd;
  logic [1:0][7:0]  mk, sk;

  xdma_top #(
    .NUM_MM2S(2), .NUM_S2MM(2), .ID_W(ID_W),
    .MAX_BURST(MAX_BURST),
    .FIFO_DEPTH(FIFO_DEPTH),
    .MAX_OUT_CH(MAX_OUT_CH),
    .MAX_OUT_RD(MAX_OUT_RD),
    .MAX_OUT_WR(MAX_OUT_WR),
    .TLB_ENTRIES(TLB_ENTRIES),
    .PREFETCH_EN(PREFETCH_EN)
  ) u_dut (
    .clk, .rst_n,
    .s_axil_awvalid, .s_axil_awready, .s_axil_awaddr, .s_axil_wvalid, .s_axil_wready,
    .s_axil_wdata, .s_axil_wstrb, .s_axil_bvalid, .s_axil_bready, .s_axil_bresp,
    .s_axil_arvalid, .s_axil_arready, .s_axil_araddr, .s_axil_rvalid, .s_axil_rready,
    .s_axil_rdata, .s_axil_rresp,
    .m_axi_awvalid, .m_axi_awready, .m_axi_awid, .m_axi_awaddr, .m_axi_awlen, .m_axi_awsize,
    .m_axi_awburst, .m_axi_awcache, .m_axi_awprot, .m_axi_awlock, .m_axi_awqos,
    .m_axi_wvalid, .m_axi_wready, .m_axi_wdata, .m_axi_wstrb, .m_axi_wlast,
    .m_axi_bvalid, .m_axi_bready, .m_axi_bid, .m_axi_bresp,
    .m_axi_arvalid, .m_axi_arready, .m_axi_arid, .m_axi_araddr, .m_axi_arlen, .m_axi_arsize,
    .m_axi_arburst, .m_axi_arcache, .m_axi_arprot, .m_axi_arlock, .m_axi_arqos,
    .m_axi_rvalid, .m_axi_rready, .m_axi_rid, .m_axi_rdata, .m_axi_rresp, .m_axi_rlast,
    .m_axis_tvalid(mv), .m_axis_tready(mr), .m_axis_tdata(md), .m_axis_tkeep(mk), .m_axis_tlast(ml),
    .s_axis_tvalid(sv), .s_axis_tready(sr), .s_axis_tdata(sd), .s_axis_tkeep(sk), .s_axis_tlast(sl),
    .irq
  );

  assign m_axis0_tvalid = mv[0];
  assign mr[0] = loop_en ? sr[0] : m_axis0_tready;
  assign m_axis0_tdata = md[0];
  assign m_axis0_tkeep = mk[0];
  assign m_axis0_tlast = ml[0];
  assign sv[0] = loop_en ? mv[0] : s_axis0_tvalid;
  assign s_axis0_tready = sr[0];
  assign sd[0] = loop_en ? md[0] : s_axis0_tdata;
  assign sk[0] = loop_en ? mk[0] : s_axis0_tkeep;
  assign sl[0] = loop_en ? ml[0] : s_axis0_tlast;

  assign m_axis1_tvalid = mv[1];
  assign mr[1] = m_axis1_tready;
  assign m_axis1_tdata = md[1];
  assign m_axis1_tkeep = mk[1];
  assign m_axis1_tlast = ml[1];
  assign sv[1] = s_axis1_tvalid;
  assign s_axis1_tready = sr[1];
  assign sd[1] = s_axis1_tdata;
  assign sk[1] = s_axis1_tkeep;
  assign sl[1] = s_axis1_tlast;

endmodule
