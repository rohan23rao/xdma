// xdma_w_mux: engine-wide W-order queue and W data multiplexer (spec 7.4).
//
// Every AW accepted by the AW arbiter pushes {awid, awlen} into the W-order
// queue (at acceptance, not at the bus handshake, so W never waits for
// AWREADY; AXI4 A3.3.1).
// The mux takes the head entry and sends exactly awlen+1 beats from the
// source that ID names, with WLAST on the final beat, then pops the entry.
// AXI4 forbids write-data interleaving, so W beats leave in AW order.
//   ID 0..NUM_CH-1        data of S2MM channel ID (from its FIFO)
//   ID NUM_CH..2*NUM_CH-1 status writeback of channel ID-NUM_CH
//                         (one beat from its holding register)
// W leaves through a skid buffer so WVALID/WDATA/WLAST are registered.
// Invariants: WLAST exactly on beat awlen of each burst; no beat is sent for
// an AW the arbiter has not accepted; queue never overflows (it holds at
// most MAX_OUT_WR entries, the AW outstanding limit).
module xdma_w_mux #(
  parameter int NUM_MM2S   = 2,
  parameter int NUM_S2MM   = 2,
  parameter int ID_W       = 4,
  parameter int MAX_OUT_WR = 8,
  localparam int NUM_CH = NUM_MM2S + NUM_S2MM
) (
  input  logic              clk,
  input  logic              rst_n,
  // AW accepted by the arbiter
  input  logic              aw_push,
  input  logic [ID_W-1:0]   aw_id,
  input  logic [7:0]        aw_len,
  // sources
  input  logic [63:0]       s2mm_data [NUM_S2MM],
  output logic [NUM_S2MM-1:0] s2mm_pop,
  input  logic [63:0]       wb_data   [NUM_CH],
  // W master
  output logic              m_axi_wvalid,
  input  logic              m_axi_wready,
  output logic [63:0]       m_axi_wdata,
  output logic [7:0]        m_axi_wstrb,
  output logic              m_axi_wlast
);

  logic            q_empty;
  logic [ID_W-1:0] q_id;
  logic [7:0]      q_len;
  logic [7:0]      beat;
  logic            s_ready;

  wire send = !q_empty && s_ready;
  wire last = (beat == q_len);

  xdma_fifo #(.WIDTH(ID_W + 8), .DEPTH(MAX_OUT_WR)) u_q (
    .clk, .rst_n, .clr(1'b0),
    .push(aw_push), .din({aw_id, aw_len}),
    .pop(send && last), .dout({q_id, q_len}),
    .full(), .empty(q_empty), .count()
  );

  always_ff @(posedge clk) begin
    if (!rst_n)    beat <= '0;
    else if (send) beat <= last ? 8'd0 : beat + 1'b1;
  end

  // Source select.
  logic [63:0] data;
  always_comb begin
    data     = '0;
    s2mm_pop = '0;
    for (int c = 0; c < NUM_S2MM; c++) begin
      if (int'(q_id) == NUM_MM2S + c) begin
        data        = s2mm_data[c];
        s2mm_pop[c] = send;
      end
    end
    for (int c = 0; c < NUM_CH; c++) begin
      if (int'(q_id) == NUM_CH + c) data = wb_data[c];
    end
  end

  xdma_skid #(.WIDTH(65)) u_w (
    .clk, .rst_n, .clr(1'b0),
    .s_valid(send), .s_ready(s_ready), .s_data({last, data}),
    .m_valid(m_axi_wvalid), .m_ready(m_axi_wready), .m_data({m_axi_wlast, m_axi_wdata})
  );
  assign m_axi_wstrb = 8'hFF;

endmodule
