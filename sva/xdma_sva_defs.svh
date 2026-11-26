// Shared assertion macros. Properties are written as boolean expressions
// (implication spelled !a || b, history via $past) so the same files run in
// simulation (Verilator) and in the open-source formal flow (yosys-slang),
// which does not accept sequence operators such as |-> or ##N.
//
// XA   : assertion about the design.
// XASM : constraint on the module's inputs. An assumption under FORMAL; in
//        simulation it is checked as an assertion against the real neighbour.
// XC   : cover point.
//
// XDMA_OFF: in simulation, Verilator evaluates the model once at time 0 with
// randomized inputs (+verilator+rand+reset+2) before the testbench can drive
// rst_n low, so checks are also disabled at time 0.
`ifndef XDMA_SVA_DEFS_SVH
`define XDMA_SVA_DEFS_SVH
`ifdef FORMAL
`define XDMA_OFF (!rst_n)
`else
`define XDMA_OFF (!rst_n || $time == 0)
`endif
`define XA(name, expr)   name: assert property (@(posedge clk) disable iff (`XDMA_OFF) (expr));
`define XC(name, expr)   name: cover  property (@(posedge clk) disable iff (`XDMA_OFF) (expr));
`ifdef FORMAL
`define XASM(name, expr) name: assume property (@(posedge clk) disable iff (`XDMA_OFF) (expr));
`else
`define XASM(name, expr) name: assert property (@(posedge clk) disable iff (`XDMA_OFF) (expr));
`endif
`endif
