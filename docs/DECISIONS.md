# Design decisions

Decisions where the spec was silent or ambiguous, deviations from the spec,
and root causes of every bug found by formal or by a failing seed.
Numbered so the README and the RTL comments can refer to them.

## Toolchain and flow

**D-001 Open-source flow instead of VCS / JasperGold / DC / PT.**
No commercial EDA tool or license was available on the build machine, so
the owner chose a fully open-source flow. Simulation is Verilator + cocotb;
formal is SymbiYosys (yosys-slang front end, Yices k-induction / BMC and ABC
PDR engines); synthesis is Yosys mapped to the ASAP7 RVT typical-corner
library, with OpenSTA for timing and power (`make syn`, `syn/yosys/`).
Synthesis was first dropped from scope and then brought back by the owner
with this open-source flow; every synthesis number is labelled
"Yosys + OpenSTA, ASAP7, post-synthesis". The RTL stays within constructs
Synopsys Design Compiler accepts so the owner can rerun DC/PT later.
Consequences:
- `formal/sby/*.sby` replace `formal/jg/*.tcl` (one file per target, tasks
  for prove / bmc and cover).
- yosys-slang accepts only boolean concurrent properties with `$past`; it
  rejects `|->`, `|=>` and `##N`. Every property under `sva/` is written in
  that subset (`!a || b` for implication) so the same files run in
  simulation and in formal.
- Coverage uses Verilator line/toggle/user coverage instead of VCS URG.
- Waves are FST (GTKWave/Surfer) instead of FSDB.
- There is no Innovus step; all physical numbers are post-synthesis.

**D-004 Formal effort is bounded.** The owner asked for less time on formal
proofs and more on simulation-based verification. Targets that PDR or
k-induction close in minutes are full proofs; a target that does not
converge within about ten minutes runs as a bounded check (BMC) and is
reported as "bounded, depth N", never as proven (w_mux: PDR still open after
10 minutes, so BMC to depth 20).

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

## Step 6 independent review (fresh reviewer, RTL vs SPEC.md)

Every finding of the review and what was done with it.

**B-004 (review 1) W waited for AWREADY: AXI4 violation, possible deadlock.**
The W-order queue was pushed on the bus AW handshake (as spec 7.4 step 3
literally says), so WVALID could not rise before AWREADY. AXI4 A3.3.1 lets a
slave wait for WVALID before asserting AWREADY; such a slave deadlocks with
that master. **D-030:** the queue is now pushed when the AW arbiter accepts
the request into the AW skid buffer. The skid keeps AW order, so W bursts
still leave in AW order, and W may now lead its AW on the bus. The memory
model gained `aw_wait_w` (AWREADY only after WVALID) and accepts W before
AW; random tests enable it 30% of the time. With the old push point and
`aw_wait_w` forced, `test_s2mm_1d` hangs until its timeout fails it; with
the fix it passes.

**B-005 (review 2) CSR function read module-level signals.** `merge()` read
`w_data_q` and `wmask` without taking them as arguments and was called from
continuous assignments, which event-driven simulators (VCS) do not
re-evaluate when only those signals change; synthesis would build the
intended logic, so RTL and gates could disagree. Verilator evaluates the
body each time, which hid it. Fix: every input is an argument.

**B-006 (review 3) One discarded MM2S beat leaked after a drain.** In the
flush cycle `dp_drain` is already low while the FIFO clear lands at the
clock edge, so one FIFO beat was popped into the output skid and counted as
live (and, if it was the tagged last beat, could complete the next
descriptor early). Fix: no FIFO pop in the flush cycle.

**B-007 (review 13) S2MM took up to two beats past a descriptor.** TREADY
came from a register of the RUN state, so the input skid accepted beats of
the next packet; they were lost if the descriptor then failed. Fix:
interface-side beat counters; TREADY is high only while the current
descriptor still has beats to accept, and is still driven only by
registers.

**D-031 (review 14) STALL_CYC.** A cycle counts as a stall when the channel
is BUSY and: its AR/AW request is not granted, or an MM2S burst is ready but
blocked on FIFO space, or MM2S stream backpressure, or an S2MM beat is
blocked because the FIFO is full. Waiting for the MAX_OUT_CH limit, for a
TLB answer or for a page walk is not counted. Counted only while BUSY.

