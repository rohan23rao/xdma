// xdma_csr: AXI4-Lite slave, register file, interrupt logic and counters.
//
// Register map: spec section 5. Unmapped reads return 0, unmapped writes are
// ignored, every response is OKAY. Writes honour WSTRB byte lanes.
// Write channel: AW and W are captured independently; the write is performed
// once both are held and no B response is pending. Read channel: one read in
// flight; RDATA is registered.
// Channel state that the channel controller owns (BUSY, ERR, CUR_DESC,
// ERR_ADDR) is read straight from it. Counters (BYTES, BUSY_CYC, STALL_CYC,
// descriptors completed) live here and clear when a START is accepted.
// Invariants: bvalid/rvalid hold until accepted; irq ==
// |(IRQ_STATUS & IRQ_MASK); START pulses only when EN = 1 and the channel is
// idle.
module xdma_csr #(
  parameter int NUM_MM2S    = 2,
  parameter int NUM_S2MM    = 2,
  parameter int MAX_BURST   = 16,
  parameter int TLB_ENTRIES = 8,
  parameter int DATA_W      = 64,
  localparam int NUM_CH = NUM_MM2S + NUM_S2MM
) (
  input  logic               clk,
  input  logic               rst_n,
  // AXI4-Lite slave
  input  logic               s_axil_awvalid,
  output logic               s_axil_awready,
  input  logic [11:0]        s_axil_awaddr,
  input  logic               s_axil_wvalid,
  output logic               s_axil_wready,
  input  logic [31:0]        s_axil_wdata,
  input  logic [3:0]         s_axil_wstrb,
  output logic               s_axil_bvalid,
  input  logic               s_axil_bready,
  output logic [1:0]         s_axil_bresp,
  input  logic               s_axil_arvalid,
  output logic               s_axil_arready,
  input  logic [11:0]        s_axil_araddr,
  output logic               s_axil_rvalid,
  input  logic               s_axil_rready,
  output logic [31:0]        s_axil_rdata,
  output logic [1:0]         s_axil_rresp,
  // global control
  output logic               translate_en,
  output logic [19:0]        pt_root,
  output logic               tlb_flush,
  input  logic               tlb_hit_inc,
  input  logic               tlb_miss_inc,
  output logic               irq,
  // per channel control
  output logic [NUM_CH-1:0]  ch_start,
  output logic [NUM_CH-1:0]  ch_abort,
  output logic [31:0]        ch_desc_head [NUM_CH],
  // per channel status (owned by the channel controller)
  input  logic [NUM_CH-1:0]  ch_busy,
  input  logic [2:0]         ch_err      [NUM_CH],
  input  logic [31:0]        ch_cur_desc [NUM_CH],
  input  logic [31:0]        ch_err_addr [NUM_CH],
  // per channel events
  input  logic [7:0]         ev_bytes    [NUM_CH],  // bytes moved this cycle
  input  logic [NUM_CH-1:0]  ev_stall,
  input  logic [NUM_CH-1:0]  ev_desc_done,
  input  logic [NUM_CH-1:0]  ev_done_irq,
  input  logic [NUM_CH-1:0]  ev_err_irq
);

  // ---------------------------------------------------------------- registers
  logic [31:0]        irq_status, irq_mask, cycles;
  logic [15:0]        tlb_hits, tlb_misses;
  logic [NUM_CH-1:0]  ch_en;
  logic [31:0]        ch_bytes   [NUM_CH];
  logic [31:0]        ch_busy_cyc[NUM_CH];
  logic [31:0]        ch_stall   [NUM_CH];
  logic [23:0]        ch_ndesc   [NUM_CH];

  // ---------------------------------------------------------- write channel
  logic        aw_have, w_have;
  logic [11:0] aw_addr_q;
  logic [31:0] w_data_q;
  logic [3:0]  w_strb_q;

  assign s_axil_awready = !aw_have;
  assign s_axil_wready  = !w_have;
  assign s_axil_bresp   = 2'b00;

  wire do_write = aw_have && w_have && !s_axil_bvalid;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      aw_have       <= 1'b0;
      w_have        <= 1'b0;
      s_axil_bvalid <= 1'b0;
    end else begin
      if (s_axil_awvalid && s_axil_awready) aw_have <= 1'b1;
      if (s_axil_wvalid && s_axil_wready)   w_have  <= 1'b1;
      if (do_write) begin
        aw_have       <= 1'b0;
        w_have        <= 1'b0;
        s_axil_bvalid <= 1'b1;
      end else if (s_axil_bvalid && s_axil_bready) begin
        s_axil_bvalid <= 1'b0;
      end
    end
  end

  always_ff @(posedge clk) begin
    if (s_axil_awvalid && s_axil_awready) aw_addr_q <= {s_axil_awaddr[11:2], 2'b00};
    if (s_axil_wvalid && s_axil_wready) begin
      w_data_q <= s_axil_wdata;
      w_strb_q <= s_axil_wstrb;
    end
  end

  // Byte-lane mask and the effective write value for RW registers.
  logic [31:0] wmask;
  always_comb begin
    for (int b = 0; b < 4; b++) wmask[8*b +: 8] = {8{w_strb_q[b]}};
  end
  function automatic logic [31:0] merge(logic [31:0] old);
    return (old & ~wmask) | (w_data_q & wmask);
  endfunction
  wire [31:0] wbits = w_data_q & wmask;   // bits written as 1 (W1C / W1S)

  // Address decode helpers.
  function automatic logic is_ch(logic [11:0] a, int c, logic [5:0] off);
    return a == 12'(32'h100 + 32'h40 * c + 32'(off));
  endfunction

  // ----------------------------------------------------- global registers
  logic [31:0] gctrl_new, pt_root_new;
  assign gctrl_new   = merge({31'b0, translate_en});
  assign pt_root_new = merge({12'b0, pt_root});

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      translate_en <= 1'b0;
      pt_root      <= '0;
      irq_mask     <= '0;
      tlb_flush    <= 1'b0;
    end else begin
      tlb_flush <= do_write && (aw_addr_q == 12'h008) && wbits[1];
      if (do_write && aw_addr_q == 12'h008) translate_en <= gctrl_new[0];
      if (do_write && aw_addr_q == 12'h00C) pt_root      <= pt_root_new[19:0];
      if (do_write && aw_addr_q == 12'h014) irq_mask     <= merge(irq_mask);
    end
  end

  // IRQ_STATUS: set by events, W1C by software; a set in the same cycle wins.
  logic [31:0] irq_set;
  always_comb begin
    irq_set = '0;
    for (int c = 0; c < NUM_CH; c++) begin
      irq_set[c]     = ev_done_irq[c];
      irq_set[8 + c] = ev_err_irq[c];
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      irq_status <= '0;
      irq        <= 1'b0;
      cycles     <= '0;
      tlb_hits   <= '0;
      tlb_misses <= '0;
    end else begin
      irq_status <= (irq_status & ~((do_write && aw_addr_q == 12'h010) ? wbits : 32'b0)) | irq_set;
      irq        <= |(irq_status & irq_mask);
      cycles     <= cycles + 1'b1;
      if (tlb_hit_inc  && tlb_hits   != 16'hFFFF) tlb_hits   <= tlb_hits + 1'b1;
      if (tlb_miss_inc && tlb_misses != 16'hFFFF) tlb_misses <= tlb_misses + 1'b1;
    end
  end

  // ---------------------------------------------------- channel registers
  for (genvar c = 0; c < NUM_CH; c++) begin : g_ch
    wire wr_ctrl = do_write && is_ch(aw_addr_q, c, 6'h00);
    wire [31:0] ctrl_new = merge({31'b0, ch_en[c]});
    wire [31:0] head_new = merge(ch_desc_head[c]);
    // START uses the EN value being written in the same access.
    wire start_ok = wr_ctrl && wbits[1] && ctrl_new[0] && !ch_busy[c];

    always_ff @(posedge clk) begin
      if (!rst_n) begin
        ch_en[c]        <= 1'b0;
        ch_desc_head[c] <= '0;
        ch_start[c]     <= 1'b0;
        ch_abort[c]     <= 1'b0;
        ch_bytes[c]     <= '0;
        ch_busy_cyc[c]  <= '0;
        ch_stall[c]     <= '0;
        ch_ndesc[c]     <= '0;
      end else begin
        ch_start[c] <= start_ok;
        ch_abort[c] <= wr_ctrl && wbits[2];
        if (wr_ctrl) ch_en[c] <= ctrl_new[0];
        // DESC_HEAD is 32-byte aligned: bits [4:0] read as zero.
        if (do_write && is_ch(aw_addr_q, c, 6'h04))
          ch_desc_head[c] <= {head_new[31:5], 5'b0};
        if (ch_start[c]) begin
          ch_bytes[c]    <= '0;
          ch_busy_cyc[c] <= '0;
          ch_stall[c]    <= '0;
          ch_ndesc[c]    <= '0;
        end else begin
          ch_bytes[c]    <= ch_bytes[c] + 32'(ev_bytes[c]);
          ch_busy_cyc[c] <= ch_busy_cyc[c] + 32'(ch_busy[c]);
          ch_stall[c]    <= ch_stall[c] + 32'(ev_stall[c]);
          ch_ndesc[c]    <= ch_ndesc[c] + 24'(ev_desc_done[c]);
        end
      end
    end
  end

  // ----------------------------------------------------------- read channel
  // Registers are decoded on the 32-bit word address; the byte offset in
  // AxADDR[1:0] only matters through WSTRB.
  logic [31:0] rd_mux;
  wire  [11:0] ar_word = {s_axil_araddr[11:2], 2'b00};
  always_comb begin
    rd_mux = '0;
    case (ar_word)
      12'h000: rd_mux = xdma_pkg::XDMA_ID_VALUE;
      12'h004: rd_mux = {8'(DATA_W / 8), 8'(TLB_ENTRIES), 8'(MAX_BURST), 4'(NUM_S2MM), 4'(NUM_MM2S)};
      12'h008: rd_mux = {31'b0, translate_en};
      12'h00C: rd_mux = {12'b0, pt_root};
      12'h010: rd_mux = irq_status;
      12'h014: rd_mux = irq_mask;
      12'h018: rd_mux = cycles;
      12'h01C: rd_mux = {tlb_misses, tlb_hits};
      default: rd_mux = '0;
    endcase
    for (int c = 0; c < NUM_CH; c++) begin
      if (is_ch(ar_word, c, 6'h00)) rd_mux = {31'b0, ch_en[c]};
      if (is_ch(ar_word, c, 6'h04)) rd_mux = ch_desc_head[c];
      if (is_ch(ar_word, c, 6'h08)) rd_mux = {ch_ndesc[c], 4'b0, ch_err[c], ch_busy[c]};
      if (is_ch(ar_word, c, 6'h0C)) rd_mux = ch_cur_desc[c];
      if (is_ch(ar_word, c, 6'h10)) rd_mux = ch_err_addr[c];
      if (is_ch(ar_word, c, 6'h14)) rd_mux = ch_bytes[c];
      if (is_ch(ar_word, c, 6'h18)) rd_mux = ch_busy_cyc[c];
      if (is_ch(ar_word, c, 6'h1C)) rd_mux = ch_stall[c];
    end
  end

  assign s_axil_arready = !s_axil_rvalid;
  assign s_axil_rresp   = 2'b00;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      s_axil_rvalid <= 1'b0;
    end else if (s_axil_arvalid && s_axil_arready) begin
      s_axil_rvalid <= 1'b1;
    end else if (s_axil_rready) begin
      s_axil_rvalid <= 1'b0;
    end
  end

  always_ff @(posedge clk) begin
    if (s_axil_arvalid && s_axil_arready) s_axil_rdata <= rd_mux;
  end

endmodule
