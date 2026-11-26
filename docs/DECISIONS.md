# Design decisions

Decisions where the spec was silent or ambiguous, deviations from the spec,
and root causes of every bug found by formal or by a failing seed.
Numbered so the README and the RTL comments can refer to them.

## Bug stories (how each was found and why it matters)

Short versions for interviews; the root cause and fix of each is in its
B-/D- entry below.

| Bug | How it was found | Why it matters |
|---|---|---|
| **B-001** addr_gen `min()` truncated the 4 KB limit | Formal: the 1..MAX_BURST assertion failed in the base case when the proof shrank LINE_BYTES to 9 bits | A width bug that random simulation at default widths can never hit. Reducing widths for tractability also changes which bugs exist; compare at the widest operand's width. |
| **B-002** CSR ignored sub-word writes | `test_csr` byte-strobe check: cocotbext-axi put the byte address (0x016) on AWADDR | AXI4-Lite allows unaligned AxADDR with WSTRB. A CSR that only decodes aligned addresses works with most drivers and fails silently with others. |
| **B-003** status BYTES one beat short | `test_mm2s_1d` scoreboard compared the written-back status word | An off-by-one-cycle capture (the counter updates in the same cycle as `done`). It's the classic "sample one cycle later" bug, and only an end-to-end check of software-visible state catches it. |
| **B-004** W waited for AWREADY (AXI4 deadlock) | Independent review against the spec, then confirmed with a new memory-model mode (AWREADY waits for WVALID): old RTL hangs, fixed RTL passes | Following the spec's wording ("push on AW handshake") produced a protocol violation the testbench could not see, because the memory accepted AW on its own. Lesson: model the strictest legal slave, not a friendly one. |
| **B-005** CSR helper function read module-level signals | Independent review (Verilator masks it) | A simulation/synthesis mismatch waiting for the first event-driven RTL sim (VCS). Functions should take all inputs as arguments; reviewers catch what one simulator cannot. |
| **B-006** discarded beat leaked after a drain | Independent review, by tracing the flush cycle | A one-cycle window where `drain` was already low but the FIFO had not yet cleared. It could also complete the *next* descriptor early. Corner cases live at state-transition edges. |
| **B-007** S2MM accepted beats past its descriptor | Independent review | Registered TREADY looked safe (no combinational path) but over-accepted, and those beats were lost if the descriptor then failed. Count at the interface you are controlling. |
| **B-008** TLB flush undone by an in-flight walk | Second independent review; reproduced by a directed race test (repoint the level-1 PTE mid-walk, then flush) | Cache coherence across a flush: any in-flight fill must be invalidated too. Without the fix the DMA silently read a page software had unmapped, which is a memory-safety bug. |
| **T-001** time-0 assertion failures | First Verilator runs: assertions fired at t = 0 on randomized power-up state | Verilator evaluates once before cocotb can drive reset. Checks must be off until reset is applied (D-002). |
| **T-002** loopback over-counted stream beats | `test_loopback_copy` beat count mismatch | The monitor sampled the sink's TREADY, but in loopback the real ready came from the S2MM port. A monitor must observe the handshake that actually happens. |
| **T-003** walker-error test expected one faulting page | `test_translate_fault` ERR_ADDR mismatch | A PTE read is an 8-byte word holding two PTEs, so an injected error faults both pages. The test, not the RTL, was wrong. |
| **T-004** soak hang | `test_soak` timeouts on 3 seeds | cocotbext-axi leaves a sink paused when its pause generator is replaced (D-047). The hang was in the testbench, which the root-cause analysis proved before any RTL change. |
| **T-005** deadlock showed up as a hang, not a failure | Re-running the old B-004 RTL | The S2MM driver blocked before the polling timeout. Every test now has a simulation-time limit, so a hang fails with the seed printed. |

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
reported as "bounded, depth N", never as proven. Where a full-size proof
does not converge, the target is reduced and the reduction is stated: w_mux
(PDR open after 10 minutes and BMC stuck past depth 15 at 4 writes in flight
with 16-beat bursts) is proven at 2 writes in flight with 1..4-beat bursts.
The same assertions run at full size in every simulation.

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

**D-038 (review 8, updated by review 3.2) ABORT during WB / WB_WAIT.** The
writeback in flight completes normally (descriptor written back DONE,
counted, DONE irq if CTRL.IRQ). If that descriptor was LAST the chain is
complete and the ABORT has nothing left to stop: the channel finishes with
ERR = NONE. Otherwise the channel stops before the next descriptor with
ERR = ABORTED, and ERR_ADDR / CUR_DESC name that next (not started)
descriptor, as an ABORT in FETCH does.

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

**D-045 (review 4) Prefetch keeps the 7.1 state machine (owner decision).**
The spec asked for "no bubble between consecutive descriptors beyond
writeback latency", but its own state machine runs RUN -> WRITEBACK -> RUN
in sequence, so the next descriptor's first data read can only start after
the writeback B returns. The owner chose to keep the state machine and
reworded SPEC.md 7.1: prefetch hides the descriptor fetch latency; the
remaining gap (status writeback round trip + first read latency) is
measured by the descriptor-overhead experiment and reported in RESULTS.md.
Overlapping the next descriptor with the writeback is in FUTURE.md.

