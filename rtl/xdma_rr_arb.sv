// xdma_rr_arb: N-way round-robin arbiter with one-hot grant.
//
// grant is combinational from req and the priority pointer. The requester at
// or after ptr (wrapping) with the lowest index wins. When the caller reports
// a handshake (hs = the granted request was accepted this cycle) the pointer
// moves to one past the winner, so the winner becomes lowest priority.
// The pointer never moves without a handshake.
// Invariants: grant is one-hot or zero; grant only to an active requester;
// grant != 0 whenever req != 0; a requester held high is granted within N
// handshakes (starvation freedom).
module xdma_rr_arb #(
  parameter int N = 4,
  localparam int PW = (N > 1) ? $clog2(N) : 1
) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [N-1:0] req,
  input  logic         hs,
  output logic [N-1:0] grant,
  output logic [PW-1:0] grant_idx
);

  logic [PW-1:0] ptr;

  always_comb begin
    grant     = '0;
    grant_idx = '0;
    // Scan N positions starting at ptr; first active requester wins.
    for (int k = N - 1; k >= 0; k--) begin
      int unsigned idx;
      idx = (int'(ptr) + k) % N;
      if (req[idx]) begin
        grant     = '0;
        grant[idx] = 1'b1;
        grant_idx = PW'(idx);
      end
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ptr <= '0;
    end else if (hs && (|req)) begin
      ptr <= (grant_idx == PW'(N - 1)) ? '0 : grant_idx + 1'b1;
    end
  end

endmodule
