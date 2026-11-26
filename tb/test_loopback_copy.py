"""test_loopback_copy: memory-to-memory copy, MM2S channel 0 looped into
S2MM channel 2 (the first S2MM channel). The source and destination are cut
into different descriptor chains of random sizes; only the last descriptor
of each chain has EOP, so TLAST appears exactly once, at the end."""
import cocotb

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import XdmaEnv
from models.desc_builder import Allocator
from models.ref_model import Desc
from scoreboard import ChannelJob, check_memory, run_jobs


def split(r, total, max_parts):
    """Cut total bytes (multiple of 8) into random 8-byte-multiple parts."""
    cuts = sorted(r.sample(range(1, total // 8), min(max_parts - 1, total // 8 - 1))) if total > 8 else []
    edges = [0] + [8 * c for c in cuts] + [total]
    return [b - a for a, b in zip(edges, edges[1:])]


def chain_for(r, a, sizes, region):
    out, off = [], 0
    addrs = [a.alloc(32, align=32) for _ in sizes]
    for i, n in enumerate(sizes):
        last = i == len(sizes) - 1
        out.append((addrs[i], Desc(addr=region + off, line_bytes=n, lines=1, stride=0,
                                   nxt=0 if last else addrs[i + 1], last=last, eop=last,
                                   irq=r.random() < 0.3)))
        off += n
    return out


@cocotb.test(**TIMEOUT)
async def test_loopback_copy(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r))
    await env.start()
    dut.loop_en.value = 1
    a = Allocator(r)
    s2mm = p["NUM_MM2S"]
    for _ in range(4):
        total = 8 * r.choice([1, r.randint(2, 64), r.randint(64, 4096)])
        src, dst = a.alloc(total, near_4k=r.random() < 0.5), a.alloc(total, near_4k=r.random() < 0.5)
        rd_chain = chain_for(r, a, split(r, total, r.randint(1, 6)), src)
        wr_chain = chain_for(r, a, split(r, total, r.randint(1, 6)), dst)
        rd_job = ChannelJob(env, 0, rd_chain)
        src_bytes = env.mem.read(src, total)
        wr_job = ChannelJob(env, s2mm, wr_chain, stream_bytes=src_bytes, drive=False)
        before = env.mem.snapshot()
        await run_jobs(env, [rd_job, wr_job])
        check_memory(env, before, [rd_job, wr_job])
        if env.mem.read(dst, total) != src_bytes:
            fail("destination differs from source after copy")
