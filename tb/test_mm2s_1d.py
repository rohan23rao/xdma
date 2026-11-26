"""test_mm2s_1d: single 1D descriptors on each MM2S channel (spec 14.2).
Sizes from one beat to 8 KB, random start addresses (some just below a 4 KB
boundary), random EOP/IRQ, random memory timing and stream backpressure."""
import cocotb

from common import TIMEOUT, params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test(**TIMEOUT)
async def test_mm2s_1d(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.3]))
    await env.start()
    b = DescBuilder(r, Allocator(r))
    for c in range(p["NUM_MM2S"]):
        for _ in range(6):
            d = b.desc(write=False)
            d.lines, d.stride, d.line_bytes = 1, 0, r.choice([8, 128, 8 * r.randint(1, 1024)])
            d.addr = b.place(d.line_bytes, 1, 0)
            d.last = True
            chain = [(b.alloc.alloc(32, align=32), d)]
            job = ChannelJob(env, c, chain)
            before = env.mem.snapshot()
            await run_jobs(env, [job])
            check_memory(env, before, [job])
