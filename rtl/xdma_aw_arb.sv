// xdma_aw_arb: write-address arbiter (spec 9.3).
//
// Fixed priority between classes, round-robin inside a class:
//   1. status writebacks   ID NUM_CH + c,   AWLEN 0      (RR over channels)
//   2. S2MM data bursts    ID NUM_MM2S + c, AWLEN beats-1 (RR over S2MM)
// At most MAX_OUT_WR AWs are outstanding engine-wide: counted from the
// arbiter grant until the B handshake. AW leaves through a skid buffer.
// The W-order queue is pushed by the bus AW handshake (see xdma_w_mux).
// Invariants: one grant per cycle at most; wr_out <= MAX_OUT_WR.
module xdma_aw_arb #(
  parameter int NUM_MM2S   = 2,
  parameter int NUM_S2MM   = 2,
  parameter int ID_W       = 4,
  parameter int MAX_OUT_WR = 8,
  localparam int NUM_CH = NUM_MM2S + NUM_S2MM,
  localparam int OW = $clog2(MAX_OUT_WR + 1)
) (
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic [NUM_CH-1:0]    wb_req,
  input  logic [31:0]          wb_addr [NUM_CH],
  output logic [NUM_CH-1:0]    wb_gnt,
  input  logic [NUM_S2MM-1:0]  data_req,
  input  logic [31:0]          data_addr [NUM_S2MM],
  input  logic [7:0]           data_len  [NUM_S2MM],
  output logic [NUM_S2MM-1:0]  data_gnt,
  input  logic                 b_hs,
  output logic [OW-1:0]        wr_out,
  output logic                 m_axi_awvalid,
  input  logic                 m_axi_awready,
  output logic [ID_W-1:0]      m_axi_awid,
  output logic [31:0]          m_axi_awaddr,
  output logic [7:0]           m_axi_awlen
);

  localparam int WPW = (NUM_CH > 1) ? $clog2(NUM_CH) : 1;
  localparam int SPW = (NUM_S2MM > 1) ? $clog2(NUM_S2MM) : 1;

  logic                s_ready;
  logic [NUM_CH-1:0]   w_grant;
  logic [WPW-1:0]      w_idx;
  logic [NUM_S2MM-1:0] s_grant;
  logic [SPW-1:0]      s_idx;

  wire can    = s_ready && (wr_out < OW'(MAX_OUT_WR));
  wire sel_wb = |wb_req;
  wire sel_d  = !(|wb_req) && (|data_req);
  wire accept = can && (sel_wb || sel_d);

  xdma_rr_arb #(.N(NUM_CH)) u_rr_wb (
    .clk, .rst_n, .req(wb_req), .hs(accept && sel_wb), .grant(w_grant), .grant_idx(w_idx)
  );
  xdma_rr_arb #(.N(NUM_S2MM)) u_rr_data (
    .clk, .rst_n, .req(data_req), .hs(accept && sel_d), .grant(s_grant), .grant_idx(s_idx)
  );

  assign wb_gnt   = (accept && sel_wb) ? w_grant : '0;
  assign data_gnt = (accept && sel_d) ? s_grant : '0;

  logic [ID_W-1:0] id;
  logic [31:0]     addr;
  logic [7:0]      len;
  always_comb begin
    if (sel_wb) begin
      id = ID_W'(NUM_CH + int'(w_idx)); addr = wb_addr[w_idx]; len = 8'd0;
    end else begin
      id = ID_W'(NUM_MM2S + int'(s_idx)); addr = data_addr[s_idx]; len = data_len[s_idx];
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) wr_out <= '0;
    else        wr_out <= wr_out + OW'(accept) - OW'(b_hs);
  end

  xdma_skid #(.WIDTH(ID_W + 40)) u_aw (
    .clk, .rst_n, .clr(1'b0),
    .s_valid(accept), .s_ready(s_ready), .s_data({id, addr, len}),
    .m_valid(m_axi_awvalid), .m_ready(m_axi_awready),
    .m_data({m_axi_awid, m_axi_awaddr, m_axi_awlen})
  );

endmodule
