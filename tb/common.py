"""Shared testbench helpers: seeding, parameters, clock/reset, timeouts."""
import os, random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

DEFAULTS = dict(NUM_MM2S=2, NUM_S2MM=2, ADDR_W=32, DATA_W=64, ID_W=4, MAX_BURST=16,
                FIFO_DEPTH=64, MAX_OUT_CH=8, MAX_OUT_RD=16, MAX_OUT_WR=8, TLB_ENTRIES=8,
                PREFETCH_EN=1)


def params():
    """Engine parameters for this run (defaults overridden by the runner)."""
    p = dict(DEFAULTS)
    for kv in filter(None, os.environ.get("XDMA_PARAMS", "").split(",")):
        k, v = kv.split("=")
        p[k] = int(v)
    p["NUM_CH"] = p["NUM_MM2S"] + p["NUM_S2MM"]
    return p


def seed():
    """The test seed, set by tb/run.py (SEED=<n> on the make command line)."""
    return int(os.environ.get("XDMA_SEED", "1"))


def rng():
    """Per-test RNG; the seed is printed so any failure is reproducible."""
    s = seed()
    cocotb.log.info(f"SEED={s}")
    return random.Random(s)


async def clock_reset(dut, cycles=5):
    # Reset goes low before the first clock edge so no assertion ever sees
    # random power-up state outside reset.
    dut.rst_n.value = 0
    dut.clk.value = 0
    await Timer(1, unit="ns")
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start(start_high=False))
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


def fail(msg):
    """Raise a test failure that always names the seed."""
    raise AssertionError(f"{msg}  [SEED={seed()}]")


def rand_mem_cfg(r, throttle=True):
    """Random memory-model timing: latency, jitter, READY/VALID throttling,
    out-of-order responses across IDs."""
    from models.axi_mem import MemConfig
    if not throttle:
        return MemConfig(latency=r.randint(4, 30))
    return MemConfig(latency=r.randint(2, 40), jitter=r.choice([0, 3, 12]),
                     max_rd=r.choice([2, 4, 32]), max_wr=r.choice([2, 4, 32]),
                     p_arready=r.choice([1.0, 0.7, 0.4]), p_awready=r.choice([1.0, 0.7, 0.4]),
                     p_wready=r.choice([1.0, 0.7, 0.4]), p_rvalid=r.choice([1.0, 0.8, 0.5]),
                     p_bvalid=r.choice([1.0, 0.8, 0.5]), ooo=r.random() < 0.5,
                     aw_wait_w=r.random() < 0.3 or bool(os.environ.get("XDMA_AW_WAIT_W")))


# Every test gets a simulation-time limit so a hang fails instead of running
# forever: @cocotb.test(**TIMEOUT). 20 ms = 2,000,000 cycles at 100 MHz.
TIMEOUT = dict(timeout_time=20, timeout_unit="ms")
LONG_TIMEOUT = dict(timeout_time=200, timeout_unit="ms")
