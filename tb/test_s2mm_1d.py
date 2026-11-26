"""test_s2mm_1d: single 1D descriptors on each S2MM channel. Sizes from one
beat to 8 KB, EOP on and off, random stream gaps and memory timing."""
import cocotb

from common import params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test()
async def test_s2mm_1d(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), src_pause=r.choice([0, 0.3]))
    await env.start()
    b = DescBuilder(r, Allocator(r))
    for c in range(p["NUM_MM2S"], p["NUM_CH"]):
        for _ in range(6):
            d = b.desc(write=True)
            d.lines, d.stride, d.line_bytes = 1, 0, r.choice([8, 128, 8 * r.randint(1, 1024)])
            d.addr = b.place(d.line_bytes, 1, 0)
            d.last = True
            job = ChannelJob(env, c, [(b.alloc.alloc(32, align=32), d)])
            before = env.mem.snapshot()
            await run_jobs(env, [job])
            check_memory(env, before, [job])
