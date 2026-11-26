// xdma_rd_chan: MM2S data path for one channel (spec 7.3).
//
// addr_gen -> xlat_stage -> AR request (reserve-on-issue) ... R beats ->
// FIFO -> skid -> m_axis.
//
// Reserve-on-issue: an AR is requested only if the FIFO has room for every
// beat of every burst already in flight plus this one
// (count + reserved + beats <= FIFO_DEPTH) and fewer than MAX_OUT_CH bursts
// are outstanding. So R data for this channel can always be accepted and the
// shared R channel never has to stall (RREADY is tied high at the top).
//
// A per-channel burst queue (one entry per outstanding AR, in order, as R
// data for one ID is in order) remembers each burst's PA and whether it ends
// the descriptor. The final beat of the descriptor is tagged in the FIFO; when
// that tagged beat is accepted on m_axis the descriptor's data is done.
//
// On an R error the channel reports RD_RESP once and stops writing R data to
// the FIFO. While draining it keeps accepting (and discarding) R beats until
// every outstanding burst has returned, then dp_flush clears the FIFO. Beats
// already in the output skid buffer cannot be withdrawn (AXI-Stream VALID
// must stay high); they are delivered but no longer counted (stale_cnt).
// Invariants: count + reserved <= FIFO_DEPTH; outstanding <= MAX_OUT_CH;
// an R beat for this channel always finds space in the FIFO.
module xdma_rd_chan
  import xdma_pkg::*;
