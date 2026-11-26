"""perf_idle: reset, configure nothing, run 4000 idle cycles. Activity
source for idle power (spec 17.2): only the clock, the free-running CYCLES
counter and the reset logic toggle."""
import cocotb
from cocotb.triggers import ClockCycles

from common import TIMEOUT, rng
from env import XdmaEnv


@cocotb.test(**TIMEOUT)
async def perf_idle(dut):
    env = XdmaEnv(dut, rng())
    await env.start()
    await ClockCycles(dut.clk, 4000)
