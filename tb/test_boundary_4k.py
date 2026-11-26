"""test_boundary_4k: lines that straddle 4 KB boundaries. Checks every
burst the engine issues (address and length, in order) against the section
7.2 splitter, for both directions, and the data."""
import cocotb

from common import fail, params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import Desc
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test()
async def test_boundary_4k(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r))
    await env.start()
    a = Allocator(r)
    for c in range(p["NUM_CH"]):
        write = c >= p["NUM_MM2S"]
        for _ in range(4):
            lb = 8 * r.randint(2, 600)
            lines = r.randint(1, 5)
            stride = lb + 8 * r.randint(0, 64) if r.random() < 0.7 else 4096 + 8 * r.randint(-4, 4)
            stride = max(stride, lb)
            base = a.alloc(stride * lines + lb + 8192)
            addr = (base // 4096 + 1) * 4096 - 8 * r.randint(1, 16)   # within 128 B below 4 KB
            d = Desc(addr=addr, line_bytes=lb, lines=lines, stride=stride, last=True, eop=True)
            job = ChannelJob(env, c, [(a.alloc(32, align=32), d)])
            log = env.axi.aw_log if write else env.axi.ar_log
            n0 = len(log)
            before = env.mem.snapshot()
            await run_jobs(env, [job])
            check_memory(env, before, [job])
            got = [(addr_, beats) for _, aid, addr_, beats in log[n0:] if aid == c]
            want = [(va, nb) for va, nb, _ in d.bursts(p["MAX_BURST"])]
            if got != want:
                fail(f"ch{c}: bursts {[(hex(x), n) for x, n in got][:8]}... expected "
                     f"{[(hex(x), n) for x, n in want][:8]}... ({len(got)} vs {len(want)})")
