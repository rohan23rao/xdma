// xdma_ar_arb: read-address arbiter (spec 9.2).
//
// Fixed priority between classes, round-robin inside a class:
//   1. page-table walker      ID 2*NUM_CH,     ARLEN 0
//   2. descriptor fetches     ID NUM_CH + c,   ARLEN 3     (RR over channels)
//   3. MM2S data bursts       ID c,            ARLEN beats-1 (RR over MM2S)
// At most MAX_OUT_RD ARs are outstanding engine-wide: counted from the
// arbiter grant (the request enters the AR skid buffer) until RLAST.
// Requests may change or drop before they are granted; a grant is final.
// AR leaves through a skid buffer, so ARVALID and the payload are registered.
// Invariants: one grant per cycle at most; rd_out <= MAX_OUT_RD.
module xdma_ar_arb #(
  parameter int NUM_MM2S   = 2,
  parameter int NUM_CH     = 4,
  parameter int ID_W       = 4,
  parameter int MAX_OUT_RD = 16,
  localparam int OW = $clog2(MAX_OUT_RD + 1)
) (
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 walk_req,
  input  logic [31:0]          walk_addr,
  output logic                 walk_gnt,
  input  logic [NUM_CH-1:0]    desc_req,
  input  logic [31:0]          desc_addr [NUM_CH],
  output logic [NUM_CH-1:0]    desc_gnt,
  input  logic [NUM_MM2S-1:0]  data_req,
  input  logic [31:0]          data_addr [NUM_MM2S],
  input  logic [7:0]           data_len  [NUM_MM2S],
  output logic [NUM_MM2S-1:0]  data_gnt,
  input  logic                 r_last_hs,   // RVALID && RREADY && RLAST
  output logic [OW-1:0]        rd_out,
  output logic                 m_axi_arvalid,
  input  logic                 m_axi_arready,
  output logic [ID_W-1:0]      m_axi_arid,
  output logic [31:0]          m_axi_araddr,
  output logic [7:0]           m_axi_arlen
);

  localparam int DPW = (NUM_CH > 1) ? $clog2(NUM_CH) : 1;
  localparam int MPW = (NUM_MM2S > 1) ? $clog2(NUM_MM2S) : 1;

  logic                s_ready;
  logic [NUM_CH-1:0]   d_grant;
  logic [DPW-1:0]      d_idx;
  logic [NUM_MM2S-1:0] m_grant;
  logic [MPW-1:0]      m_idx;

  wire can     = s_ready && (rd_out < OW'(MAX_OUT_RD));
  wire sel_w   = walk_req;
  wire sel_d   = !walk_req && (|desc_req);
  wire sel_m   = !walk_req && !(|desc_req) && (|data_req);
  wire accept  = can && (sel_w || sel_d || sel_m);

  xdma_rr_arb #(.N(NUM_CH)) u_rr_desc (
    .clk, .rst_n, .req(desc_req), .hs(accept && sel_d), .grant(d_grant), .grant_idx(d_idx)
  );
  xdma_rr_arb #(.N(NUM_MM2S)) u_rr_data (
    .clk, .rst_n, .req(data_req), .hs(accept && sel_m), .grant(m_grant), .grant_idx(m_idx)
  );

  assign walk_gnt = accept && sel_w;
  assign desc_gnt = (accept && sel_d) ? d_grant : '0;
  assign data_gnt = (accept && sel_m) ? m_grant : '0;

  logic [ID_W-1:0] id;
  logic [31:0]     addr;
  logic [7:0]      len;
  always_comb begin
    if (sel_w) begin
      id = ID_W'(2 * NUM_CH); addr = walk_addr; len = 8'd0;
    end else if (sel_d) begin
      id = ID_W'(NUM_CH + int'(d_idx)); addr = desc_addr[d_idx]; len = 8'd3;
    end else begin
      id = ID_W'(m_idx); addr = data_addr[m_idx]; len = data_len[m_idx];
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) rd_out <= '0;
    else        rd_out <= rd_out + OW'(accept) - OW'(r_last_hs);
  end

  xdma_skid #(.WIDTH(ID_W + 40)) u_ar (
    .clk, .rst_n, .clr(1'b0),
    .s_valid(accept), .s_ready(s_ready), .s_data({id, addr, len}),
    .m_valid(m_axi_arvalid), .m_ready(m_axi_arready),
    .m_data({m_axi_arid, m_axi_araddr, m_axi_arlen})
  );

endmodule
