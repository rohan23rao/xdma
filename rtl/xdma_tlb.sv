// xdma_tlb: fully associative TLB shared by all channels (spec 8.2).
//
// Entries {valid, vpn, ppn, r, w}. One lookup per cycle; channels request
// through a round-robin arbiter. The lookup compares in the grant cycle and
// the result is registered, so the answer reaches the channel one cycle
// after its grant:
//   HIT    entry found, permission OK (R for MM2S, W for S2MM)
//   FAULT  entry found, permission missing (no walk)
//   WALK   miss, walker idle: a walk starts for this channel
//   RETRY  miss, walker busy (or a fill landed last cycle): ask again later
// A miss while the walker is busy is not queued; the channel waits for the
// walker to go idle and looks up again. This keeps one walk at a time and
// lets other channels keep hitting (hit-under-miss). RETRY after a fill
// prevents a second walk for a page that was just filled, so the TLB never
// holds two entries for one VPN.
// Fills use a round-robin replacement pointer. flush clears every valid bit.
// Stats: a hit counts on HIT or FAULT, a miss counts when a walk starts;
// RETRY lookups are not counted.
module xdma_tlb #(
  parameter int NUM_MM2S = 2,
  parameter int NUM_CH   = 4,
  parameter int ENTRIES  = 8,
  localparam int CHW = (NUM_CH > 1) ? $clog2(NUM_CH) : 1,
  localparam int EW  = (ENTRIES > 1) ? $clog2(ENTRIES) : 1
) (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               flush,
  // lookup requests
  input  logic [NUM_CH-1:0]  req,
  input  logic [19:0]        vpn [NUM_CH],
  output logic [NUM_CH-1:0]  gnt,
  // registered answer
  output logic [NUM_CH-1:0]  resp_valid,
  output xdma_pkg::xl_kind_e resp_kind,
  output logic [19:0]        resp_ppn,
  // walker
  input  logic               walk_busy,
  output logic               walk_start,
  output logic [19:0]        walk_vpn,
  output logic [CHW-1:0]     walk_ch,
  output logic               walk_write,
  input  logic               fill_valid,
  input  logic [19:0]        fill_vpn,
  input  logic [19:0]        fill_ppn,
  input  logic               fill_r,
  input  logic               fill_w,
  // stats
  output logic               hit_inc,
  output logic               miss_inc
);
  import xdma_pkg::*;

  logic [ENTRIES-1:0] e_valid;
  logic [19:0]        e_vpn [ENTRIES];
  logic [19:0]        e_ppn [ENTRIES];
  logic [ENTRIES-1:0] e_r, e_w;
  logic [EW-1:0]      repl;

  // ---------------------------------------------------- lookup (cycle 1)
  logic [CHW-1:0] g_idx;
  xdma_rr_arb #(.N(NUM_CH)) u_arb (
    .clk, .rst_n, .req(req), .hs(|req), .grant(gnt), .grant_idx(g_idx)
  );

  logic [19:0]        s_vpn;
  logic [ENTRIES-1:0] match;
  logic [19:0]        m_ppn;
  logic               m_r, m_w;
  always_comb begin
    s_vpn = vpn[g_idx];
    m_ppn = '0;
    m_r   = 1'b0;
    m_w   = 1'b0;
    for (int e = 0; e < ENTRIES; e++) begin
      match[e] = e_valid[e] && (e_vpn[e] == s_vpn);
      if (match[e]) begin
        m_ppn = m_ppn | e_ppn[e];
        m_r   = m_r | e_r[e];
        m_w   = m_w | e_w[e];
      end
    end
  end

  // -------------------------------------------- registered result (cycle 2)
  logic           l_valid, l_hit, l_r, l_w, fill_q;
  logic [CHW-1:0] l_ch;
  logic [19:0]    l_vpn, l_ppn;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      l_valid <= 1'b0;
      fill_q  <= 1'b0;
    end else begin
      l_valid <= |req;
      fill_q  <= fill_valid;
    end
  end
  always_ff @(posedge clk) begin
    if (|req) begin
      l_ch  <= g_idx;
      l_vpn <= s_vpn;
      l_hit <= |match;
      l_ppn <= m_ppn;
      l_r   <= m_r;
      l_w   <= m_w;
    end
  end

  wire l_write = (int'(l_ch) >= NUM_MM2S);
  wire perm_ok = l_write ? l_w : l_r;

  always_comb begin
    resp_valid = '0;
    resp_valid[l_ch] = l_valid;
    resp_ppn   = l_ppn;
    if (l_hit)                    resp_kind = perm_ok ? XL_HIT : XL_FAULT;
    else if (walk_busy || fill_q) resp_kind = XL_RETRY;
    else                          resp_kind = XL_WALK;
  end

  assign walk_start = l_valid && !l_hit && !walk_busy && !fill_q;
  assign walk_vpn   = l_vpn;
  assign walk_ch    = l_ch;
  assign walk_write = l_write;
  assign hit_inc    = l_valid && l_hit;
  assign miss_inc   = walk_start;

  // ---------------------------------------------------------------- fill
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      e_valid <= '0;
      repl    <= '0;
    end else if (flush) begin
      e_valid <= '0;
    end else if (fill_valid) begin
      e_valid[repl] <= 1'b1;
      repl <= (repl == EW'(ENTRIES - 1)) ? '0 : repl + 1'b1;
    end
  end
  always_ff @(posedge clk) begin
    if (fill_valid) begin
      e_vpn[repl] <= fill_vpn;
      e_ppn[repl] <= fill_ppn;
      e_r[repl]   <= fill_r;
      e_w[repl]   <= fill_w;
    end
  end

endmodule
