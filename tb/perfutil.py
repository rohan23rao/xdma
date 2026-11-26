"""Performance measurement helpers (spec 11, 16).

Bandwidth is measured in a steady-state window inside a long transfer:
- hardware: BYTES and BUSY_CYC read through the CSR bus at the start and the
  end of the window; utilization = dBYTES / (8 * dBUSY_CYC);
- testbench: beats seen by an independent monitor (m_axis handshakes for
  MM2S, s_axis handshakes for S2MM) between the cycles of those two reads;
  utilization = beats / cycles.
The two must agree within 1% (spec 11). End-to-end utilization (whole
transfer, including descriptor fetch, first-read latency and writeback) is
BYTES / (8 * BUSY_CYC) read after the channel goes idle.
"""
import json, os

from cocotb.triggers import ReadOnly, RisingEdge

from env import BUSY_CYC, BYTES


class CycleCounter:
    """Testbench cycle count and per-channel beat counts from the buses."""

    def __init__(self, dut, n_mm2s, n_ch):
        self.dut = dut
        self.cycle = 0
        self.beats = [0] * n_ch
        self.n_mm2s = n_mm2s
        self.n_ch = n_ch
        import cocotb
        cocotb.start_soon(self._run())

    async def _run(self):
        d = self.dut
        n_s2mm = self.n_ch - self.n_mm2s
        v = [getattr(d, f"m_axis{i}_tvalid") for i in range(self.n_mm2s)] + \
            [getattr(d, f"s_axis{i}_tvalid") for i in range(n_s2mm)]
        r = [getattr(d, f"m_axis{i}_tready") for i in range(self.n_mm2s)] + \
            [getattr(d, f"s_axis{i}_tready") for i in range(n_s2mm)]
        while True:
            await RisingEdge(d.clk)
            self.cycle += 1
            await ReadOnly()
            for c in range(self.n_ch):
                if int(v[c].value) and int(r[c].value):
                    self.beats[c] += 1


async def snapshot(env, cc, c):
    """(tb cycle, tb beats, BYTES, BUSY_CYC) for channel c."""
    cyc, beats = cc.cycle, cc.beats[c]
    b = await env.ch_rd(c, BYTES)
    busy = await env.ch_rd(c, BUSY_CYC)
    return cyc, beats, b, busy


def window(s0, s1):
    """Utilization (hardware, testbench) between two snapshots."""
    dcyc, dbeats = s1[0] - s0[0], s1[1] - s0[1]
    dbytes, dbusy = (s1[2] - s0[2]) & 0xFFFF_FFFF, (s1[3] - s0[3]) & 0xFFFF_FFFF
    hw = dbytes / (8 * dbusy) if dbusy else 0.0
    tb = dbeats / dcyc if dcyc else 0.0
    return hw, tb


def write_result(res):
    path = os.environ.get("XDMA_PERF_OUT")
    if path:
        with open(path, "w") as f:
            json.dump(res, f, indent=1)
