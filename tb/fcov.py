"""Functional coverage (spec 14.4), with cocotb-coverage.

Coverpoints are sampled from the testbench's own records (descriptor chains,
the memory model's burst log, error outcomes) and, in coverage runs, by a
per-cycle monitor of a few internal signals (busy channels, arbiter
conflicts, FIFO full, outstanding at max, TLB answers). At the end of a test
the hit bins are written to $XDMA_COV_FILE; tb/coverage.py merges all runs.
Nothing here affects pass/fail.
"""
import json, os

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
from cocotb_coverage.coverage import CoverPoint, coverage_db

ENABLED = bool(os.environ.get("XDMA_COV_FILE"))
MAX_BURST = 16
BINS = {
    "burst_len": list(range(1, MAX_BURST + 1)),
    "split_4k": [True],
    "lines": ["1", ">1"],
    "stride": ["neg", "zero", "pos"],
    "chain_len": ["1", "2-8", "9-32", "33-64"],
    "tlb": ["hit", "miss", "fault_invalid", "fault_megapage", "fault_perm"],
    "err_code": list(range(1, 8)),
    "abort_state": ["FETCH", "RUN", "DRAIN"],
    "active_channels": [1, 2, 3, 4],
    "ar_conflict": ["walker+desc", "walker+data", "desc+data"],
    "fifo_full": ["mm2s", "s2mm"],
    "outstanding_max": ["channel", "engine_rd", "engine_wr"],
}


def _cp(name):
    @CoverPoint(f"xdma.{name}", xf=lambda v: v, bins=BINS[name])
    def sample(v):
        pass
    return sample


_SAMPLERS = {k: _cp(k) for k in BINS}


def sample(point, value):
    if ENABLED and value in BINS[point]:
        _SAMPLERS[point](value)


def sample_chain(chain, max_burst):
    """Descriptor-level bins for one chain."""
    if not ENABLED:
        return
    n = len(chain)
    sample("chain_len", "1" if n == 1 else "2-8" if n <= 8 else "9-32" if n <= 32 else "33-64")
    for _, d in chain:
        sample("lines", "1" if d.lines == 1 else ">1")
        if d.lines > 1:
            sample("stride", "neg" if d.stride < 0 else "zero" if d.stride == 0 else "pos")
        for la in d.line_addrs():
            if la % 4096 + d.line_bytes > 4096:
                sample("split_4k", True)


def sample_bursts(axi, n_ch):
    """Burst lengths of every data burst the memory model saw."""
    if not ENABLED:
        return
    for log in (axi.ar_log, axi.aw_log):
        for _, aid, _, beats in log:
            if aid < n_ch:
                sample("burst_len", beats)


class CovMonitor:
    """Per-cycle sampling of internal conditions (coverage runs only)."""

    def __init__(self, dut, p):
        self.dut, self.p = dut, p
        if ENABLED:
            cocotb.start_soon(self._run())

    async def _run(self):
        d, p = self.dut.u_dut, self.p
        n, nm = p["NUM_CH"], p["NUM_MM2S"]
        busy = [d.g_ch[c].u_ctl.busy for c in range(n)]
        walk_req, desc_req, rd_req = d.ptw_ar_req, d.desc_req, d.rd_req
        rd_fifo = [d.g_ch[c].g_mm2s.u_rd.fifo_count for c in range(nm)]
        wr_fifo = [d.g_ch[c].g_s2mm.u_wr.fifo_count for c in range(nm, n)]
        outs = [d.g_ch[c].g_mm2s.u_rd.outstanding for c in range(nm)] + \
               [d.g_ch[c].g_s2mm.u_wr.outstanding for c in range(nm, n)]
        rd_out, wr_out = d.rd_out, d.wr_out
        tlb = d.u_tlb
        seen = set()
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            nb = sum(int(b.value) for b in busy)
            if nb:
                self._once(seen, "active_channels", nb)
            w, de, da = int(walk_req.value), int(desc_req.value) != 0, int(rd_req.value) != 0
            if w and de:
                self._once(seen, "ar_conflict", "walker+desc")
            if w and da:
                self._once(seen, "ar_conflict", "walker+data")
            if de and da:
                self._once(seen, "ar_conflict", "desc+data")
            if any(int(f.value) == p["FIFO_DEPTH"] for f in rd_fifo):
                self._once(seen, "fifo_full", "mm2s")
            if any(int(f.value) == p["FIFO_DEPTH"] for f in wr_fifo):
                self._once(seen, "fifo_full", "s2mm")
            if any(int(o.value) == p["MAX_OUT_CH"] for o in outs):
                self._once(seen, "outstanding_max", "channel")
            if int(rd_out.value) == p["MAX_OUT_RD"]:
                self._once(seen, "outstanding_max", "engine_rd")
            if int(wr_out.value) == p["MAX_OUT_WR"]:
                self._once(seen, "outstanding_max", "engine_wr")
            if int(tlb.l_valid.value):
                k = int(tlb.resp_kind.value)
                if k == 0:
                    self._once(seen, "tlb", "hit")
                elif k == 2:
                    self._once(seen, "tlb", "miss")

    @staticmethod
    def _once(seen, point, value):
        # sample each bin at most once per run (enough to mark it hit)
        if (point, value) not in seen:
            seen.add((point, value))
            sample(point, value)


def write():
    """Write {coverpoint: [hit bins]} for this run."""
    path = os.environ.get("XDMA_COV_FILE")
    if not path:
        return
    hits = {}
    for name in BINS:
        cp = coverage_db[f"xdma.{name}"]
        hits[name] = [str(b) for b, cnt in cp.detailed_coverage.items() if cnt > 0]
    with open(path, "w") as f:
        json.dump(hits, f)
