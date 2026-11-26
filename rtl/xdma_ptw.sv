// xdma_ptw: two-level page-table walker, one walk at a time (spec 8.1, 8.3).
//
// L1 PTE at (PT_ROOT << 12) + VPN1*4; L0 PTE at (PTE1.PPN << 12) + VPN0*4.
// Each PTE read is one single-beat AR (ARLEN 0) of the aligned 8-byte word on
// the walker ID; address bit 2 selects the 32-bit half.
// Faults: V = 0 at either level; R|W|X != 0 at level 1 (megapage, not
// supported); missing R (MM2S) or W (S2MM) on the leaf; any non-OKAY read
// response. On success the result fills the TLB and goes to the waiting
// channel; on a fault only that channel is told.
// busy is high from start until the cycle after done (DONE state).
module xdma_ptw #(
  parameter int NUM_CH = 4,
  localparam int CHW = (NUM_CH > 1) ? $clog2(NUM_CH) : 1
) (
  input  logic           clk,
  input  logic           rst_n,
  input  logic [19:0]    pt_root,
  input  logic           start,
  input  logic [19:0]    start_vpn,
  input  logic [CHW-1:0] start_ch,
  input  logic           start_write,
  output logic           busy,
  output logic           ar_req,
  output logic [31:0]    ar_addr,
  input  logic           ar_gnt,
  input  logic           r_valid,
  input  logic [63:0]    r_data,
  input  logic [1:0]     r_resp,
  output logic           done,
  output logic [CHW-1:0] done_ch,
  output logic           done_ok,
  output logic [19:0]    done_vpn,
  output logic [19:0]    done_ppn,
  output logic           done_r,
  output logic           done_w
);
  typedef enum logic [2:0] {P_IDLE, P_L1_AR, P_L1_R, P_L0_AR, P_L0_R, P_DONE} pstate_e;
  pstate_e st;

  logic [31:0] pte_addr;
  logic        write;

  assign busy    = (st != P_IDLE);
  assign ar_req  = (st == P_L1_AR) || (st == P_L0_AR);
  assign ar_addr = {pte_addr[31:3], 3'b000};
  assign done    = (st == P_DONE);

  wire [31:0] pte  = pte_addr[2] ? r_data[63:32] : r_data[31:0];
  wire        v    = pte[0];
  wire        rwx  = |pte[3:1];
  wire        resp_ok = (r_resp == 2'b00);
  wire        leaf_ok = write ? pte[2] : pte[1];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      st <= P_IDLE;
    end else begin
      unique case (st)
        P_IDLE:  if (start) st <= P_L1_AR;
        P_L1_AR: if (ar_gnt) st <= P_L1_R;
        P_L1_R:  if (r_valid) st <= (resp_ok && v && !rwx) ? P_L0_AR : P_DONE;
        P_L0_AR: if (ar_gnt) st <= P_L0_R;
        P_L0_R:  if (r_valid) st <= P_DONE;
        P_DONE:  st <= P_IDLE;
        default: st <= P_IDLE;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (st == P_IDLE && start) begin
      pte_addr <= {pt_root, start_vpn[19:10], 2'b00};
      done_vpn <= start_vpn;
      done_ch  <= start_ch;
      write    <= start_write;
      done_ok  <= 1'b0;
    end else if (st == P_L1_R && r_valid) begin
      pte_addr <= {pte[29:10], done_vpn[9:0], 2'b00};
    end else if (st == P_L0_R && r_valid) begin
      done_ok  <= resp_ok && v && leaf_ok;
      done_ppn <= pte[29:10];
      done_r   <= pte[1];
      done_w   <= pte[2];
    end
  end

endmodule
