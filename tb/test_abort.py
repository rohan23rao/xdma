"""test_abort: software ABORT (spec 7.1, 10) on every channel.

Cases, for both directions:
- fetch:  ABORT while the first descriptor read is outstanding (slow memory)
          -> ABORTED, no writeback, ERR_ADDR = that descriptor;
- run:    ABORT while data bursts are outstanding -> ABORTED, error
          writeback on the running descriptor;
- drain:  ABORT while already draining after an AXI error -> ignored, the
          original error is reported;
- random: ABORT at a random time -> either the chain finished first, or
          ABORTED with every earlier descriptor complete.
Every case must end with the channel idle (no hang) and the channel must
then run a normal chain correctly.
"""
import cocotb
from cocotb.triggers import RisingEdge

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import IRQ_STATUS, XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import ERR_ABORTED, ERR_NONE, ERR_RD_RESP, ERR_WR_RESP
from scoreboard import ChannelJob, check_error, check_memory, run_jobs

S_FETCH, S_RUN, S_DRAIN = 1, 2, 5


async def wait_state(env, c, state, limit=20000):
    for _ in range(limit):
        await RisingEdge(env.dut.clk)
        if env.ch_state(c) == state:
            return True
    return False


@cocotb.test(**TIMEOUT)
async def test_abort(dut):
    r = rng()
    p = params()
    cfg = rand_mem_cfg(r)
    env = XdmaEnv(dut, r, cfg, sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    b = DescBuilder(r, Allocator(r), max_bytes=8192)
    base_lat = cfg.latency
    seen = set()
    for c in range(p["NUM_CH"]):
        write = c >= p["NUM_MM2S"]
        for case in ["fetch", "run", "drain", "random", "random"]:
            n = r.randint(1, 4)
            chain = b.chain(write, n)
            if case in ("run", "drain"):
                d = chain[0][1]          # make the first descriptor long
                d.line_bytes, d.lines, d.stride = 4096, 2, 4096
                d.addr = b.place(4096, 2, 4096)
            job = ChannelJob(env, c, chain)
            before = env.mem.snapshot()
            cfg.latency = 150 if case in ("fetch", "drain") else base_lat
            if case == "drain":
                pa = chain[0][1].addr + 8 * r.randint(0, 64)
                (cfg.wr_err if write else cfg.rd_err).append((pa, pa + 8, 2))
            run = cocotb.start_soon(job.run())
            if case == "fetch":
                if not await wait_state(env, c, S_FETCH):
                    fail(f"ch{c}: never in FETCH")
                await env.abort(c)
            elif case == "run":
                if not await wait_state(env, c, S_RUN):
                    fail(f"ch{c}: never in RUN")
                await env.abort(c)
            elif case == "drain":
                if not await wait_state(env, c, S_DRAIN):
                    fail(f"ch{c}: never in DRAIN")
                await env.abort(c)
            else:
                for _ in range(r.randint(1, 600)):
                    await RisingEdge(dut.clk)
                await env.abort(c)
            state_at_abort = env.ch_state(c)
            await run
            await env.quiesce()
            cfg.latency = base_lat
            cfg.rd_err.clear()
            cfg.wr_err.clear()
            err = (job.st >> 1) & 7
            k = job.st >> 8
            dut._log.info(f"ch{c} {case}: abort in state {state_at_abort}, ERR={err}, {k}/{n} done")
            if case == "fetch":
                await check_error(env, job, ERR_ABORTED, 0, chain[0][0], writeback=False)
            elif case == "run":
                await check_error(env, job, ERR_ABORTED, 0, chain[0][0], writeback=True)
            elif case == "drain":
                await check_error(env, job, ERR_WR_RESP if write else ERR_RD_RESP, 0, writeback=True)
            elif err == ERR_NONE:
                await job.check()
            elif err == ERR_ABORTED:
                # the descriptor in flight was either never started (FETCH:
                # no writeback) or aborted mid-run (error writeback)
                # (ERR_ADDR is checked only for an aborted run: after an
                # abort during a writeback it names the finished descriptor)
                st = env.mem.read64(chain[k][0] + 0x18) if k < n else 0
                await check_error(env, job, ERR_ABORTED, k, chain[k][0] if st else None,
                                  writeback=st != 0)
            else:
                fail(f"ch{c}: unexpected ERR={err} after a random abort")
            seen.add((case, err))
            check_memory(env, before, [job])
            await env.wr(IRQ_STATUS, 0xFFFF_FFFF)
            again = ChannelJob(env, c, b.chain(write, r.randint(1, 3)))
            before = env.mem.snapshot()
            await run_jobs(env, [again])
            check_memory(env, before, [again])
    dut._log.info(f"outcomes: {sorted(seen)}")
