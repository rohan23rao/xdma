// xdma_pkg: shared types and constants for the XDMA engine.
//
// Holds the error codes (spec section 10), the decoded descriptor struct
// (section 6), AXI constants (section 4) and small helper functions.
// Engine parameters live on xdma_top so a testbench can override them.
package xdma_pkg;

  // Error codes, STATUS.ERR and descriptor STATUS.ERR
  typedef enum logic [2:0] {
    ERR_NONE         = 3'd0,
    ERR_DESC_INVALID = 3'd1,
    ERR_DESC_FETCH   = 3'd2,
    ERR_RD_RESP      = 3'd3,
    ERR_WR_RESP      = 3'd4,
    ERR_XLAT_FAULT   = 3'd5,
    ERR_STREAM_LEN   = 3'd6,
    ERR_ABORTED      = 3'd7
  } err_e;

  localparam logic [31:0] XDMA_ID_VALUE = 32'h5844_4D41;  // "XDMA"

  // AXI constants
  localparam logic [1:0] RESP_OKAY   = 2'b00;
  localparam logic [2:0] AXI_SIZE_8B = 3'b011;
  localparam logic [1:0] AXI_INCR    = 2'b01;
  localparam logic [3:0] AXI_CACHE   = 4'b0011;

  // Decoded descriptor (words 0..2; word 3 is written by the engine)
  typedef struct packed {
    logic [31:0] next;
    logic [31:0] addr;
    logic [15:0] line_bytes;
    logic [15:0] lines;
    logic [31:0] stride;
    logic        last;
    logic        irq;
    logic        eop;
  } desc_t;

  // Translation lookup result kinds (TLB -> channel)
  typedef enum logic [1:0] {
    XL_HIT   = 2'd0,  // translation valid, permission OK
    XL_FAULT = 2'd1,  // permission fault on a hit, or walk fault
    XL_WALK  = 2'd2,  // miss; walker started for this channel
    XL_RETRY = 2'd3   // miss while walker busy; ask again later
  } xl_kind_e;

  // Section 6 validation. Returns 1 if the descriptor is legal.
  function automatic logic desc_ok(desc_t d);
    return (d.addr[2:0] == 3'b0) && (d.line_bytes[2:0] == 3'b0) &&
           (d.stride[2:0] == 3'b0) && (d.line_bytes != 16'd0) &&
           (d.lines != 16'd0) && (d.last || d.next[4:0] == 5'b0);
  endfunction

endpackage
