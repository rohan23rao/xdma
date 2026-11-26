"""XDMA verification environment (UVM-style layering on cocotb).

XdmaEnv builds the agents around the DUT (tb/hdl/xdma_tb.sv):
- AXI4 slave memory agent (models/axi_mem.py) on m_axi_*;
- AXI4-Lite master agent (cocotbext-axi) on s_axil_*;
- one AXI-Stream sink agent per MM2S port (cocotbext-axi, random
  backpressure) and one source agent per S2MM port (StreamDriver, random
  gaps, any TKEEP/TLAST pattern);
- stream monitors that log every MM2S beat with its cycle for the scoreboard.
It also provides the register-level sequences (start a chain, wait for idle)
used by every test.
"""
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge
from cocotbext.axi import AxiLiteBus, AxiLiteMaster, AxiStreamBus, AxiStreamSink

from common import clock_reset, fail, params
from models.axi_mem import AxiMemModel, MemConfig, SparseMem

# Register offsets (spec 5)
ID, CAPS, GCTRL, PT_ROOT, IRQ_STATUS, IRQ_MASK, CYCLES, TLB_STATS = range(0, 0x20, 4)
CTRL, DESC_HEAD, STATUS, CUR_DESC, ERR_ADDR, BYTES, BUSY_CYC, STALL_CYC = range(0, 0x20, 4)


def ch_base(c):
    return 0x100 + 0x40 * c


def rand_pause(rng, p):
    """Endless generator for cocotbext-axi pause: True pauses for a cycle."""
    while True:
        yield rng.random() < p


class StreamMonitor:
    """Records every handshaked MM2S beat as (cycle, data, tlast)."""

    def __init__(self, dut, i):
        self.v = getattr(dut, f"m_axis{i}_tvalid")
        self.r = getattr(dut, f"m_axis{i}_tready")
        self.d = getattr(dut, f"m_axis{i}_tdata")
        self.l = getattr(dut, f"m_axis{i}_tlast")
        self.k = getattr(dut, f"m_axis{i}_tkeep")
        # In loopback mode port 0's real TREADY is S2MM port 0's TREADY.
        self.loop = dut.loop_en if i == 0 else None
        self.r_loop = dut.s_axis0_tready
        self.beats = []
        self.errors = []
        self.clk = dut.clk
        cocotb.start_soon(self._run())

    async def _run(self):
        cycle = 0
        while True:
            await RisingEdge(self.clk)
            cycle += 1
            await ReadOnly()
            ready = self.r_loop if (self.loop is not None and int(self.loop.value)) else self.r
            if int(self.v.value) and int(ready.value):
                if int(self.k.value) != 0xFF:
                    self.errors.append(f"TKEEP {int(self.k.value):#x} at cycle {cycle}")
                self.beats.append((cycle, int(self.d.value), int(self.l.value)))


