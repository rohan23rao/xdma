"""test_soak: long random run (spec 14.2): at least 30,000 cycles, all four
channels at once, translation on, everything random. Every round draws new
memory timing (latency, jitter, throttling, out-of-order, AWREADY waiting
for WVALID), new stream backpressure, new chains (1D/2D, every stride kind,
4 KB corners), and may flush the TLB or abort one channel. Every round is
fully checked; aborted channels are checked for a consistent outcome."""
import cocotb
from cocotb.triggers import RisingEdge

from common import LONG_TIMEOUT, fail, params, rand_mem_cfg, rng
from env import IRQ_STATUS, XdmaEnv
from models.ref_model import ERR_ABORTED, ERR_NONE
from scoreboard import ChannelJob, check_error, check_memory
from xlat_util import XlatSetup

MIN_CYCLES = 30000


@cocotb.test(**LONG_TIMEOUT)
async def test_soak(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r, rand_mem_cfg(r), sink_pause=0.2, src_pause=0.2)
    await env.start()
    xs = XlatSetup(env, r, max_bytes=4096)
    await xs.enable()
    rounds = aborts = 0
    while env.axi.cycle < MIN_CYCLES:
        rounds += 1
        new = rand_mem_cfg(r)
        for k, v in vars(new).items():
            if k not in ("rd_err", "wr_err"):
                setattr(env.axi.cfg, k, v)
        for i in range(len(env.sinks)):
            env.set_sink_pause(i, r.choice([0, 0.2, 0.5]))
        for d in env.drv:
            d.pause = r.choice([0, 0.2, 0.5])
        if r.random() < 0.2:
            await xs.flush()
        jobs = [ChannelJob(env, c, xs.chain(c >= p["NUM_MM2S"], r.randint(1, 8)), xlat=xs.xlat)
                for c in range(p["NUM_CH"])]
        victim = r.randrange(p["NUM_CH"]) if r.random() < 0.15 else None
        before = env.mem.snapshot()
        tasks = [cocotb.start_soon(j.run()) for j in jobs]
        if victim is not None:
            for _ in range(r.randint(1, 2000)):
                await RisingEdge(dut.clk)
            await env.abort(victim)
            aborts += 1
        for t in tasks:
            await t
        await env.quiesce()
        for j in jobs:
            err = (j.st >> 1) & 7
            if j.c == victim and err == ERR_ABORTED:
                k = j.st >> 8
                st = env.mem.read64(j.chain[k][0] + 0x18) if k < len(j.chain) else 0
                await check_error(env, j, ERR_ABORTED, k, None, writeback=st != 0)
            elif err == ERR_NONE:
                await j.check()
            else:
                fail(f"ch{j.c}: unexpected ERR={err}")
        check_memory(env, before, jobs)
        await env.wr(IRQ_STATUS, 0xFFFF_FFFF)
    dut._log.info(f"soak: {rounds} rounds, {aborts} aborts, {env.axi.cycle} cycles, "
                  f"{env.axi.r_beats} R beats, {env.axi.w_beats} W beats")
