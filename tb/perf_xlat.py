"""perf_xlat: translation cost (spec 16). One MM2S channel, memory latency
50, 64 KB in three modes: translation off; on, linear (64 KB contiguous,
16 pages); on, STRIDE = 4096 (64 lines of 512 B, a new page every line).
Reports steady-state-free end-to-end utilization and the TLB hit rate
(TLB_STATS delta)."""
import os

import cocotb

from common import LONG_TIMEOUT, params, rng
from env import BUSY_CYC, BYTES, GCTRL, TLB_STATS, XdmaEnv
from models.axi_mem import MemConfig
from models.ref_model import Desc
from perfutil import write_result
from scoreboard import ChannelJob
from xlat_util import XlatSetup


@cocotb.test(**LONG_TIMEOUT)
async def perf_xlat(dut):
    r = rng()
    p = params()
    lat = int(os.environ.get("XDMA_PERF_LAT", "50"))
    env = XdmaEnv(dut, r, MemConfig(latency=lat, max_rd=64, max_wr=64))
    await env.start()
    xs = XlatSetup(env, r)
    res = dict(latency=lat)
    modes = [("off", None), ("linear", (16384, 4, 16384)), ("stride4096", (512, 64, 4096))]
    for i, (name, shape) in enumerate(modes):
        if name == "off":
            await env.wr(GCTRL, 0)
            d = Desc(addr=0x0200_0000, line_bytes=16384, lines=4, stride=16384, last=True)
            xl = None
        else:
            await xs.enable()
            await xs.flush()
            lb, lines, stride = shape
            va = 0x5000_0000 + i * 0x0100_0000
            d = Desc(addr=va, line_bytes=lb, lines=lines, stride=stride, last=True)
            xs.map_chain([(0, d)])
            xl = xs.xlat
        job = ChannelJob(env, 0, [(xs.palloc.alloc(32, align=32), d)], xlat=xl)
        t0 = await env.rd(TLB_STATS)
        await job.run(timeout=2_000_000)
        await env.quiesce()
        await job.check()
        t1 = await env.rd(TLB_STATS)
        hits, misses = (t1 & 0xFFFF) - (t0 & 0xFFFF), (t1 >> 16) - (t0 >> 16)
        util = await env.ch_rd(0, BYTES) / (8 * await env.ch_rd(0, BUSY_CYC))
        res[name] = dict(util_e2e=util, tlb_hits=hits, tlb_misses=misses,
                         hit_rate=hits / (hits + misses) if hits + misses else None)
    dut._log.info(f"perf_xlat {res}")
    write_result(res)
