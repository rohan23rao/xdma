"""test_2d: 2D descriptors with positive, negative and zero strides, both
directions (zero stride on MM2S only: it repeats a line)."""
import cocotb

from common import params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test()
async def test_2d(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.3]), src_pause=r.choice([0, 0.3]))
    await env.start()
    b = DescBuilder(r, Allocator(r))
    for c in range(p["NUM_CH"]):
        write = c >= p["NUM_MM2S"]
        kinds = ["contig", "gap", "neg"] + ([] if write else ["zero"])
        for kind in kinds:
            d = b.desc(write, kind=kind)
            if d.lines == 1:
                d.lines = r.randint(2, 8)
                d.line_bytes = min(d.line_bytes, 1024)
                d.stride = {"contig": d.line_bytes, "gap": d.line_bytes + 8 * r.randint(1, 100),
                            "neg": -(d.line_bytes + 8 * r.randint(0, 100)), "zero": 0}[kind]
                d.addr = b.place(d.line_bytes, d.lines, d.stride)
            d.last = True
            job = ChannelJob(env, c, [(b.alloc.alloc(32, align=32), d)])
            before = env.mem.snapshot()
            await run_jobs(env, [job])
            check_memory(env, before, [job])
