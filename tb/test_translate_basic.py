"""test_translate_basic: translation on (spec 8). Every channel runs random
chains whose buffers are virtual and mapped to scattered physical pages, all
channels at once. Checks data, memory isolation, and the TLB / walker
bookkeeping: every miss starts exactly one walk of two single-beat PTE reads
on the walker ID, and both hits and misses happen."""
import cocotb

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import TLB_STATS, XdmaEnv
from scoreboard import ChannelJob, check_memory, run_jobs
from xlat_util import XlatSetup


@cocotb.test(**TIMEOUT)
async def test_translate_basic(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=r.choice([0, 0.2]), src_pause=r.choice([0, 0.2]))
    await env.start()
    xs = XlatSetup(env, r)
    await xs.enable()
    walker_id = 2 * p["NUM_CH"]
    for rnd in range(3):
        jobs = [ChannelJob(env, c, xs.chain(c >= p["NUM_MM2S"], r.randint(1, 6)), xlat=xs.xlat)
                for c in range(p["NUM_CH"])]
        before = env.mem.snapshot()
        await run_jobs(env, jobs)
        check_memory(env, before, jobs)
    stats = await env.rd(TLB_STATS)
    hits, misses = stats & 0xFFFF, stats >> 16
    walks = [a for a in env.axi.ar_log if a[1] == walker_id]
    dut._log.info(f"TLB hits {hits}, misses {misses}, walker reads {len(walks)}")
    if misses == 0 or hits == 0:
        fail(f"expected both TLB hits and misses, got hits={hits} misses={misses}")
    if len(walks) != 2 * misses:
        fail(f"{len(walks)} walker reads for {misses} misses (expected two per walk)")
    if any(beats != 1 or addr % 8 for _, _, addr, beats in walks):
        fail("walker read is not a single aligned beat")
