"""Unit test: xdma_addr_gen against the Python burst splitter (section 7.2).

Random descriptors with weighted corner cases (near 4 KB, negative and zero
stride, single line, 8-byte lines, long lines), random output backpressure
and occasional aborts. Every emitted burst must match split_bursts().
"""
import cocotb
from cocotb.triggers import RisingEdge, ReadOnly

from common import clock_reset, fail, rng
from models.ref_model import split_bursts

MAX_BURST = 16


def rand_desc(r):
    kind = r.random()
    if kind < 0.3:      # start within 128 B below a 4 KB boundary
        addr = r.randrange(0, 1 << 20) * 4096 - 8 * r.randint(1, 16)
    elif kind < 0.4:    # near the top of the address space: wrap-around
        addr = (1 << 32) - 8 * r.randint(1, 64)
    else:
        addr = r.randrange(0, 1 << 29) * 8
    addr &= 0xFFFF_FFFF
    lb = r.choice([8, 8 * r.randint(1, 16), 8 * r.randint(1, 512), 0xFFF8, 8 * r.randint(1, 8191)])
    lines = r.choice([1, 1, r.randint(2, 6), r.randint(2, 40)])
    if lb > 4096:
        lines = min(lines, 3)
    stride = r.choice([lb, lb + 8 * r.randint(1, 600), -8 * r.randint(1, 2000), 0, 4096])
    return addr, lb, lines, stride


@cocotb.test()
async def test_addr_gen(dut):
    r = rng()
    dut.start.value = 0
    dut.abort.value = 0
    dut.out_ready.value = 0
    await clock_reset(dut)
    ndesc = 120
    for n in range(ndesc):
        addr, lb, lines, stride = rand_desc(r)
        expect = split_bursts(addr, lb, lines, stride, MAX_BURST)
        abort_at = r.randrange(len(expect)) if r.random() < 0.08 else None
        dut.addr.value = addr
        dut.line_bytes.value = lb
        dut.lines.value = lines
        dut.stride.value = stride & 0xFFFF_FFFF
        dut.start.value = 1
        await RisingEdge(dut.clk)
        dut.start.value = 0
        got = 0
        ready_p = r.choice([1.0, 0.7, 0.3])
        cycles = 0
        while got < len(expect):
            if abort_at is not None and got == abort_at:
                dut.abort.value = 1
                await RisingEdge(dut.clk)
                dut.abort.value = 0
                await ReadOnly()
                if int(dut.busy.value):
                    fail("busy after abort")
                await RisingEdge(dut.clk)
                break
            dut.out_ready.value = int(r.random() < ready_p)
            await ReadOnly()
            if int(dut.out_valid.value) and int(dut.out_ready.value):
                va, beats, last = int(dut.out_va.value), int(dut.out_beats.value), int(dut.out_last.value)
                if (va, beats, bool(last)) != expect[got]:
                    fail(f"desc {n} burst {got}: got {(hex(va), beats, last)} expected "
                         f"{(hex(expect[got][0]), expect[got][1], expect[got][2])} "
                         f"(addr={addr:#x} lb={lb} lines={lines} stride={stride})")
                got += 1
            await RisingEdge(dut.clk)
            cycles += 1
            if cycles > 40 * len(expect) + 100:
                fail(f"timeout in desc {n}")
        dut.out_ready.value = 0
        await ReadOnly()
        if int(dut.out_valid.value):
            fail(f"desc {n}: extra burst after the last one")
        await RisingEdge(dut.clk)
    dut._log.info(f"{ndesc} descriptors checked")