class XdmaEnv:
    def __init__(self, dut, rng, mem_cfg=None, sink_pause=0.0, src_pause=0.0):
        self.dut = dut
        self.rng = rng
        self.p = params()
        self.mem = SparseMem()
        self.mem_cfg = mem_cfg or MemConfig()
        n = self.p["NUM_CH"]
        ids = dict(AR=set(range(self.p["NUM_MM2S"])) | set(range(n, 2 * n)) | {2 * n},
                   AW=set(range(self.p["NUM_MM2S"], n)) | set(range(n, 2 * n)))
        self.axi = AxiMemModel(dut, self.mem, self.mem_cfg, rng, self.p["MAX_BURST"], ids)
        self.sink_pause = sink_pause
        self.src_pause = src_pause

    async def start(self):
        dut = self.dut
        for i in range(2):
            getattr(dut, f"s_axis{i}_tvalid").value = 0
            getattr(dut, f"m_axis{i}_tready").value = 0
        dut.loop_en.value = 0
        for n in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
            getattr(dut, f"s_axil_{n}").value = 0
        self.axi.start()
        await clock_reset(dut)
        self.axil = AxiLiteMaster(AxiLiteBus.from_prefix(dut, "s_axil"), dut.clk, dut.rst_n,
                                  reset_active_level=False)
        self.sinks, self.drv, self.mons = [], [], []
        for i in range(2):
            snk = AxiStreamSink(AxiStreamBus.from_prefix(dut, f"m_axis{i}"), dut.clk, dut.rst_n,
                                reset_active_level=False)
            if self.sink_pause:
                snk.set_pause_generator(rand_pause(self.rng, self.sink_pause))
            self.sinks.append(snk)
            self.drv.append(StreamDriver(dut, i, self.rng, self.src_pause))
            self.mons.append(StreamMonitor(dut, i))
        await ClockCycles(dut.clk, 2)

    # ---------------------------------------------------------- registers
    async def wr(self, addr, val, strb=0xF):
        if strb == 0xF:
            await self.axil.write_dword(addr, val & 0xFFFF_FFFF)
        else:
            data = (val & 0xFFFF_FFFF).to_bytes(4, "little")
            for b in range(4):
                if strb >> b & 1:
                    await self.axil.write(addr + b, data[b:b + 1])

    async def rd(self, addr):
        return await self.axil.read_dword(addr)

    async def ch_wr(self, c, off, val):
        await self.wr(ch_base(c) + off, val)

    async def ch_rd(self, c, off):
        return await self.rd(ch_base(c) + off)

    async def start_chain(self, c, head):
        await self.ch_wr(c, DESC_HEAD, head)
        await self.ch_wr(c, CTRL, 0x3)      # EN + START

    async def wait_idle(self, c, timeout_cycles=200000, poll=50):
        """Poll STATUS.BUSY until clear. Fails (with seed) on timeout."""
        waited = 0
        while True:
            st = await self.ch_rd(c, STATUS)
            if not st & 1:
                return st
            await ClockCycles(self.dut.clk, poll)
            waited += poll
            if waited > timeout_cycles:
                fail(f"channel {c} still busy after {timeout_cycles} cycles (STATUS={st:#x})")

    async def wait_all_idle(self, chans, timeout_cycles=400000):
        for c in chans:
            await self.wait_idle(c, timeout_cycles)

    async def quiesce(self, cycles=50):
        """Let in-flight AXI traffic finish, then check the memory model."""
        for _ in range(200):
            await ClockCycles(self.dut.clk, cycles)
            if self.axi.idle():
                break
        self.axi.check()
        for m in self.mons:
            if m.errors:
                fail("stream monitor: " + "; ".join(m.errors))

    # ------------------------------------------------------------- streams
    def set_sink_pause(self, i, prob):
        """Change MM2S sink backpressure. cocotbext-axi leaves `pause` at its
        last value when a generator is replaced, so reset it explicitly."""
        snk = self.sinks[i]
        snk.clear_pause_generator()
        snk.pause = False
        if prob:
            snk.set_pause_generator(rand_pause(self.rng, prob))

    def ch_state(self, c):
        """Channel controller state (0 IDLE, 1 FETCH, 2 RUN, 3 WB, 4 WB_WAIT,
        5 DRAIN, 6 EWB, 7 EWB_WAIT, 8 FINISH), read through the hierarchy."""
        return int(self.dut.u_dut.g_ch[c].u_ctl.state.value)

    async def abort(self, c):
        await self.ch_wr(c, CTRL, 0x5)      # keep EN, ABORT

    async def send_beats(self, i, beats):
        """Drive beats [(data64, tkeep, tlast)] on S2MM port i, with random
        gaps when src_pause is set. Returns when the last beat is accepted."""
        await self.drv[i].send(beats)


class StreamDriver:
    """AXI-Stream source agent for one S2MM port. Own driver (not
    cocotbext-axi) so tests can inject bad TKEEP and any TLAST pattern,
    including streams with no TLAST at all (EOP = 0 descriptors)."""

    def __init__(self, dut, i, rng, pause):
        self.clk = dut.clk
        self.v = getattr(dut, f"s_axis{i}_tvalid")
        self.r = getattr(dut, f"s_axis{i}_tready")
        self.d = getattr(dut, f"s_axis{i}_tdata")
        self.k = getattr(dut, f"s_axis{i}_tkeep")
        self.l = getattr(dut, f"s_axis{i}_tlast")
        self.rng, self.pause = rng, pause
        self.sent = 0
        self.lock = cocotb.triggers.Lock()
        self._stop = False

    def stop(self):
        """Abandon the beats not yet accepted (the channel stopped taking
        them, e.g. after an error or abort)."""
        self._stop = True

    async def send(self, beats):
        async with self.lock:
            self._stop = False
            for data, keep, last in beats:
                while self.pause and self.rng.random() < self.pause and not self._stop:
                    self.v.value = 0
                    await RisingEdge(self.clk)
                if self._stop:
                    break
                self.v.value = 1
                self.d.value = data
                self.k.value = keep
                self.l.value = last
                while True:
                    await ReadOnly()
                    rdy = int(self.r.value)
                    await RisingEdge(self.clk)
                    if rdy or self._stop:
                        break
                if not rdy:
                    break
                self.sent += 1
            self.v.value = 0
            self._stop = False
