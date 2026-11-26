// xdma_desc_fetch: per-channel controller (spec 7.1).
//
// Owns the channel state machine, the descriptor fetcher with a one-entry
// prefetch (shadow) buffer, section 6 validation, the status writeback and
// the channel's error/abort handling. The data path (rd_chan / wr_chan) moves
// the data and reports done / error / idle.
//
// States:
//   IDLE    wait for START
//   FETCH   wait for the descriptor at fetch_addr to arrive in the shadow
//   RUN     data path busy; prefetch of NEXT may be in flight
//   WB      status writeback AW requested (W data held in wb_data)
//   WB_WAIT wait for the writeback B
//   DRAIN   error/abort: data path stops issuing; wait for it to go idle
//   EWB     error-status writeback AW requested
//   EWB_WAIT wait for its B
//   FINISH  wait for any fetch still in flight, then report and go IDLE
//
// Descriptor reads use the channel's descriptor ID (NUM_CH + c, set by the
// AR arbiter), ARLEN = 3. The shadow buffer is
// filled by every descriptor read (first fetch and prefetch alike), so FETCH
// only has to wait for shadow_valid.
// Invariants: at most one descriptor read outstanding; the writeback AW is
// requested only after the data path reports done (RUN) or idle (DRAIN);
// DONE is counted only after the writeback B returns OKAY.
module xdma_desc_fetch
  import xdma_pkg::*;
