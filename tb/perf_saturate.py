"""perf_saturate: all four channels saturating at once (spec 16 fairness;
also the activity source for power analysis). Memory latency 50, no
throttling, sinks always ready, sources always valid. Each channel moves a
long transfer; shares are measured in a window where all four are busy.
Pass threshold: each channel within +-5% of the equal share of its
direction (the two MM2S channels share R, the two S2MM channels share W)."""
import os

import cocotb
from cocotb.triggers import ClockCycles

from common import LONG_TIMEOUT, fail, params, rng
from env import XdmaEnv
from models.axi_mem import MemConfig
from models.ref_model import Desc
from perfutil import CycleCounter, snapshot, window, write_result
from scoreboard import ChannelJob

TOTAL = int(os.environ.get("XDMA_PERF_TOTAL", 32 * 1024))


@cocotb.test(**LONG_TIMEOUT)
async def perf_saturate(dut):
    r = rng()
    p = params()
    n = p["NUM_CH"]
    env = XdmaEnv(dut, r, MemConfig(latency=int(os.environ.get("XDMA_PERF_LAT", "50")),
                                    max_rd=64, max_wr=64))
    await env.start()
    cc = CycleCounter(dut, p["NUM_MM2S"], n)
    jobs = []
    for c in range(n):
        line = TOTAL // 4
        d = Desc(addr=0x0100_0000 + c * 0x10_0000, line_bytes=line, lines=4, stride=line, last=True)
        jobs.append(ChannelJob(env, c, [(0x2000 + 0x100 * c, d)]))
    beats = TOTAL // 8
    tasks = [cocotb.start_soon(j.run(timeout=2_000_000)) for j in jobs]
    while min(cc.beats) < beats // 10:
        await ClockCycles(dut.clk, 20)
    s0 = [await snapshot(env, cc, c) for c in range(n)]
    while max(cc.beats) < beats * 7 // 10:
        await ClockCycles(dut.clk, 20)
    s1 = [await snapshot(env, cc, c) for c in range(n)]
    for t in tasks:
        await t
    await env.quiesce()
    for j in jobs:
        await j.check()
    utils = [window(a, b) for a, b in zip(s0, s1)]
    byts = [(b[2] - a[2]) & 0xFFFF_FFFF for a, b in zip(s0, s1)]
    groups = {"mm2s": list(range(p["NUM_MM2S"])), "s2mm": list(range(p["NUM_MM2S"], n))}
    shares = {}
    for g, chans in groups.items():
        tot = sum(byts[c] for c in chans)
        for c in chans:
            shares[c] = byts[c] / tot if tot else 0.0
    res = dict(util_hw=[u[0] for u in utils], util_tb=[u[1] for u in utils], bytes=byts,
               share_in_direction=[shares[c] for c in range(n)],
               share_total=[b / sum(byts) for b in byts])
    dut._log.info(f"perf_saturate {res}")
    write_result(res)
    for c, (hw, tb) in enumerate(utils):
        if abs(hw - tb) > 0.01 * max(tb, 1e-9):
            fail(f"ch{c}: hardware ({hw:.4f}) and testbench ({tb:.4f}) disagree by more than 1%")
    for g, chans in groups.items():
        eq = 1 / len(chans)
        for c in chans:
            if abs(shares[c] - eq) > 0.05 * eq:
                fail(f"ch{c}: share {shares[c]:.3f} of {g} bandwidth, equal is {eq:.3f} (+-5%)")
