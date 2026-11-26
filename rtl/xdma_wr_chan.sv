// xdma_wr_chan: S2MM data path for one channel (spec 7.4).
//
// s_axis -> input skid -> checks -> FIFO ... W mux pops beats for issued AWs.
// addr_gen -> xlat_stage -> AW request (AW-after-data).
//
// AW-after-data: an AW is requested only when all of its beats are already
// in the FIFO and not promised to an earlier AW
// (count - committed >= beats). So once an AW is on the bus its W beats can
// be sent back to back, and a stalled input stream can never block the
// shared W channel. committed rises by beats on the AW grant and falls by one
// for every beat the W mux takes.
//
// Stream checks per beat (section 6): TKEEP must be all ones; TLAST only on
// the last beat of a descriptor with EOP = 1, and there it is required.
// Any violation is STREAM_LEN; the bad beat is not written to the FIFO.
// The beat count per descriptor uses two nested down-counters (beats in the
// line, lines left), so no multiplier is needed.
//
// The descriptor is done when the B of its final burst returns OKAY (B
// responses for one ID come back in order, so all earlier ones are back).
// Invariants: fifo_count >= committed; outstanding <= MAX_OUT_CH; TREADY is
// low whenever the channel is not running, is in error, or has accepted all
// beats of the current descriptor.
module xdma_wr_chan
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
  input  logic [31:0]   cur_desc,
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
  // AW request to the arbiter
  output logic          aw_req,
  output logic [31:0]   aw_addr,
  output logic [7:0]    aw_len,
  input  logic          aw_gnt,
  // W data to the W mux
  output logic [63:0]   w_data,
  input  logic          w_pop,
  // B responses for this channel's data ID
  input  logic          b_valid,
  input  logic [1:0]    b_resp,
  // AXI-Stream slave
  input  logic          s_axis_tvalid,
  output logic          s_axis_tready,
  input  logic [63:0]   s_axis_tdata,
  input  logic [7:0]    s_axis_tkeep,
  input  logic          s_axis_tlast,
  // events
  output logic [7:0]    ev_bytes,
  output logic          ev_stall
);

  // ------------------------------------------------------- stream intake
  logic        in_valid, in_ready, in_last, in_ready_skid;
  logic [63:0] in_data;
  logic [7:0]  in_keep;
  logic        err_seen;
  // Interface-side beat counters: TREADY is high only while the current
  // descriptor still has beats to accept, so the input skid never takes a
  // beat of the next packet (all terms are registers: no combinational
  // path from TVALID to TREADY).
  logic [12:0] acc_beat;
  logic [15:0] acc_lines;
  logic        acc_want;
  wire         acc_ok = acc_want && dp_run && !dp_drain && !err_seen;

  xdma_skid #(.WIDTH(73)) u_in (
    .clk, .rst_n, .clr(dp_flush),
    .s_valid(s_axis_tvalid && acc_ok), .s_ready(in_ready_skid),
    .s_data({s_axis_tlast, s_axis_tkeep, s_axis_tdata}),
    .m_valid(in_valid), .m_ready(in_ready), .m_data({in_last, in_keep, in_data})
  );
  assign s_axis_tready = in_ready_skid && acc_ok;
  wire   s_hs = s_axis_tvalid && s_axis_tready;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      acc_want <= 1'b0;
    end else if (dp_start || dp_flush) begin
      acc_want <= dp_start;
    end else if (s_hs && acc_beat == 13'd1 && acc_lines == 16'd1) begin
      acc_want <= 1'b0;
    end
  end
  always_ff @(posedge clk) begin
    if (dp_start) begin
      acc_beat  <= dp_desc.line_bytes[15:3];
      acc_lines <= dp_desc.lines;
    end else if (s_hs) begin
      if (acc_beat == 13'd1) begin
        acc_beat  <= dp_desc.line_bytes[15:3];
        acc_lines <= acc_lines - 1'b1;
      end else begin
        acc_beat <= acc_beat - 1'b1;
      end
    end
  end

  // Beats left in the descriptor: nested counters.
  logic [12:0] line_beats, beat_left;
  logic [15:0] lines_left;
  logic        want;              // more beats expected for this descriptor
  wire         last_beat = (beat_left == 13'd1) && (lines_left == 16'd1);

  logic        f_full;
  logic [CW-1:0] fifo_count;
  wire take = in_valid && want && !f_full && dp_run && !err_seen;
  assign in_ready = take;

  wire keep_bad  = (in_keep != 8'hFF);
  wire last_bad  = in_last ? !(last_beat && dp_desc.eop) : (last_beat && dp_desc.eop);
  wire beat_bad  = take && (keep_bad || last_bad);
  wire push      = take && !beat_bad;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      want     <= 1'b0;
    end else begin
      if (dp_start || dp_flush) want <= dp_start;
      else if (take && last_beat) want <= 1'b0;
      else if (beat_bad) want <= 1'b0;
    end
  end

  always_ff @(posedge clk) begin
    if (dp_start) begin
      line_beats <= dp_desc.line_bytes[15:3];
      beat_left  <= dp_desc.line_bytes[15:3];
      lines_left <= dp_desc.lines;
    end else if (take) begin
      if (beat_left == 13'd1) begin
        beat_left  <= line_beats;
        lines_left <= lines_left - 1'b1;
      end else begin
        beat_left <= beat_left - 1'b1;
      end
    end
  end

  xdma_fifo #(.WIDTH(64), .DEPTH(FIFO_DEPTH)) u_fifo (
    .clk, .rst_n, .clr(dp_flush),
    .push(push), .din(in_data),
    .pop(w_pop), .dout(w_data),
    .full(f_full), .empty(), .count(fifo_count)
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

  // ------------------------------------------------- AW-after-data issue
  logic [CW-1:0] committed;
  logic [OW-1:0] outstanding;

  wire data_here = (32'(fifo_count) - 32'(committed)) >= 32'(xl_beats);
  assign aw_req   = xl_valid && data_here && (outstanding < OW'(MAX_OUT_CH)) && !dp_drain;
  assign aw_addr  = xl_pa;
  assign aw_len   = 8'(xl_beats) - 8'd1;
  assign xl_ready = aw_gnt;
  wire aw_hs = aw_req && aw_gnt;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      committed   <= '0;
      outstanding <= '0;
    end else begin
      committed   <= committed + (aw_hs ? CW'(xl_beats) : '0) - CW'(w_pop);
      outstanding <= outstanding + OW'(aw_hs) - OW'(b_valid);
    end
  end

  // Burst queue: {pa, beats, last_of_desc}, one entry per outstanding AW.
  logic          bq_last;
  logic [31:0]   bq_pa;
  logic [BW-1:0] bq_beats;
  xdma_fifo #(.WIDTH(33 + BW), .DEPTH(MAX_OUT_CH)) u_bq (
    .clk, .rst_n, .clr(1'b0),
    .push(aw_hs), .din({xl_pa, xl_beats, xl_last}),
    .pop(b_valid), .dout({bq_pa, bq_beats, bq_last}),
    .full(), .empty(), .count()
  );

  // ------------------------------------------------------- B / completion
  wire b_ok = b_valid && (b_resp == RESP_OKAY);

  always_ff @(posedge clk) begin
    if (!rst_n || dp_flush || dp_start) err_seen <= 1'b0;
    else if (dp_err) err_seen <= 1'b1;
  end

  logic xl_fault_q;
  always_ff @(posedge clk) begin
    if (!rst_n) xl_fault_q <= 1'b0;
    else        xl_fault_q <= xl_fault;
  end
  wire fault_pulse = xl_fault && !xl_fault_q;
  wire b_err       = b_valid && !b_ok;

  // Priority when several errors coincide: write response, stream, fault.
  always_comb begin
    dp_err      = 1'b0;
    dp_err_code = ERR_WR_RESP;
    dp_err_addr = bq_pa;
    if (!err_seen && !dp_drain) begin
      if (b_err) begin
        dp_err = 1'b1;
      end else if (beat_bad) begin
        dp_err      = 1'b1;
        dp_err_code = ERR_STREAM_LEN;
        dp_err_addr = cur_desc;
      end else if (fault_pulse) begin
        dp_err      = 1'b1;
        dp_err_code = ERR_XLAT_FAULT;
        dp_err_addr = xl_va;
      end
    end
  end

  assign dp_done = b_ok && bq_last && dp_run && !err_seen;
  assign dp_idle = (outstanding == '0);

  always_ff @(posedge clk) begin
    if (!rst_n || dp_start) dp_bytes <= '0;
    else if (b_ok)          dp_bytes <= dp_bytes + 32'({bq_beats, 3'b000});
  end

  // ------------------------------------------------------------- events
  assign ev_bytes = b_ok ? 8'({bq_beats, 3'b000}) : 8'd0;
  assign ev_stall = (aw_req && !aw_gnt) || (in_valid && want && f_full && dp_run);

endmodule
