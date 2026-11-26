"""test_stream_len: S2MM stream rule violations (spec 6, 7.4, 10):
early TLAST, missing TLAST, TLAST with EOP = 0, bad TKEEP.

The bad beat is placed at a random position of a random descriptor of the
chain; the other S2MM channel and both MM2S channels run normal chains at
the same time. Checks: STREAM_LEN on the failing channel with ERR_ADDR =
the failing descriptor, earlier descriptors complete, error writeback on
the failing one, no byte from the bad beat onward written to memory, other
channels unaffected, channel reusable.
"""
import cocotb

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import IRQ_STATUS, XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import ERR_STREAM_LEN
from scoreboard import ChannelJob, check_error, check_memory, run_jobs

CASES = ["early_tlast", "missing_tlast", "tlast_eop0", "bad_tkeep"]


@cocotb.test(**TIMEOUT)
async def test_stream_len(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    b = DescBuilder(r, Allocator(r), max_bytes=2048)
    s2mm = list(range(p["NUM_MM2S"], p["NUM_CH"]))
    cases = CASES * 2
    r.shuffle(cases)
    for case in cases:
        c = r.choice(s2mm)
        n = r.randint(1, 4)
        k = r.randrange(n)
        jobs = {ch: ChannelJob(env, ch, b.chain(ch >= p["NUM_MM2S"], n if ch == c else r.randint(1, 3)))
                for ch in range(p["NUM_CH"])}
        bad = jobs[c]
        da, d = bad.chain[k]
        nbeats = d.nbytes() // 8
        if case == "early_tlast" and nbeats < 2:
            d.line_bytes, d.lines = 16, 1      # need at least two beats
        d.eop = case != "tlast_eop0"
        env.mem.write(da, d.to_bytes())
        bad.__init__(env, c, bad.chain)          # re-predict after the edit
        nbeats = d.nbytes() // 8
        beats = bad.stream_beats()
        base = sum(x.nbytes() // 8 for _, x in bad.chain[:k])
        if case == "early_tlast":
            j = r.randrange(nbeats - 1)
            beats[base + j] = (beats[base + j][0], 0xFF, 1)
        elif case == "missing_tlast":
            j = nbeats - 1
            beats[base + j] = (beats[base + j][0], 0xFF, 0)
        elif case == "tlast_eop0":
            j = r.randrange(nbeats)
            beats[base + j] = (beats[base + j][0], 0xFF, 1)
        else:
            j = r.randrange(nbeats)
            beats[base + j] = (beats[base + j][0], r.choice([0x0F, 0x7F, 0xFE, 0x00]), beats[base + j][2])
        dut._log.info(f"{case}: ch{c} descriptor {k} of {n}, bad beat {j} of {nbeats}")
        before = env.mem.snapshot()
        tasks = [cocotb.start_soon(job.run(beats=beats if ch == c else None)) for ch, job in jobs.items()]
        for t in tasks:
            await t
        await env.quiesce()
        for ch, job in jobs.items():
            if ch != c:
                await job.check()
        await check_error(env, bad, ERR_STREAM_LEN, k, err_addr=da, writeback=True)
        # nothing from the bad beat onward reached memory
        pos = 8 * (base + j)
        order = []
        for _, x in bad.chain:
            for va, nb, _ in x.bursts(p["MAX_BURST"]):
                order += [va + i for i in range(nb * 8)]
        for a in order[pos:pos + 8 * (nbeats - j)]:
            if env.mem.read(a, 1)[0] != before.get(a // 4096, bytes(4096))[a % 4096]:
                fail(f"byte {a:#x} from the bad beat onward was written")
        check_memory(env, before, list(jobs.values()))
        await env.wr(IRQ_STATUS, 0xFFFF_FFFF)
        again = ChannelJob(env, c, b.chain(True, r.randint(1, 3)))
        before = env.mem.snapshot()
        await run_jobs(env, [again])
        check_memory(env, before, [again])
