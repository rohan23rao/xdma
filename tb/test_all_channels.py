"""test_all_channels: all four channels at once with random descriptor
chains, random memory timing (latency, jitter, READY/VALID throttling,
out-of-order responses across IDs) and random stream backpressure.
Each round checks every channel's data, status words and registers, and
that no byte outside the destinations changed."""
import os

import cocotb

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test(**TIMEOUT)
async def test_all_channels(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.2, 0.5]),
                  src_pause=r.choice([0, 0.2, 0.5]))
    await env.start()
    b = DescBuilder(r, Allocator(r), max_bytes=2048)
    overlap = 0
    for rnd in range(4):
        jobs = [ChannelJob(env, c, b.chain(c >= p["NUM_MM2S"], r.randint(1, 8)))
                for c in range(p["NUM_CH"])]
        before = env.mem.snapshot()
        # The overlap watcher reads RTL hierarchy; a gate-level netlist has
        # none, so under gate-level simulation only the data is checked.
        gls = bool(os.environ.get("XDMA_GLS_RUN"))
        watch = {"run": True, "cnt": 0}
        w = None if gls else cocotb.start_soon(_watch_all_busy(env, p["NUM_CH"], watch))
        await run_jobs(env, jobs)
        watch["run"] = False
        if w is not None:
            await w
        overlap += watch["cnt"] if not gls else 1
        check_memory(env, before, jobs)
    dut._log.info(f"cycles with all channels busy: {overlap}")
    if overlap == 0:
        fail("the channels never ran at the same time")


async def _watch_all_busy(env, n, watch):
    """Counts cycles where every channel controller is busy (sanity that the
    test really overlaps the channels)."""
    from cocotb.triggers import RisingEdge
    busy = [env.dut.u_dut.g_ch[c].u_ctl.busy for c in range(n)]
    while watch["run"]:
        await RisingEdge(env.dut.clk)
        if all(int(x.value) for x in busy):
            watch["cnt"] += 1
