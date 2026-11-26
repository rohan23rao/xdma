"""test_irq: interrupt bits and the irq pin (spec 5.1, 6, 10).

- A chain whose descriptors all have CTRL.IRQ = 0 sets no DONE bit.
- A chain with CTRL.IRQ on any descriptor sets that channel's DONE bit.
- An error sets the channel's ERROR bit (8 + c).
- irq == |(IRQ_STATUS & IRQ_MASK) at all times while the test runs (checked
  every cycle by a monitor against the register values read back).
- W1C clears only the written bits; masking hides bits from the pin.
"""
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from common import TIMEOUT, fail, params, rand_mem_cfg, rng
from env import IRQ_MASK, IRQ_STATUS, XdmaEnv
from models.desc_builder import Allocator, DescBuilder
from models.ref_model import Desc
from scoreboard import ChannelJob, run_jobs


async def pin_monitor(dut, state):
    """Checks irq == |(status & mask) using the CSR block's registers."""
    csr = dut.u_dut.u_csr
    while state["run"]:
        await RisingEdge(dut.clk)
        await ReadOnly()
        want = int((int(csr.irq_status.value) & int(csr.irq_mask.value)) != 0)
        if int(dut.irq.value) != want:
            state["err"] = f"irq={int(dut.irq.value)} but |(status & mask)={want}"


@cocotb.test(**TIMEOUT)
async def test_irq(dut):
    r = rng()
    p = params()
    n_ch = p["NUM_CH"]
    env = XdmaEnv(dut, r, rand_mem_cfg(r, throttle=False))
    await env.start()
    mon = {"run": True, "err": None}
    cocotb.start_soon(pin_monitor(dut, mon))
    b = DescBuilder(r, Allocator(r), max_bytes=512)

    # 1. no CTRL.IRQ anywhere -> no DONE bits
    jobs = [ChannelJob(env, c, b.chain(c >= p["NUM_MM2S"], r.randint(1, 4), irq=False)) for c in range(n_ch)]
    await run_jobs(env, jobs)
    if await env.rd(IRQ_STATUS) != 0:
        fail(f"IRQ_STATUS={await env.rd(IRQ_STATUS):#x} with CTRL.IRQ clear everywhere")

    # 2. CTRL.IRQ on one random descriptor of a random subset of channels
    chans = [c for c in range(n_ch) if r.random() < 0.6] or [0]
    jobs = []
    for c in range(n_ch):
        chain = b.chain(c >= p["NUM_MM2S"], r.randint(1, 4), irq=False)
        if c in chans:
            a, d = r.choice(chain)
            d.irq = True
        jobs.append(ChannelJob(env, c, chain))
    await env.wr(IRQ_MASK, 0)
    await run_jobs(env, jobs)
    want = sum(1 << c for c in chans)
    got = await env.rd(IRQ_STATUS)
    if got != want:
        fail(f"IRQ_STATUS={got:#x}, expected DONE bits {want:#x}")
    if int(dut.irq.value):
        fail("irq high with IRQ_MASK = 0")

    # 3. errors: DESC_INVALID on a random subset -> ERROR bits
    errs = [c for c in range(n_ch) if r.random() < 0.5] or [n_ch - 1]
    for c in errs:
        da = b.alloc.alloc(32, align=32)
        env.mem.write(da, Desc(addr=0x8000, line_bytes=12, lines=1, last=True).to_bytes())
        await env.start_chain(c, da)
    for c in errs:
        await env.wait_idle(c)
    want |= sum(1 << (8 + c) for c in errs)
    got = await env.rd(IRQ_STATUS)
    if got != want:
        fail(f"IRQ_STATUS={got:#x}, expected {want:#x}")

    # 4. masking and W1C, one bit at a time, in random order
    bits = [i for i in range(32) if want >> i & 1]
    r.shuffle(bits)
    for i in bits:
        await env.wr(IRQ_MASK, 1 << i)
        await ClockCycles(dut.clk, 2)
        if not int(dut.irq.value):
            fail(f"irq low with status bit {i} set and unmasked")
        await env.wr(IRQ_STATUS, 1 << i)
        want &= ~(1 << i)
        got = await env.rd(IRQ_STATUS)
        if got != want:
            fail(f"after W1C of bit {i}: IRQ_STATUS={got:#x}, expected {want:#x}")
        await ClockCycles(dut.clk, 2)
        if int(dut.irq.value):
            fail(f"irq still high after clearing bit {i}")
    mon["run"] = False
    await ClockCycles(dut.clk, 2)
    if mon["err"]:
        fail(mon["err"])
    await env.quiesce()