## Step 7 and 8 testbench notes

**D-046 Walker AXI errors are tested in test_translate_fault** (kind
`walker_err`), next to the other translation faults, rather than in
test_axi_errors. A PTE read is one aligned 8-byte word that holds two
adjacent leaf PTEs, so an error on it faults both pages; the test accounts
for that.

**D-047 Soak hang root cause: testbench, not RTL.** Three soak seeds
timed out with an MM2S channel busy. cocotbext-axi's sink keeps its `pause`
flag at the last generated value when its pause generator is replaced, so a
round could leave TREADY stuck low forever. `env.set_sink_pause()` now
clears `pause` explicitly; the same seeds pass.

## Step 8 independent review (second fresh reviewer, RTL vs SPEC.md)

**B-008 (review 2.1) TLB_FLUSH undone by a walk in flight (bug).** A flush
cleared the valid bits, but a page walk already running filled its result
afterwards; a lookup compared in the flush cycle could also still return
HIT. Realistic case: the walker has read the level-1 PTE, software repoints
it at a new level-0 table and flushes, the walker then reads the old
level-0 table and caches the old physical page. Fix: a lookup compared in
the flush cycle answers RETRY; a walk that sees a flush while running is
marked stale, its result is not cached and its channel looks up again.
`test_tlb_flush` now builds exactly that race; with the fix disabled the
DMA reads the old page and the test fails, with it the data comes from the
new page.

**D-048 (review 2.2) ABORT in the cycle a descriptor completes.** Completion
wins: the descriptor is written back as done (counted, DONE irq if
CTRL.IRQ), then the channel stops with ABORTED, the same as an ABORT during
WB / WB_WAIT (D-038). A data error in that cycle still wins over both.

**D-049 (review 2.3) No descriptor read is launched in an ABORT cycle.** The
prefetch and the post-writeback fetch both ignore a cycle with ABORT or a
data error, so no spurious AR follows an abort.

**D-050 (review 2.14) Event byte count widened to 12 bits** so MAX_BURST up
to 256 cannot overflow it; simulation stops at elaboration if
FIFO_DEPTH < MAX_BURST (no burst could ever issue).

Other review 2 findings were already decided: translation bypass register
(D-035), stale MM2S beats after a drain (D-037), ID map (D-036), DONE = 1 on
error writeback (D-039), TLB_STATS (D-041), STALL_CYC (D-031), ERR_ADDR
(D-042), START with ABORT (D-033), DESC_HEAD low bits (D-040), xdma_top
glue (D-034). Review 2.16: s_axis_tready, RREADY/BREADY and the AXI-Lite
outputs come from flops (no combinational path from any input) rather than
through an xdma_skid instance; the spec's intent, no READY->VALID path and
registered outputs, holds. Review 2.5 also noted that with translation on a
single channel issues at most one burst per three cycles (LOOKUP, RESP,
READY); this caps 1-beat-burst traffic and is listed in FUTURE.md.

## Step 11 independent review (third fresh reviewer, RTL vs SPEC.md v1.1)

No deadlock, data loss or protocol violation found. Changes made:

**D-051 (review 3.1) MM2S byte counts only count beats delivered while the
descriptor runs.** Beats that leave the output skid during DRAIN or after
the flush are still delivered (VALID cannot be withdrawn, D-037) but are no
longer counted, so CSR BYTES, the descriptor's STATUS.BYTES and the
written-back status agree after an abort or error. Before, beats leaving in
the drain-exit and flush cycles were in BYTES but not in STATUS.BYTES.

**D-052 (review 3.2) ABORT after the last data of a chain has no effect**,
and an ABORT between descriptors reports the next, not-started descriptor
(D-038).

**D-053 (review 3.3) DRAIN waits for a page walk owned by the channel.** A
channel whose translation stage had started a walk could finish DRAIN and
report idle while the walker still issued PTE reads for it; software may
then change PT_ROOT or the page tables under the spec 5.1 "all channels
idle" rule. The translation stage now keeps `walk_pending` until the walker
answers (even after the stage is aborted), and the data path is idle only
when that flag is clear.

Review 3.9: simulation also stops at elaboration if NUM_CH > 8 (IRQ bits
[c] and [8+c] would overlap). Review 3.10: the writeback-capture comment in
xdma_desc_fetch now states the real (zero-cycle) margin. Review 3.12: SPEC
section 4 cross-reference fixed. Reviews 3.4-3.8 and 3.11 restate decided
choices: D-039 (DONE = 1 on error writeback), D-033 (START with ABORT),
D-042 (ERR_ADDR), D-041 (TLB_STATS), D-035 (bypass register), D-036
(fixed ADDR_W/DATA_W), review 2.16 (registered outputs not all through
xdma_skid).
