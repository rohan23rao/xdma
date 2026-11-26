bind xdma_w_mux xdma_w_mux_sva #(.ID_W(ID_W), .MAX_OUT_WR(MAX_OUT_WR)) u_sva (.*);
bind xdma_w_mux xdma_fv_reset u_fv_reset (.*);
// The AW lengths the engine can produce: MAX_BURST <= 16 beats.
module xdma_w_mux_fv_env (input logic clk, input logic rst_n, input logic aw_push, input logic [7:0] aw_len);
  fv_len: assume property (@(posedge clk) !aw_push || aw_len < 8'd16);
endmodule
bind xdma_w_mux xdma_w_mux_fv_env u_fv_env (.*);
