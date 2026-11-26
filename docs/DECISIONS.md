# Design decisions

Decisions where the spec was silent or ambiguous, deviations from the spec,
and root causes of every bug found by formal or by a failing seed.
Numbered so the README and the RTL comments can refer to them.

## Toolchain and flow

**D-001 Open-source flow instead of VCS / JasperGold / DC / PT.**
No commercial EDA tool or license was available on the build machine. The
owner chose a fully open-source flow and asked for synthesis to be left out.
Simulation is Verilator + cocotb, formal is SymbiYosys (yosys-slang front end,
Yices k-induction and ABC PDR engines). Consequences:
- `formal/sby/*.sby` replace `formal/jg/*.tcl` (one file per target, tasks
  for prove and cover).
- yosys-slang accepts only boolean concurrent properties with `$past`; it
  rejects `|->`, `|=>` and `##N`. Every property under `sva/` is written in
  that subset (`!a || b` for implication) so the same files run in
  simulation and in formal.
- No synthesis, timing, area or power numbers exist; RESULTS.md says so
  instead of filling them in. `make syn` / `make pnr` are not provided.
- Coverage uses Verilator line/toggle/user coverage instead of VCS URG.
- Waves are FST (GTKWave/Surfer) instead of FSDB.

**D-002 Simulation assertions are disabled at time 0.**
Verilator evaluates the model once at time 0, with randomized top-level
inputs (`+verilator+rand+reset+2`), before cocotb can drive `rst_n` low. A
random `rst_n = 1` at that instant fired combinational checks on random
state. Checks are disabled while `$time == 0` in simulation only; formal
forces reset in the first cycle instead (`formal/sby/xdma_fv_reset.sv`).

**D-003 Input constraints are `XASM`.**
A property about a module's inputs is an `assume` in that module's formal
proof and an `assert` in simulation, where the real neighbour drives it. So a
proof's assumptions are checked in every simulation of the full design.

## Building blocks

**D-010 FIFO and skid buffer have a synchronous `clr`.**
Needed to discard data when a channel drains (spec 7.1 DRAIN). `clr` is used
on internal FIFOs and on the S2MM input skid buffer only, never on a buffer
that drives a master VALID, so no AXI rule is broken.

**D-011 Round-robin arbiter pointer moves only on a handshake.**
`hs` is an input from the consumer; with no handshake the same requester
keeps the grant (unless it drops its request). This is what makes the
starvation bound hold when the consumer stalls.

## Bugs found and their root causes

**B-001 addr_gen: 4 KB limit truncated in `min()` (found by formal).**
The burst size compared `rem`, `MAX_BURST` and `to_4k` at `LBB+1` bits
(line-length width + 1). `to_4k` can be 512; with the reduced formal width
`LB_W = 9` (`LBB+1 = 7` bits) 512 truncated to 0 and the burst length became
0. At the default `LB_W = 16` the width is 14 bits and the bug is latent, but
the module is parameterized and the bug is real. Fix: compare at
`max(LBB+1, 10)` bits. Found by `a_ag_beats_min` in `addr_gen_prove_b16`.

**B-002 CSR: sub-word writes ignored (found by test_csr).**
The CSR block compared the full AWADDR with the register offset. A byte
write from cocotbext-axi puts the byte's address on AWADDR (e.g. 0x016 for
lane 2 of IRQ_MASK), so the write matched no register. AXI4-Lite allows an
unaligned AxADDR with the lane selected by WSTRB. Fix: decode on the word
address (`AxADDR[11:2]`) for both reads and writes.

**B-003 Status writeback BYTES one beat short (found by test_mm2s_1d).**
The controller latched the status word on `dp_done`, but the data path
counts the descriptor's final beat in that same cycle, so BYTES missed the
last beat (0 for a one-beat descriptor). Fix: latch the status word in the
WB state, one cycle later; the W mux cannot read it before the writeback AW
has been granted and accepted on the bus.

## Testbench

**D-020 S2MM stream source is our own driver.** cocotbext-axi's
AxiStreamSource always ends a frame with TLAST and cannot send a bad TKEEP,
so tests could not build EOP = 0 descriptors or STREAM_LEN errors with it.
`env.StreamDriver` drives any beat pattern with random gaps. cocotbext-axi is
still used for the AXI4-Lite master and the MM2S stream sinks.

**D-021 No test-only descriptor backdoor.** Step 4 of the spec suggests
loading the first MM2S descriptor through a backdoor before the fetcher
exists. The fetcher was written together with the data path, so every test
goes through the real fetch path instead.
