"""test_axi_errors: SLVERR / DECERR on an MM2S data read, an S2MM data write,
a descriptor read and a status writeback (spec 10, 14.2). The walker case
is in test_translate_fault.

Each round injects one error into one descriptor of one channel's chain
while every other channel runs a normal chain at the same time. Checks:
the failing channel reports the right ERR code and ERR_ADDR, earlier
descriptors completed, the failing descriptor got (or did not get) the
error writeback, its ERROR interrupt bit is set, the other channels are
unaffected, and the failing channel works again afterwards.
"""
import cocotb

from common import TIMEOUT, params, rand_mem_cfg, rng
from env import IRQ_STATUS, XdmaEnv
from models.axi_mem import DECERR, SLVERR
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import ERR_DESC_FETCH, ERR_RD_RESP, ERR_WR_RESP
from scoreboard import ChannelJob, check_error, check_memory, run_jobs

KINDS = ["rd_data", "wr_data", "desc_fetch", "writeback"]


@cocotb.test(**TIMEOUT)
async def test_axi_errors(dut):
    r = rng()
    p = params()
    cfg = rand_mem_cfg(r)
    env = XdmaEnv(dut, r, cfg, sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    b = DescBuilder(r, Allocator(r), max_bytes=2048)
    mm2s = list(range(p["NUM_MM2S"]))
    s2mm = list(range(p["NUM_MM2S"], p["NUM_CH"]))
    rounds = KINDS * 2
    r.shuffle(rounds)
    for kind in rounds:
        if kind == "rd_data":
            c = r.choice(mm2s)
        elif kind == "wr_data":
            c = r.choice(s2mm)
        else:
            c = r.randrange(p["NUM_CH"])
        n = r.randint(1, 5)
        k = r.randrange(n)
        jobs = {ch: ChannelJob(env, ch, b.chain(ch >= p["NUM_MM2S"], n if ch == c else r.randint(1, 4)))
                for ch in range(p["NUM_CH"])}
        bad = jobs[c]
        da, d = bad.chain[k]
        resp = r.choice([SLVERR, DECERR])
        writeback = True
        if kind in ("rd_data", "wr_data"):
            bursts = [(pa, nb) for pa, nb, _ in d.bursts(p["MAX_BURST"])]
            pa, nb = r.choice(bursts)
            first = next(x for x, n_ in bursts if x == pa)
            rng_ = (pa, pa + 8, resp)
            (cfg.rd_err if kind == "rd_data" else cfg.wr_err).append(rng_)
            err = ERR_RD_RESP if kind == "rd_data" else ERR_WR_RESP
            err_addr = first
        elif kind == "desc_fetch":
            cfg.rd_err.append((da, da + 32, resp))
            err, err_addr, writeback = ERR_DESC_FETCH, da, False
        else:
            cfg.wr_err.append((da + 0x18, da + 0x20, resp))
            err, err_addr, writeback = ERR_WR_RESP, da + 0x18, False
        dut._log.info(f"round {kind}: ch{c} descriptor {k} of {n}, resp {resp}")
        before = env.mem.snapshot()
        others = [j for ch, j in jobs.items() if ch != c]
        tasks = [cocotb.start_soon(j.run()) for j in jobs.values()]
        for t in tasks:
            await t
        await env.quiesce()
        for j in others:
            await j.check()
        if kind == "writeback":
            # the data of descriptor k completed; only its status write failed
            await check_error(env, bad, err, k, err_addr, writeback=False)
        else:
            await check_error(env, bad, err, k, err_addr, writeback=writeback)
        check_memory(env, before, list(jobs.values()))
        cfg.rd_err.clear()
        cfg.wr_err.clear()
        await env.wr(IRQ_STATUS, 0xFFFF_FFFF)
        # the failed channel is reusable
        again = ChannelJob(env, c, b.chain(c >= p["NUM_MM2S"], r.randint(1, 3)))
        before = env.mem.snapshot()
        await run_jobs(env, [again])
        check_memory(env, before, [again])
