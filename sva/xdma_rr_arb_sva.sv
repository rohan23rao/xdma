// Assertions for xdma_rr_arb. Bound into every arbiter instance.
`include "xdma_sva_defs.svh"
module xdma_rr_arb_sva #(
  parameter int N = 4,
  localparam int PW = (N > 1) ? $clog2(N) : 1
) (
  input logic          clk,
  input logic          rst_n,
  input logic [N-1:0]  req,
  input logic          hs,
  input logic [N-1:0]  grant,
  input logic [PW-1:0] grant_idx,
  input logic [PW-1:0] ptr
);
  `XA(a_arb_onehot,     $onehot0(grant))
  `XA(a_arb_only_req,   (grant & ~req) == '0)
  `XA(a_arb_work_conserving, (req == '0) || (grant != '0))
  `XA(a_arb_idx, (grant == '0) || (grant[grant_idx]))
  `XA(a_arb_ptr_range, int'(ptr) < N)

  // Starvation freedom: count handshakes that went to someone else while
  // requester i was waiting. It must never reach N.
  for (genvar i = 0; i < N; i++) begin : g_starve
    logic [7:0] waited;
    always_ff @(posedge clk) begin
      if (!rst_n || !req[i] || (hs && grant[i])) waited <= '0;
      else if (hs && (grant != '0))                waited <= waited + 1'b1;
    end
    `XA(a_arb_no_starve, int'(waited) < N)
    // Helper invariant (makes the proof inductive): every handshake that
    // passes over requester i moves ptr strictly closer to i, so the
    // distance from ptr to i plus the handshakes already waited is < N.
    `XA(a_arb_wait_bound, !req[i] || (((i - int'(ptr) + N) % N) + int'(waited) <= N - 1))
    `XC(c_arb_wait_max, int'(waited) == N - 1)
  end
endmodule
