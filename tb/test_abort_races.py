"""test_abort_races: ABORT in cycle-exact corner cases (spec 7.1, 10;
DECISIONS D-038, D-048, D-051, D-052). An AXI-Lite write cannot be placed
on an exact cycle, so the ABORT pulse is driven by depositing the CSR
block's one-cycle `ch_abort` register in the chosen cycle.

Cases (MM2S channel 0, chains of 2 descriptors and of 1):
- done:    ABORT in the cycle the descriptor's last beat completes;
- wb:      ABORT while the status writeback AW is being requested (S_WB);
Expected: the descriptor completes and is written back DONE. If it was LAST
the chain ends with ERR = NONE, otherwise with ERR = ABORTED and ERR_ADDR /
CUR_DESC naming the next (not started) descriptor.
- stalled: ABORT while the stream sink is stalled with beats in the output
  skid buffer. Those beats are delivered after the channel is idle (AXI-Stream
  VALID cannot be withdrawn) but not counted: CSR BYTES equals the error
  writeback's STATUS.BYTES (D-051).
"""
import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import BYTES, CUR_DESC, ERR_ADDR, IRQ_STATUS, XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import ERR_ABORTED, ERR_NONE, Desc, status_word
from scoreboard import ChannelJob, check_memory, run_jobs

S_WB = 3


async def abort_when(env, c, cond, limit=200000):
    """Pulse ch_abort[c] for one cycle in the first cycle cond() holds.
    Values read in the RisingEdge callback are still the pre-edge ones, so
    cond() is evaluated 1 ns into the cycle, on the settled values, and the
    deposit then holds for the rest of that cycle."""
    csr = env.dut.u_dut.u_csr
    for _ in range(limit):
        await RisingEdge(env.dut.clk)
        await Timer(1, unit="ns")
        if cond():
            csr.ch_abort.value = 1 << c
            await ReadOnly()
            return True
    return False


@cocotb.test(**TIMEOUT)
async def test_abort_races(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r, throttle=False))
    await env.start()
    a = Allocator(r)
    b = DescBuilder(r, a, max_bytes=1024)
    c = 0
    ctl = dut.u_dut.g_ch[c].u_ctl
    rd = dut.u_dut.g_ch[c].g_mm2s.u_rd
    for case in ("done", "wb"):
        for n in (2, 1):
            chain = b.chain(False, n, irq=False)
            job = ChannelJob(env, c, chain)
            run = cocotb.start_soon(job.run())
            if case == "done":
                # dp_done fires in this cycle: tagged beat presented, sink ready
                cond = lambda: int(ctl.dp_done.value) == 1 and int(ctl.state.value) == 2
            else:
                cond = lambda: int(ctl.state.value) == S_WB
            if not await abort_when(env, c, cond):
                fail(f"{case}: abort window never seen")
            if not (int(ctl.abort.value) and int(ctl.state.value) == (2 if case == "done" else S_WB)):
                fail(f"{case}: the ABORT pulse missed its cycle")
            await run
            await env.quiesce()
            err = (job.st >> 1) & 7
            d0 = chain[0]
            if env.mem.read64(d0[0] + 0x18) != status_word(1, ERR_NONE, d0[1].nbytes()):
                fail(f"{case}/{n}: descriptor 0 not written back as done")
            if job.st >> 8 != 1:
                fail(f"{case}/{n}: {job.st >> 8} descriptors counted, expected 1")
            if n == 1:
                if err != ERR_NONE:
                    fail(f"{case}: ABORT after the last data of the chain gave ERR={err}")
            else:
                if err != ERR_ABORTED:
                    fail(f"{case}: expected ABORTED, got ERR={err}")
                nxt = chain[1][0]
                if await env.ch_rd(c, ERR_ADDR) != nxt or await env.ch_rd(c, CUR_DESC) != nxt:
                    fail(f"{case}: ERR_ADDR / CUR_DESC do not name the next descriptor {nxt:#x}")
                if env.mem.read64(nxt + 0x18) != 0:
                    fail(f"{case}: the not-started descriptor was written back")
            await env.wr(IRQ_STATUS, 0xFFFF_FFFF)

    # ---- stalled sink: beats stuck in the output skid at the abort
    d = Desc(addr=a.alloc(8192), line_bytes=4096, lines=1, last=True)
    job = ChannelJob(env, c, [(a.alloc(32, align=32), d)])
    env.set_sink_pause(c, 0)
    snk = env.sinks[c]
    run = cocotb.start_soon(job.run())
    for _ in range(100000):                      # let some data flow, then stall
        await RisingEdge(dut.clk)
        if len(env.mons[c].beats) - job.mon_start >= 40:
            break
    snk.pause = True
    for _ in range(1000):
        await RisingEdge(dut.clk)
        if int(rd.m_axis_tvalid.value) and int(rd.skid_full.value):
            break
    await env.abort(c)
    while int(ctl.state.value) != 0:
        await RisingEdge(dut.clk)
    stalled_beats = len(env.mons[c].beats) - job.mon_start
    snk.pause = False
    await run
    await env.quiesce()
    delivered = len(env.mons[c].beats) - job.mon_start
    status = env.mem.read64(job.chain[0][0] + 0x18)
    nbytes = await env.ch_rd(c, BYTES)
    if (job.st >> 1) & 7 != ERR_ABORTED:
        fail("stalled: expected ABORTED")
    if status >> 32 != nbytes:
        fail(f"stalled: CSR BYTES {nbytes} != written-back STATUS.BYTES {status >> 32}")
    if not 1 <= delivered - stalled_beats <= 2:
        fail(f"stalled: {delivered - stalled_beats} beats delivered after the drain, expected 1..2 "
             "(the output skid contents)")
    if 8 * stalled_beats != nbytes:
        fail(f"stalled: {stalled_beats} beats seen while running but BYTES = {nbytes}")
    for i, ((_, dd, _), (ed, _)) in enumerate(zip(env.mons[c].beats[job.mon_start:], job.exp.stream)):
        if dd != ed:
            fail(f"stalled: beat {i} data mismatch")
    # the channel is reusable afterwards
    again = ChannelJob(env, c, b.chain(False, 2))
    before = env.mem.snapshot()
    await run_jobs(env, [again])
    check_memory(env, before, [again])
    dut._log.info(f"stalled: {stalled_beats} beats counted, {delivered - stalled_beats} stale beats delivered")
