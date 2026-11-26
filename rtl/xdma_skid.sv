// xdma_skid: 2-entry skid buffer with registered valid and ready.
//
// Breaks the combinational path between the producer (s_*) and consumer
// (m_*). s_ready comes straight from a flop (!skid_valid), m_valid and m_data
// come straight from flops, so no combinational path exists from m_ready to
// m_valid or from s_valid to s_ready.
// Invariants: m_valid/m_data hold stable while m_valid && !m_ready; every
// accepted input beat appears on the output exactly once, in order.
// clr drops both entries (only for internal input-side buffers whose
// contents are being discarded; never used on a master output).
module xdma_skid #(
  parameter int WIDTH = 64
) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             clr,
  input  logic             s_valid,
  output logic             s_ready,
  input  logic [WIDTH-1:0] s_data,
  output logic             m_valid,
  input  logic             m_ready,
  output logic [WIDTH-1:0] m_data
);

  logic             skid_valid;
  logic [WIDTH-1:0] skid_data;

  assign s_ready = !skid_valid;

  wire s_hs = s_valid && s_ready;
  wire out_free = !m_valid || m_ready;   // output register can take new data

  always_ff @(posedge clk) begin
    if (!rst_n || clr) begin
      m_valid    <= 1'b0;
      skid_valid <= 1'b0;
    end else if (out_free) begin
      // Output register reloads from the skid entry first, else from input.
      m_valid    <= skid_valid || s_hs;
      skid_valid <= 1'b0;
    end else if (s_hs) begin
      // Output is stalled: park the new beat in the skid entry.
      skid_valid <= 1'b1;
    end
  end

  always_ff @(posedge clk) begin
    if (out_free) begin
      if (skid_valid)  m_data <= skid_data;
      else if (s_hs)   m_data <= s_data;
    end
    if (!out_free && s_hs) skid_data <= s_data;
  end

endmodule
