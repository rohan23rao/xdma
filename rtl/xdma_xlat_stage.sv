// xdma_xlat_stage: one-entry translation stage between addr_gen and the AXI
// request logic of a data channel (spec 8).
//
// Holds one burst. With TRANSLATE_EN = 0 the burst is ready the cycle after
// it is captured (PA = VA; translation adds no cycles to the pipeline, the
// holding register is a pipeline stage present in both modes). With
// TRANSLATE_EN = 1 it asks the shared TLB:
//   LOOKUP  request held until the TLB arbiter grants it
//   RESP    TLB answer one cycle after the grant: HIT, FAULT, WALK or RETRY
//   WALK    waiting for the walker's answer for this channel (an answer
//           marked stale by a TLB flush sends the burst back to LOOKUP)
//   RETRY   TLB missed while the walker was busy: wait until it is free
//   READY   physical burst valid (out_valid)
//   FAULT   translation fault; fault stays high until abort
// Because a burst never crosses 4 KB, one translation covers it.
// abort clears the stage; a walker answer that arrives later is ignored
// because it is accepted only in the WALK state.
module xdma_xlat_stage
  import xdma_pkg::*;
#(
  parameter int BW = 5
) (
  input  logic          clk,
  input  logic          rst_n,
  input  logic          translate_en,
  input  logic          abort,
  // burst in (from addr_gen)
  input  logic          in_valid,
  output logic          in_ready,
  input  logic [31:0]   in_va,
  input  logic [BW-1:0] in_beats,
  input  logic          in_last,
  // physical burst out
  output logic          out_valid,
  input  logic          out_ready,
  output logic [31:0]   out_pa,
  output logic [31:0]   out_va,
  output logic [BW-1:0] out_beats,
  output logic          out_last,
  output logic          fault,
  // TLB lookup
  output logic          tlb_req,
  output logic [19:0]   tlb_vpn,
  input  logic          tlb_gnt,
  input  logic          tlb_resp_valid,
  input  xl_kind_e      tlb_resp_kind,
  input  logic [19:0]   tlb_resp_ppn,
  // walker answer for this channel
  input  logic          walk_resp_valid,
  input  logic          walk_resp_ok,
  input  logic          walk_resp_stale,   // a TLB flush overtook the walk
  input  logic [19:0]   walk_resp_ppn,
  input  logic          walk_busy
);

  typedef enum logic [2:0] {X_EMPTY, X_LOOKUP, X_RESP, X_WALK, X_RETRY, X_READY, X_FAULT} xstate_e;
  xstate_e st;

  assign out_valid = (st == X_READY);
  assign fault     = (st == X_FAULT);
  assign tlb_req   = (st == X_LOOKUP);
  assign tlb_vpn   = out_va[31:12];
  assign in_ready  = !abort && ((st == X_EMPTY) || (st == X_READY && out_ready));

  wire load = in_valid && in_ready;

  always_ff @(posedge clk) begin
    if (!rst_n || abort) begin
      st <= X_EMPTY;
    end else if (load) begin
      st <= translate_en ? X_LOOKUP : X_READY;
    end else begin
      unique case (st)
        X_LOOKUP: if (tlb_gnt) st <= X_RESP;
        X_RESP: if (tlb_resp_valid) begin
          unique case (tlb_resp_kind)
            XL_HIT:   st <= X_READY;
            XL_FAULT: st <= X_FAULT;
            XL_WALK:  st <= X_WALK;
            default:  st <= X_RETRY;
          endcase
        end
        X_WALK:  if (walk_resp_valid) st <= walk_resp_stale ? X_RETRY :
                                             (walk_resp_ok ? X_READY : X_FAULT);
        X_RETRY: if (!walk_busy) st <= X_LOOKUP;
        X_READY: if (out_ready) st <= X_EMPTY;
        default: ;
      endcase
    end
  end

  // Payload registers (enable-coded, no reset).
  always_ff @(posedge clk) begin
    if (load) begin
      out_va    <= in_va;
      out_pa    <= in_va;                 // bypass; overwritten on a hit
      out_beats <= in_beats;
      out_last  <= in_last;
    end else if (st == X_RESP && tlb_resp_valid && tlb_resp_kind == XL_HIT) begin
      out_pa[31:12] <= tlb_resp_ppn;
    end else if (st == X_WALK && walk_resp_valid && walk_resp_ok && !walk_resp_stale) begin
      out_pa[31:12] <= walk_resp_ppn;
    end
  end

endmodule