#(
  parameter int PREFETCH_EN = 1
) (
  input  logic        clk,
  input  logic        rst_n,
  // CSR
  input  logic        start,
  input  logic        abort,
  input  logic [31:0] desc_head,
  output logic        busy,
  output logic [2:0]  err,
  output logic [31:0] err_addr,
  output logic [31:0] cur_desc,
  output logic        ev_desc_done,
  output logic        ev_done_irq,
  output logic        ev_err_irq,
  // descriptor read (AR) request and its R beats (already ID-filtered)
  output logic        ar_req,
  output logic [31:0] ar_addr,
  input  logic        ar_gnt,
  input  logic        r_valid,
  input  logic [63:0] r_data,
  input  logic [1:0]  r_resp,
  input  logic        r_last,
  // status writeback (AW + one W beat) and its B (already ID-filtered)
  output logic        aw_req,
  output logic [31:0] aw_addr,
  input  logic        aw_gnt,
  output logic [63:0] wb_data,
  input  logic        b_valid,
  input  logic [1:0]  b_resp,
  // data path control
  output logic        dp_start,      // pulse: load cur descriptor
  output desc_t       dp_desc,
  output logic        dp_run,        // channel in RUN
  output logic        dp_drain,      // stop issuing, discard
  output logic        dp_flush,      // pulse: clear FIFOs (end of drain)
  input  logic        dp_done,       // pulse: all data of the descriptor done
  input  logic        dp_err,        // pulse: data path error
  input  logic [2:0]  dp_err_code,
  input  logic [31:0] dp_err_addr,
  input  logic        dp_idle,       // nothing outstanding on the data IDs
  input  logic [31:0] dp_bytes       // bytes moved for the current descriptor
);

  typedef enum logic [3:0] {
    S_IDLE, S_FETCH, S_RUN, S_WB, S_WB_WAIT, S_DRAIN, S_EWB, S_EWB_WAIT, S_FINISH
  } state_e;

  state_e state;
  desc_t  cur;                 // descriptor being executed
  logic [31:0] cur_addr;

  // Fetch machinery and shadow buffer
  logic        fetch_req;      // AR requested, not yet granted
  logic        fetch_out;      // AR granted, R beats pending
  logic [31:0] fetch_addr;
  logic [1:0]  beat;
  logic        resp_bad;
  desc_t       sh;
  logic        sh_valid;
  logic [2:0]  sh_code;
  logic [31:0] sh_addr;

  logic        abort_pend;     // ABORT seen in FETCH / WB / WB_WAIT
  logic [2:0]  pend_err;       // error to report at FINISH
  logic [31:0] pend_addr;

  logic        launch_fetch, cancel_fetch, consume_shadow;
  logic [31:0] launch_addr;

  wire fetch_busy = fetch_req || fetch_out;

  // Prefetch the next descriptor while the current one runs.
  wire want_prefetch = (PREFETCH_EN != 0) && !cur.last && !sh_valid && !fetch_busy &&
                       !abort_pend && !abort && !dp_err &&
                       (state == S_RUN || state == S_WB || state == S_WB_WAIT);

  assign ar_req  = fetch_req;
  assign ar_addr = fetch_addr;
  assign busy    = (state != S_IDLE);
  assign cur_desc = cur_addr;
  assign dp_desc  = cur;
  assign dp_run   = (state == S_RUN);
  assign dp_drain = (state == S_DRAIN);
  assign aw_req   = (state == S_WB) || (state == S_EWB);
  assign aw_addr  = cur_addr + 32'h18;

  // ------------------------------------------------------ descriptor reads
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      fetch_req <= 1'b0;
      fetch_out <= 1'b0;
      sh_valid  <= 1'b0;
      beat      <= '0;
      resp_bad  <= 1'b0;
    end else begin
      // launch: first fetch (from IDLE / WB_WAIT) or prefetch
      if (launch_fetch) begin
        fetch_req <= 1'b1;
      end else if (fetch_req && ar_gnt) begin
        fetch_req <= 1'b0;
        fetch_out <= 1'b1;
      end else if (cancel_fetch) begin
        fetch_req <= 1'b0;               // never granted: safe to withdraw
      end
      if (r_valid) begin
        beat <= beat + 1'b1;
        if (r_resp != RESP_OKAY) resp_bad <= 1'b1;
        if (r_last) begin
          fetch_out <= 1'b0;
          sh_valid  <= 1'b1;
          beat      <= '0;
          resp_bad  <= 1'b0;
        end
      end
      if (consume_shadow) sh_valid <= 1'b0;
    end
  end

  // Shadow fields: datapath registers, enable-coded.
  always_ff @(posedge clk) begin
    if (launch_fetch) begin
      fetch_addr <= launch_addr;
    end
    if (r_valid) begin
      case (beat)
        2'd0: begin sh.next <= r_data[31:0]; sh.addr <= r_data[63:32]; end
        2'd1: begin sh.line_bytes <= r_data[15:0]; sh.lines <= r_data[31:16]; sh.stride <= r_data[63:32]; end
        2'd2: begin sh.last <= r_data[0]; sh.irq <= r_data[1]; sh.eop <= r_data[2]; end
        default: ;
      endcase
      if (r_last) begin
        sh_addr <= fetch_addr;
        sh_code <= (resp_bad || r_resp != RESP_OKAY) ? ERR_DESC_FETCH : ERR_NONE;
      end
    end
  end

  // Validation happens on the complete shadow entry.
  logic [2:0] sh_status;
  assign sh_status = (sh_code != ERR_NONE) ? sh_code :
                         (!desc_ok(sh) ? ERR_DESC_INVALID : ERR_NONE);

  // ------------------------------------------------------------ main FSM

  always_comb begin
    launch_fetch   = 1'b0;
    launch_addr    = cur.next;
    cancel_fetch   = 1'b0;
    consume_shadow = 1'b0;
    if (state == S_IDLE && start) begin
      launch_fetch = 1'b1;
      launch_addr  = desc_head;
    end else if (state == S_WB_WAIT && b_valid && b_resp == RESP_OKAY && !cur.last &&
                 !abort_pend && !abort && !sh_valid && !fetch_busy) begin
      launch_fetch = 1'b1;                 // no prefetch happened: fetch now
    end else if (want_prefetch) begin
      launch_fetch = 1'b1;
    end
    if ((state == S_FETCH && abort) || state == S_DRAIN || state == S_FINISH ||
        (abort_pend && state != S_RUN))
      cancel_fetch = fetch_req && !ar_gnt;
    if (state == S_FETCH && sh_valid && !abort_pend && !abort) consume_shadow = 1'b1;
    if (state == S_FINISH && !fetch_out) consume_shadow = 1'b1;
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state        <= S_IDLE;
      err          <= ERR_NONE;
      err_addr     <= '0;
      cur_addr     <= '0;
      abort_pend   <= 1'b0;
      pend_err     <= ERR_NONE;
      pend_addr    <= '0;
      dp_start     <= 1'b0;
      dp_flush     <= 1'b0;
      ev_desc_done <= 1'b0;
      ev_done_irq  <= 1'b0;
      ev_err_irq   <= 1'b0;
    end else begin
      dp_start     <= 1'b0;
      dp_flush     <= 1'b0;
      ev_desc_done <= 1'b0;
      ev_done_irq  <= 1'b0;
      ev_err_irq   <= 1'b0;
      unique case (state)
        S_IDLE: begin
          abort_pend <= 1'b0;
          if (start) begin
            err      <= ERR_NONE;
            cur_addr <= desc_head;
            state    <= S_FETCH;
          end
        end

        S_FETCH: begin
          if (abort || abort_pend) begin
            // Abort before the descriptor started: no writeback.
            abort_pend <= 1'b1;
            pend_err   <= ERR_ABORTED;
            pend_addr  <= cur_addr;
            state      <= S_FINISH;
          end else if (sh_valid) begin
            cur_addr <= sh_addr;
            if (sh_status != ERR_NONE) begin
              pend_err  <= sh_status;
              pend_addr <= sh_addr;
              state     <= S_FINISH;
            end else begin
              dp_start <= 1'b1;
              state    <= S_RUN;
            end
          end
        end

        S_RUN: begin
          // A data error wins. Otherwise completion wins over an ABORT in
          // the same cycle: the descriptor is written back as done and the
          // abort takes effect after the writeback (as in WB / WB_WAIT).
          if (dp_err) begin
            pend_err  <= dp_err_code;
            pend_addr <= dp_err_addr;
            state     <= S_DRAIN;
          end else if (dp_done) begin
            if (abort) abort_pend <= 1'b1;
            state <= S_WB;
          end else if (abort) begin
            pend_err  <= ERR_ABORTED;
            pend_addr <= cur_addr;
            state     <= S_DRAIN;
          end
        end

        S_WB: begin
          if (abort) abort_pend <= 1'b1;
          if (aw_gnt) state <= S_WB_WAIT;
        end

        S_WB_WAIT: begin
          if (abort) abort_pend <= 1'b1;
          if (b_valid) begin
            if (b_resp != RESP_OKAY) begin
              pend_err  <= ERR_WR_RESP;
              pend_addr <= cur_addr + 32'h18;
              state     <= S_FINISH;
            end else begin
              ev_desc_done <= 1'b1;
              ev_done_irq  <= cur.irq;
              if (abort || abort_pend) begin
                pend_err  <= ERR_ABORTED;
                pend_addr <= cur_addr;
                state     <= S_FINISH;
              end else if (cur.last) begin
                pend_err <= ERR_NONE;
                state    <= S_FINISH;
              end else begin
                cur_addr <= cur.next;
                state    <= S_FETCH;
              end
            end
          end
        end

        S_DRAIN: begin
          if (dp_idle && !fetch_out) begin
            dp_flush <= 1'b1;
            state    <= S_EWB;
          end
        end

        S_EWB: begin
          if (aw_gnt) state <= S_EWB_WAIT;
        end

        S_EWB_WAIT: begin
          // The original error is kept even if this writeback fails.
          if (b_valid) state <= S_FINISH;
        end

        S_FINISH: begin
          if (!fetch_out && !fetch_req) begin
            err        <= pend_err;
            ev_err_irq <= (pend_err != ERR_NONE);
            if (pend_err != ERR_NONE) err_addr <= pend_addr;
            abort_pend <= 1'b0;
            state      <= S_IDLE;
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

  // Status word: [0] DONE, [3:1] ERR, [63:32] BYTES. The error writeback
  // also sets DONE = 1: the engine is finished with this descriptor.
  // Captured in WB (not on dp_done): the data path counts the final beat in
  // the same cycle it raises dp_done. The W mux reads wb_data only after the
  // AW has been granted and accepted on the bus, at least two cycles later.
  always_ff @(posedge clk) begin
    if (state == S_WB)
      wb_data <= {dp_bytes, 28'b0, ERR_NONE, 1'b1};
    else if (state == S_DRAIN && dp_idle && !fetch_out)
      wb_data <= {dp_bytes, 28'b0, pend_err, 1'b1};
  end

  // Descriptor fields: datapath, loaded from the shadow. Only read in states
  // that are entered after this load, so no reset is needed.
  always_ff @(posedge clk) begin
    if (state == S_FETCH && sh_valid && !abort && !abort_pend && sh_status == ERR_NONE)
      cur <= sh;
  end

endmodule
