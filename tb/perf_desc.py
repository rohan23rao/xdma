"""perf_desc: descriptor overhead (spec 16). One MM2S channel moves 4 KB
either as one 4 KB descriptor or as a chain of 64 x 64 B descriptors, memory
latency XDMA_PERF_LAT (default 50). Run with PREFETCH_EN = 1 and 0. Reports
end-to-end utilization and the mean gap, in cycles, between the last beat
of one descriptor and the first beat of the next on the stream."""
import os

import cocotb

from common import LONG_TIMEOUT, params, rng
from env import BUSY_CYC, BYTES, XdmaEnv
from models.axi_mem import MemConfig
from models.ref_model import Desc
from perfutil import write_result
from scoreboard import ChannelJob


@cocotb.test(**LONG_TIMEOUT)
async def perf_desc(dut):
    r = rng()
    p = params()
    lat = int(os.environ.get("XDMA_PERF_LAT", "50"))
    env = XdmaEnv(dut, r, MemConfig(latency=lat, max_rd=64, max_wr=64))
    await env.start()
    res = dict(latency=lat, prefetch=p["PREFETCH_EN"])
    for name, n, size in (("1x4KB", 1, 4096), ("64x64B", 64, 64)):
        chain = []
        addrs = [0x4000 + 32 * i for i in range(n)]
        for i in range(n):
            chain.append((addrs[i], Desc(addr=0x0100_0000 + 0x1_0000 * len(res) + size * i,
                                          line_bytes=size, nxt=addrs[i + 1] if i + 1 < n else 0,
                                          last=i == n - 1, eop=True)))
        job = ChannelJob(env, 0, chain)
        m0 = len(env.mons[0].beats)
        await job.run(timeout=2_000_000)
        await env.quiesce()
        await job.check()
        util = await env.ch_rd(0, BYTES) / (8 * await env.ch_rd(0, BUSY_CYC))
        beats = env.mons[0].beats[m0:]
        gaps = [beats[i + 1][0] - beats[i][0] - 1 for i in range(len(beats) - 1) if beats[i][2]]
        res[name] = dict(util_e2e=util, mean_gap=sum(gaps) / len(gaps) if gaps else None)
    dut._log.info(f"perf_desc {res}")
    write_result(res)
