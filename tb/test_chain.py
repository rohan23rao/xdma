"""test_chain: descriptor chains of 1..64 on every channel, run one channel
at a time. Checks every status writeback word, the descriptor count, and
that the next descriptor was prefetched: its fetch AR is on the bus before
the writeback AW of the descriptor before it."""
import cocotb

from common import fail, params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from scoreboard import ChannelJob, check_memory, run_jobs


@cocotb.test()
async def test_chain(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    b = DescBuilder(r, Allocator(r), max_bytes=1024)
    n_ch = p["NUM_CH"]
    for c in range(n_ch):
        for n in (1, r.randint(2, 8), r.choice([r.randint(9, 32), 64])):
            chain = b.chain(c >= p["NUM_MM2S"], n)
            job = ChannelJob(env, c, chain)
            ar0, aw0 = len(env.axi.ar_log), len(env.axi.aw_log)
            before = env.mem.snapshot()
            await run_jobs(env, [job])
            check_memory(env, before, [job])
            if p["PREFETCH_EN"] and n > 1:
                fetch = {addr: cyc for cyc, aid, addr, _ in env.axi.ar_log[ar0:] if aid == n_ch + c}
                wb = {addr - 0x18: cyc for cyc, aid, addr, _ in env.axi.aw_log[aw0:] if aid == n_ch + c}
                for (da, _), (na, _) in zip(chain, chain[1:]):
                    if not fetch[na] < wb[da]:
                        fail(f"ch{c}: descriptor {na:#x} fetched at cycle {fetch[na]}, not before "
                             f"the writeback of {da:#x} at cycle {wb[da]} (no prefetch)")