#(
  parameter int MAX_BURST  = 16,
  parameter int FIFO_DEPTH = 64,
  parameter int MAX_OUT_CH = 8,
  localparam int BW  = $clog2(MAX_BURST + 1),
  localparam int CW  = $clog2(FIFO_DEPTH + 1),
  localparam int OW  = $clog2(MAX_OUT_CH + 1)
) (
  input  logic          clk,
  input  logic          rst_n,
  input  logic          translate_en,
  // channel controller
  input  logic          dp_start,
  input  desc_t         dp_desc,
  input  logic          dp_run,
  input  logic          dp_drain,
  input  logic          dp_flush,
  output logic          dp_done,
  output logic          dp_err,
  output logic [2:0]    dp_err_code,
  output logic [31:0]   dp_err_addr,
  output logic          dp_idle,
  output logic [31:0]   dp_bytes,
  // TLB / walker
  output logic          tlb_req,
  output logic [19:0]   tlb_vpn,
  input  logic          tlb_gnt,
  input  logic          tlb_resp_valid,
  input  xl_kind_e      tlb_resp_kind,
  input  logic [19:0]   tlb_resp_ppn,
  input  logic          walk_resp_valid,
  input  logic          walk_resp_ok,
  input  logic [19:0]   walk_resp_ppn,
  input  logic          walk_busy,
  // AR request to the arbiter
  output logic          ar_req,
  output logic [31:0]   ar_addr,
  output logic [7:0]    ar_len,
  input  logic          ar_gnt,
  // R beats for this channel's data ID
  input  logic          r_valid,
  input  logic [63:0]   r_data,
  input  logic [1:0]    r_resp,
  input  logic          r_last,
  // AXI-Stream master (registered through a skid buffer)
  output logic          m_axis_tvalid,
  input  logic          m_axis_tready,
  output logic [63:0]   m_axis_tdata,
  output logic          m_axis_tlast,
  // events
  output logic [7:0]    ev_bytes,
  output logic          ev_stall
);

  // ------------------------------------------------------------- addr_gen
  logic          ag_valid, ag_ready, ag_last;
  logic [31:0]   ag_va;
  logic [BW-1:0] ag_beats;

  xdma_addr_gen #(.MAX_BURST(MAX_BURST)) u_ag (
    .clk, .rst_n,
    .start(dp_start), .addr(dp_desc.addr), .line_bytes(dp_desc.line_bytes),
    .lines(dp_desc.lines), .stride(dp_desc.stride), .abort(dp_drain),
    .busy(), .out_valid(ag_valid), .out_ready(ag_ready),
    .out_va(ag_va), .out_beats(ag_beats), .out_last(ag_last)
  );

  // --------------------------------------------------------- translation
  logic          xl_valid, xl_ready, xl_last, xl_fault;
  logic [31:0]   xl_pa, xl_va;
  logic [BW-1:0] xl_beats;

  xdma_xlat_stage #(.BW(BW)) u_xl (
    .clk, .rst_n, .translate_en, .abort(dp_drain),
    .in_valid(ag_valid), .in_ready(ag_ready), .in_va(ag_va), .in_beats(ag_beats), .in_last(ag_last),
    .out_valid(xl_valid), .out_ready(xl_ready), .out_pa(xl_pa), .out_va(xl_va),
    .out_beats(xl_beats), .out_last(xl_last), .fault(xl_fault),
    .tlb_req, .tlb_vpn, .tlb_gnt, .tlb_resp_valid, .tlb_resp_kind, .tlb_resp_ppn,
    .walk_resp_valid, .walk_resp_ok, .walk_resp_ppn, .walk_busy
  );

  // ------------------------------------------------- reserve-on-issue AR
  logic [CW-1:0] fifo_count;
  logic [CW-1:0] reserved;
  logic [OW-1:0] outstanding;

  wire room = (32'(fifo_count) + 32'(reserved) + 32'(xl_beats)) <= FIFO_DEPTH;
  assign ar_req  = xl_valid && room && (outstanding < OW'(MAX_OUT_CH)) && !dp_drain;
  assign ar_addr = xl_pa;
  assign ar_len  = 8'(xl_beats) - 8'd1;
  assign xl_ready = ar_gnt;

  wire ar_hs = ar_req && ar_gnt;

  // Burst queue: {pa, last_of_desc}, one entry per outstanding AR.
  logic        bq_empty, bq_full, bq_last;
  logic [31:0] bq_pa;
  xdma_fifo #(.WIDTH(33), .DEPTH(MAX_OUT_CH)) u_bq (
    .clk, .rst_n, .clr(1'b0),
    .push(ar_hs), .din({xl_pa, xl_last}),
    .pop(r_valid && r_last), .dout({bq_pa, bq_last}),
    .full(bq_full), .empty(bq_empty), .count()
  );

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      reserved    <= '0;
      outstanding <= '0;
    end else begin
      reserved    <= reserved + (ar_hs ? CW'(xl_beats) : '0) - CW'(r_valid);
      outstanding <= outstanding + OW'(ar_hs) - OW'(r_valid && r_last);
    end
  end

  // ------------------------------------------------------------ R path
  logic err_seen;
  wire  r_bad  = r_valid && (r_resp != RESP_OKAY);
  wire  r_push = r_valid && !r_bad && !err_seen && !dp_drain;
  wire  r_tag  = r_last && bq_last;           // final beat of the descriptor

  // FIFO entry: {tag, tlast, data}
  logic        f_empty, f_tag, f_tlast;
  logic [63:0] f_data;
  logic        s_ready;
  // No pops while draining, nor in the flush cycle (the clear lands at the
  // clock edge, so a pop in that cycle would leak a discarded beat).
  wire         f_pop = !f_empty && s_ready && !dp_drain && !dp_flush;

  xdma_fifo #(.WIDTH(66), .DEPTH(FIFO_DEPTH)) u_fifo (
    .clk, .rst_n, .clr(dp_flush),
    .push(r_push), .din({r_tag, r_tag && dp_desc.eop, r_data}),
    .pop(f_pop), .dout({f_tag, f_tlast, f_data}),
    .full(), .empty(f_empty), .count(fifo_count)
  );

  always_ff @(posedge clk) begin
    if (!rst_n || dp_flush || dp_start) err_seen <= 1'b0;
    else if (r_bad) err_seen <= 1'b1;
  end

  // Errors: first R error (RD_RESP, address of the failing burst) or a
  // translation fault (XLAT_FAULT, the faulting VA). One pulse each; the
  // controller leaves RUN on the first one.
  logic xl_fault_q;
  always_ff @(posedge clk) begin
    if (!rst_n) xl_fault_q <= 1'b0;
    else        xl_fault_q <= xl_fault;
  end
  wire fault_pulse = xl_fault && !xl_fault_q;
  wire r_err_pulse = r_bad && !err_seen && !dp_drain;
  assign dp_err      = r_err_pulse || (fault_pulse && !dp_drain);
  assign dp_err_code = r_err_pulse ? ERR_RD_RESP : ERR_XLAT_FAULT;
  assign dp_err_addr = r_err_pulse ? bq_pa : xl_va;

  // ------------------------------------------------- stream output (skid)
  logic        o_tag;
  logic [1:0]  stale_cnt;     // beats in the skid left over from a drain
  logic        skid_full;

  xdma_skid #(.WIDTH(66)) u_out (
    .clk, .rst_n, .clr(1'b0),
    .s_valid(f_pop), .s_ready(s_ready), .s_data({f_tag, f_tlast, f_data}),
    .m_valid(m_axis_tvalid), .m_ready(m_axis_tready),
    .m_data({o_tag, m_axis_tlast, m_axis_tdata})
  );
  assign skid_full = !s_ready;     // skid entry occupied

  wire out_hs   = m_axis_tvalid && m_axis_tready;
  wire out_live = out_hs && (stale_cnt == 2'd0);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      stale_cnt <= '0;
    end else if (dp_flush) begin
      stale_cnt <= 2'(m_axis_tvalid) + 2'(skid_full) - 2'(out_hs);
    end else if (out_hs && stale_cnt != 2'd0) begin
      stale_cnt <= stale_cnt - 1'b1;
    end
  end

  assign dp_done = out_live && o_tag && dp_run;

  always_ff @(posedge clk) begin
    if (!rst_n || dp_start) dp_bytes <= '0;
    else if (out_live)      dp_bytes <= dp_bytes + 32'd8;
  end

  assign dp_idle = (outstanding == '0);

  // ------------------------------------------------------------- events
  assign ev_bytes = out_live ? 8'd8 : 8'd0;
  // Stall: a request not granted, a burst blocked on FIFO space, or stream
  // backpressure. Waiting for the per-channel outstanding limit or for a
  // translation is not a stall (D-031).
  assign ev_stall = (ar_req && !ar_gnt) || (xl_valid && !room && !dp_drain) ||
                    (m_axis_tvalid && !m_axis_tready);

endmodule
