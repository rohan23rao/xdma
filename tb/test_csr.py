"""test_csr: register map (spec 5): reset values, RW, W1C, W1S, CAPS,
byte strobes, unmapped addresses, START gating and the irq pin."""
import cocotb
from cocotb.triggers import ClockCycles

from common import TIMEOUT, fail, params, rng
from env import (BUSY_CYC, BYTES, CAPS, CTRL, CUR_DESC, CYCLES, DESC_HEAD, ERR_ADDR, GCTRL, ID,
                 IRQ_MASK, IRQ_STATUS, PT_ROOT, STALL_CYC, STATUS, TLB_STATS, XdmaEnv, ch_base)
from models.ref_model import ERR_DESC_INVALID, Desc


def expect(name, got, want):
    if got != want:
        fail(f"{name}: read {got:#010x}, expected {want:#010x}")


@cocotb.test(**TIMEOUT)
async def test_csr(dut):
    r = rng()
    p = params()
    env = XdmaEnv(dut, r)
    await env.start()
    n = p["NUM_CH"]

    # Reset values
    expect("ID", await env.rd(ID), 0x58444D41)
    caps = p["NUM_MM2S"] | p["NUM_S2MM"] << 4 | p["MAX_BURST"] << 8 | p["TLB_ENTRIES"] << 16 | 8 << 24
    expect("CAPS", await env.rd(CAPS), caps)
    for off, name in [(GCTRL, "GCTRL"), (PT_ROOT, "PT_ROOT"), (IRQ_STATUS, "IRQ_STATUS"),
                      (IRQ_MASK, "IRQ_MASK"), (TLB_STATS, "TLB_STATS")]:
        expect(name, await env.rd(off), 0)
    for c in range(n):
        for off in (CTRL, DESC_HEAD, STATUS, CUR_DESC, ERR_ADDR, BYTES, BUSY_CYC, STALL_CYC):
            expect(f"ch{c}+{off:#x}", await env.ch_rd(c, off), 0)
    if int(dut.irq.value) != 0:
        fail("irq high after reset")

    # Read-only registers ignore writes
    await env.wr(ID, 0x12345678)
    await env.wr(CAPS, 0)
    expect("ID after write", await env.rd(ID), 0x58444D41)
    expect("CAPS after write", await env.rd(CAPS), caps)

    # CYCLES counts
    c0 = await env.rd(CYCLES)
    await ClockCycles(dut.clk, 100)
    c1 = await env.rd(CYCLES)
    if not 100 <= c1 - c0 <= 140:
        fail(f"CYCLES advanced {c1 - c0} over ~100 cycles")

    # RW registers with random values; reserved bits read as zero
    irq_bits = sum((1 << c) | (1 << (8 + c)) for c in range(n))
    for _ in range(4):
        v = r.getrandbits(32)
        await env.wr(PT_ROOT, v)
        expect("PT_ROOT", await env.rd(PT_ROOT), v & 0xFFFFF)
        await env.wr(IRQ_MASK, v)
        expect("IRQ_MASK", await env.rd(IRQ_MASK), v & irq_bits)
        await env.wr(GCTRL, v & ~2)
        expect("GCTRL", await env.rd(GCTRL), v & 1)
        for c in range(n):
            await env.ch_wr(c, DESC_HEAD, v)
            expect(f"DESC_HEAD{c}", await env.ch_rd(c, DESC_HEAD), v & ~0x1F)

    # Byte strobes: only the written byte lane changes
    await env.wr(IRQ_MASK, 0)
    await env.wr(IRQ_MASK, 0xAABBCCDD, strb=0b0010)
    expect("IRQ_MASK strobe", await env.rd(IRQ_MASK), 0x0000CC00 & irq_bits)
    await env.wr(PT_ROOT, 0)
    await env.wr(PT_ROOT, 0xAABBCCDD, strb=0b0100)
    expect("PT_ROOT strobe", await env.rd(PT_ROOT), 0x000B0000)
    await env.wr(IRQ_MASK, 0)

    # W1S: TLB_FLUSH and START/ABORT read back as zero
    await env.wr(GCTRL, 0x2)
    expect("GCTRL W1S", await env.rd(GCTRL), 0)

    # START ignored while EN = 0
    await env.ch_wr(0, DESC_HEAD, 0x1000)
    await env.ch_wr(0, CTRL, 0x2)
    await ClockCycles(dut.clk, 5)
    expect("STATUS after START with EN=0", await env.ch_rd(0, STATUS), 0)
    expect("CTRL EN", await env.ch_rd(0, CTRL), 0)

    # Unmapped addresses: read 0, writes ignored, response OKAY
    for a in (0x020, 0x0FC, 0x100 + 0x40 * n, 0x100 + 0x20, 0xFFC):
        await env.wr(a, 0xFFFFFFFF)
        expect(f"unmapped {a:#x}", await env.rd(a), 0)

    # W1C + irq pin: make every channel fail with DESC_INVALID (LINE_BYTES=0)
    for c in range(n):
        base = 0x2000 + 0x100 * c
        env.mem.write(base, Desc(addr=0x8000, line_bytes=0, lines=1, last=True).to_bytes())
        await env.start_chain(c, base)
    for c in range(n):
        st = await env.wait_idle(c)
        expect(f"ch{c} STATUS.ERR", (st >> 1) & 7, ERR_DESC_INVALID)
        expect(f"ch{c} ERR_ADDR", await env.ch_rd(c, ERR_ADDR), 0x2000 + 0x100 * c)
    err_bits = sum(1 << (8 + c) for c in range(n))
    expect("IRQ_STATUS errors", await env.rd(IRQ_STATUS), err_bits)
    await ClockCycles(dut.clk, 3)
    if int(dut.irq.value):
        fail("irq high while IRQ_MASK = 0")
    await env.wr(IRQ_MASK, 1 << 9)
    await ClockCycles(dut.clk, 3)
    if not int(dut.irq.value):
        fail("irq low with a masked-in status bit set")
    await env.wr(IRQ_STATUS, 1 << 9)          # W1C one bit
    expect("IRQ_STATUS after W1C", await env.rd(IRQ_STATUS), err_bits & ~(1 << 9))
    await ClockCycles(dut.clk, 3)
    if int(dut.irq.value):
        fail("irq still high after clearing its only masked-in bit")
    await env.wr(IRQ_STATUS, 0xFFFFFFFF)
    expect("IRQ_STATUS cleared", await env.rd(IRQ_STATUS), 0)

    # START clears ERR; channel is reusable
    env.mem.write(0x2000, Desc(addr=0x8000, line_bytes=0, lines=1, last=True).to_bytes())
    await env.start_chain(0, 0x2000)
    st = await env.wait_idle(0)
    expect("ch0 ERR again", (st >> 1) & 7, ERR_DESC_INVALID)
    await env.quiesce()
