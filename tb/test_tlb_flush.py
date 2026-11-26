"""test_tlb_flush: remapping pages and TLB_FLUSH (spec 5.1, 8.2).

For one channel at a time (MM2S and S2MM):
1. map a VA buffer (at most 4 pages, so it stays in the 8-entry TLB) and
   run a transfer: the TLB now caches the mapping;
2. remap every page to new physical pages WITHOUT a flush and run again:
   the cached (old) mapping must still be used;
3. TLB_FLUSH, run again: the new mapping must be used, and the walker must
   have re-read the page tables.
"""
import cocotb

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import TLB_STATS, XdmaEnv
from models.ref_model import Desc
from scoreboard import ChannelJob, check_memory, run_jobs
from xlat_util import XlatSetup


class FixedXlat:
    """Translation through a frozen copy of the page map (what the TLB holds)."""

    def __init__(self, maps):
        self.maps = dict(maps)

    def translate(self, va, write):
        pg = self.maps.get(va & ~0xFFF)
        return (pg | (va & 0xFFF), None) if pg is not None else (None, "unmapped")


@cocotb.test(**TIMEOUT)
async def test_tlb_flush(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r))
    await env.start()
    xs = XlatSetup(env, r)
    await xs.enable()
    for c in range(p["NUM_CH"]):
        write = c >= p["NUM_MM2S"]
        await xs.flush()
        va = xs.valloc.alloc(8192) & ~0xFFF
        va += 8 * r.randint(0, 511)
        nbytes = 8 * r.randint(1, 1024)            # <= 8 KB: spans at most 3 pages
        pages = sorted({(va + o) & ~0xFFF for o in range(0, nbytes, 8)})

        def job():
            return [(xs.palloc.alloc(32, align=32), Desc(addr=va, line_bytes=nbytes, last=True))]

        for pg in pages:
            xs.pt.map(pg)
        old = FixedXlat({pg: xs.pt.maps[pg] for pg in pages})
        j1 = ChannelJob(env, c, job(), xlat=old)
        await run_jobs(env, [j1])

        for pg in pages:                           # remap, no flush
            xs.pt.map(pg)
        new = FixedXlat({pg: xs.pt.maps[pg] for pg in pages})
        j2 = ChannelJob(env, c, job(), xlat=old)   # TLB still holds the old mapping
        before = env.mem.snapshot()
        misses0 = (await env.rd(TLB_STATS)) >> 16
        await run_jobs(env, [j2])
        check_memory(env, before, [j2])
        if (await env.rd(TLB_STATS)) >> 16 != misses0:
            fail(f"ch{c}: TLB missed on pages it should still hold")

        await xs.flush()
        j3 = ChannelJob(env, c, job(), xlat=new)
        before = env.mem.snapshot()
        await run_jobs(env, [j3])
        check_memory(env, before, [j3])
        misses = ((await env.rd(TLB_STATS)) >> 16) - misses0
        if misses != len(pages):
            fail(f"ch{c}: {misses} misses after the flush, expected {len(pages)} (one per page)")
