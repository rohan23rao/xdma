// Binds every assertion module into the design for simulation.
// Formal targets use their own bind files under formal/sby/.
bind xdma_fifo     xdma_fifo_sva     #(.WIDTH(WIDTH), .DEPTH(DEPTH)) u_sva (.*);
bind xdma_skid     xdma_skid_sva     #(.WIDTH(WIDTH)) u_sva (.*);
bind xdma_rr_arb   xdma_rr_arb_sva   #(.N(N)) u_sva (.*);
bind xdma_addr_gen xdma_addr_gen_sva #(.MAX_BURST(MAX_BURST), .LB_W(LB_W), .LN_W(LN_W)) u_sva (.*);
