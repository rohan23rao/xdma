"""perf_latency: one MM2S channel streaming a 64 KB transfer, memory with a
fixed latency (XDMA_PERF_LAT cycles, no jitter, no throttling), stream sink
always ready. Measures steady-state and end-to-end bandwidth utilization.
Used by the latency-tolerance and burst-length sweeps (spec 16).
Pass threshold (spec 16): >= 90% at latency 50 with default parameters."""
import os

import cocotb
from cocotb.triggers import ClockCycles

from common import LONG_TIMEOUT, fail, params, rng
from env import BUSY_CYC, BYTES, XdmaEnv
from models.axi_mem import MemConfig
from models.ref_model import Desc
from perfutil import CycleCounter, snapshot, window, write_result
from scoreboard import ChannelJob

TOTAL = 64 * 1024


@cocotb.test(**LONG_TIMEOUT)
async def perf_latency(dut):
    r = rng()
    p = params()
    lat = int(os.environ.get("XDMA_PERF_LAT", "50"))
    env = XdmaEnv(dut, r, MemConfig(latency=lat, max_rd=64, max_wr=64))
    await env.start()
    cc = CycleCounter(dut, p["NUM_MM2S"], p["NUM_CH"])
    line = TOTAL // 4
    d = Desc(addr=0x0100_0000, line_bytes=line, lines=4, stride=line, last=True)
    job = ChannelJob(env, 0, [(0x2000, d)])
    beats = TOTAL // 8
    run = cocotb.start_soon(job.run(timeout=2_000_000))
    while cc.beats[0] < beats // 10:
        await ClockCycles(dut.clk, 20)
    s0 = await snapshot(env, cc, 0)
    while cc.beats[0] < beats * 9 // 10:
        await ClockCycles(dut.clk, 20)
    s1 = await snapshot(env, cc, 0)
    await run
    await env.quiesce()
    await job.check()
    hw, tb = window(s0, s1)
    e2e = await env.ch_rd(0, BYTES) / (8 * await env.ch_rd(0, BUSY_CYC))
    res = dict(latency=lat, fifo_depth=p["FIFO_DEPTH"], max_out_ch=p["MAX_OUT_CH"],
               max_burst=p["MAX_BURST"], util_hw=hw, util_tb=tb, util_e2e=e2e,
               window_cycles=s1[0] - s0[0])
    dut._log.info(f"perf_latency {res}")
    write_result(res)
    if abs(hw - tb) > 0.01 * max(tb, 1e-9):
        fail(f"hardware counters ({hw:.4f}) and testbench ({tb:.4f}) disagree by more than 1%")
    if os.environ.get("XDMA_PERF_CHECK") and lat == 50 and hw < 0.90:
        fail(f"utilization {hw:.3f} < 0.90 at latency 50")
