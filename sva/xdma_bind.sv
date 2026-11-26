// Binds every assertion module into the design for simulation.
// Formal targets use their own bind files under formal/sby/.
bind xdma_fifo     xdma_fifo_sva     #(.WIDTH(WIDTH), .DEPTH(DEPTH)) u_sva (.*);
bind xdma_skid     xdma_skid_sva     #(.WIDTH(WIDTH)) u_sva (.*);
bind xdma_rr_arb   xdma_rr_arb_sva   #(.N(N)) u_sva (.*);
bind xdma_addr_gen xdma_addr_gen_sva #(.MAX_BURST(MAX_BURST), .LB_W(LB_W), .LN_W(LN_W)) u_sva (.*);
bind xdma_top xdma_top_sva #(.NUM_MM2S(NUM_MM2S), .NUM_S2MM(NUM_S2MM), .ID_W(ID_W),
  .MAX_BURST(MAX_BURST), .FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT_RD(MAX_OUT_RD), .MAX_OUT_WR(MAX_OUT_WR)) u_sva (.*);
bind xdma_rd_chan  xdma_rd_chan_sva  #(.FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT_CH(MAX_OUT_CH)) u_sva (.*);
bind xdma_wr_chan  xdma_wr_chan_sva  #(.FIFO_DEPTH(FIFO_DEPTH), .MAX_OUT_CH(MAX_OUT_CH)) u_sva (.*);
bind xdma_desc_fetch xdma_desc_fetch_sva u_sva (.*);
bind xdma_w_mux xdma_w_mux_sva #(.ID_W(ID_W), .MAX_OUT_WR(MAX_OUT_WR)) u_sva (.*);