**D-032 (review 6) `irq` is combinational** from the IRQ_STATUS and
IRQ_MASK registers, exactly `|(IRQ_STATUS & IRQ_MASK)`, no extra cycle.

**D-033 (review 19) IRQ_MASK keeps only implemented bits** ([c] and [8+c]
for each channel); reserved bits read 0. Writing START and ABORT together
starts the channel (ABORT is ignored while idle). Clearing EN while running
has no effect; EN only gates START.

**D-034 (review 20) Enable-coded counters.** BYTES, BUSY_CYC, STALL_CYC and
the descriptor count only load on their event, so synthesis can gate them.
The RREADY/BREADY flops stay in xdma_top (two flops; a module of their own
would add nothing). `xdma_xlat_stage` is an extra module not in the spec's
table: the per-channel translation stage both data paths share.

**D-035 (review 5) TRANSLATE_EN = 0 is a 0-cycle bypass of translation.**
The burst still passes the stage's holding register, which is a pipeline
register present in both modes (it breaks the addr_gen min() -> reservation
compare -> arbiter path the spec lists as critical). It adds one cycle at the
start of each descriptor and no throughput loss: a new burst loads in the
same cycle the previous one is granted.

**D-036 (review 11, 12) Parameter limits.** Descriptor IDs are NUM_CH + c
and the walker ID is 2*NUM_CH (the spec's map at NUM_CH = 4, without the
overlap the spec's literal map would have for NUM_CH > 4). ADDR_W and DATA_W
are fixed at 32 and 64 (the datapaths are written for them). FIFO_DEPTH must
be >= MAX_BURST. Simulation stops at elaboration if ID_W cannot hold
2*NUM_CH (`xdma_top_sva`).

**D-037 (review 7) Abort mid-packet on MM2S.** Up to two beats already in
the output skid are still delivered (VALID cannot be withdrawn); the packet
is left without TLAST. Software that aborts a packet must resynchronize the
consumer. The spec does not define this.

**D-038 (review 8) ABORT during WB / WB_WAIT.** The writeback in flight
completes normally (descriptor written back DONE, counted, DONE irq if
CTRL.IRQ), then the channel stops with ERR = ABORTED and the ERROR bit.

**D-039 (review 9) Error writeback sets DONE = 1 with ERR != 0:** the engine
is finished with that descriptor; ERR says how. Software must check ERR.

**D-040 (review 10) DESC_HEAD[4:0] read as zero;** descriptors are 32-byte
aligned so the low bits are not stored.

**D-041 (review 15, 16) TLB statistics and flush.** A hit counts on HIT and
on a permission FAULT; a miss counts when a walk starts; a lookup that is
told to RETRY (walker busy) is not counted, and if its retry hits an entry
another channel's walk just filled it counts as a hit. TLB_FLUSH, like
TRANSLATE_EN and PT_ROOT, is only defined while every channel is idle (a
walk in flight would fill after the flush).

**D-042 (review 17) ERR_ADDR:** DESC_INVALID / DESC_FETCH: descriptor
address. RD_RESP / WR_RESP: start address of the failing burst (physical).
Status writeback failure: descriptor address + 0x18. XLAT_FAULT: faulting
VA. STREAM_LEN and ABORTED: address of the current descriptor.

**D-043 (review 18) DONE interrupt per descriptor.** CTRL.IRQ sets the DONE
bit when that descriptor completes (section 6 table), not only at the end of
the chain as the 7.1 diagram suggests.

**D-044 (review 21)** WSTRB is 0xFF for every beat, including the status
writeback (the spec's wording contradicts itself; both readings end in a
full-beat write).

**OPEN (review 4) Prefetch "no bubble beyond writeback latency".** With
prefetch the next descriptor's read latency is gone, but its first data read
still starts only after the writeback B returns, because the spec's state
machine runs RUN -> WRITEBACK -> (FETCH) -> RUN in sequence and RUN of the
next descriptor may not start before the writeback completes. The gap
between descriptors is therefore writeback latency + first-read latency.
Meeting the stated requirement exactly needs the next descriptor's data to
overlap the previous writeback, which the state machine forbids. Raised
with the owner; current RTL follows the state machine.
