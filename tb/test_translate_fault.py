"""test_translate_fault: translation faults (spec 8, 10).

One channel per round gets a fault; the other three run translated chains
at the same time and must be unaffected. Fault kinds:
- l0_invalid  leaf PTE with V = 0
- l1_invalid  level-1 PTE with V = 0
- megapage    level-1 PTE with R set (unsupported superpage)
- perm        leaf without R (MM2S) or W (S2MM)
- perm_hit    the page is already in the TLB from the other direction
              without the needed permission: fault on the hit, no walk
- walker_err  SLVERR/DECERR on a PTE read
Expected: ERR = XLAT_FAULT, ERR_ADDR = VA of the first burst that touches a
bad page, earlier descriptors complete, error writeback on the faulting one.
"""
import cocotb

import fcov
from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import IRQ_STATUS, XdmaEnv
from models.axi_mem import DECERR, SLVERR
from models.desc_builder import Allocator, DescBuilder
from models.page_table import R, W
from models.ref_model import ERR_XLAT_FAULT, Desc
from scoreboard import ChannelJob, check_error, check_memory, run_jobs
from xlat_util import XlatSetup

KINDS = ["l0_invalid", "l1_invalid", "megapage", "perm", "perm_hit", "walker_err"]


def first_bad(chain, bad_pages, max_burst):
    """(descriptor index, VA) of the first burst touching a bad page."""
    for i, (_, d) in enumerate(chain):
        for va, _, _ in d.bursts(max_burst):
            if va & ~0xFFF in bad_pages:
                return i, va
    return None, None


@cocotb.test(**TIMEOUT)
async def test_translate_fault(dut):
    r = rng()
    p = params()
    cfg = rand_mem_cfg(r)
    env = XdmaEnv(dut, r, cfg, sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    xs = XlatSetup(env, r)
    await xs.enable()
    kinds = KINDS * 2
    r.shuffle(kinds)
    for rnd, kind in enumerate(kinds):
        c = r.randrange(p["NUM_CH"])
        write = c >= p["NUM_MM2S"]
        # the faulting channel's buffers get their own 4 MB region (own L1 PTE)
        region = 0xE000_0000 + rnd * 0x40_0000
        fb = DescBuilder(r, Allocator(r, region, region + 0x40_0000), max_bytes=2048, dalloc=xs.palloc)
        if kind == "perm_hit":
            # one page, primed by the opposite direction
            va = region + 0x1000 + 8 * r.randint(0, 64)
            nbytes = 8 * r.randint(1, 64)
            chain = [(xs.palloc.alloc(32, align=32), Desc(addr=va, line_bytes=nbytes, last=True))]
            page = va & ~0xFFF
            xs.pt.map(page, perm=W if not write else R)       # opposite permission only
            prime_c = p["NUM_MM2S"] if not write else 0
            prime = ChannelJob(env, prime_c, [(xs.palloc.alloc(32, align=32),
                                               Desc(addr=va, line_bytes=nbytes, last=True))], xlat=xs.xlat)
            await run_jobs(env, [prime])
            bad_pages = {page}
        else:
            chain = fb.chain(write, r.randint(1, 4))
            xs.map_chain(chain)
            pages = sorted({va & ~0xFFF for _, d in chain for va, _, _ in d.bursts(p["MAX_BURST"])})
            page = r.choice(pages)
            if kind == "l0_invalid":
                xs.pt.invalidate_l0(page)
                bad_pages = {page}
            elif kind == "perm":
                xs.pt.set_perm(page, (W if not write else R) | 1)
                bad_pages = {page}
            elif kind == "l1_invalid":
                xs.pt.invalidate_l1(page)
                bad_pages = set(pages)
            elif kind == "megapage":
                xs.pt.make_megapage(page)
                bad_pages = set(pages)
            else:  # walker_err on this page's leaf PTE read
                pa = xs.pt.pte_addr(page) & ~7
                cfg.rd_err.append((pa, pa + 8, r.choice([SLVERR, DECERR])))
                # one 8-byte PTE read covers two adjacent leaf PTEs
                bad_pages = {page, page ^ 0x1000}
        k, fva = first_bad(chain, bad_pages, p["MAX_BURST"])
        jobs = {ch: ChannelJob(env, ch, xs.chain(ch >= p["NUM_MM2S"], r.randint(1, 3)), xlat=xs.xlat)
                for ch in range(p["NUM_CH"]) if ch != c}
        jobs[c] = bad = ChannelJob(env, c, chain, xlat=xs.xlat)
        dut._log.info(f"{kind}: ch{c}, fault in descriptor {k} at VA {fva:#x}")
        fcov.sample("tlb", {"l0_invalid": "fault_invalid", "l1_invalid": "fault_invalid",
                            "megapage": "fault_megapage", "perm": "fault_perm",
                            "perm_hit": "fault_perm"}.get(kind, "miss"))
        ar0 = len(env.axi.ar_log)
        before = env.mem.snapshot()
        tasks = [cocotb.start_soon(j.run()) for j in jobs.values()]
        for t in tasks:
            await t
        await env.quiesce()
        for ch, j in jobs.items():
            if ch != c:
                await j.check()
        await check_error(env, bad, ERR_XLAT_FAULT, k, fva, writeback=True)
        check_memory(env, before, list(jobs.values()))
        if kind == "perm_hit":
            # the fault came from the TLB hit: no walk read that page's PTE
            pte = xs.pt.pte_addr(page) & ~7
            walks = [a for a in env.axi.ar_log[ar0:] if a[1] == 2 * p["NUM_CH"] and a[2] == pte]
            if walks:
                fail(f"perm_hit: page {page:#x} was walked ({len(walks)} PTE reads) instead of faulting on the hit")
        cfg.rd_err.clear()
        await env.wr(IRQ_STATUS, 0xFFFF_FFFF)
        # channel reusable (translation still on)
        again = ChannelJob(env, c, xs.chain(write, r.randint(1, 2)), xlat=xs.xlat)
        before = env.mem.snapshot()
        await run_jobs(env, [again])
        check_memory(env, before, [again])
