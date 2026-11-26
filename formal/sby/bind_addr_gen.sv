bind xdma_addr_gen xdma_addr_gen_sva #(.MAX_BURST(MAX_BURST), .LB_W(LB_W), .LN_W(LN_W)) u_sva (.*);
bind xdma_addr_gen xdma_fv_reset u_fv_reset (.*);
