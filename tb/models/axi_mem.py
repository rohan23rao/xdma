"""AXI4 slave memory model for the XDMA testbench (spec 14.1).

Written for this project instead of using an off-the-shelf RAM so it can:
- hold sparse byte-addressable memory (SparseMem);
- add a fixed latency plus uniform random jitter to every transaction;
- limit how many reads / writes it accepts at once;
- throttle READY / VALID on all five channels with probability knobs;
- return SLVERR / DECERR for address ranges, separately for reads and writes;
- return responses out of order across IDs (randomly), in order within an ID,
  and interleave R beats of different IDs;
- check the master: 4 KB crossing, ARLEN/AWLEN <= MAX_BURST-1, size and burst
  type, WLAST position, W before AW, VALID held until READY with a stable
  payload, IDs outside the engine's ID map.

Timing: one coroutine runs the whole slave. Each cycle, right after the
rising edge, it applies the handshakes that happened at that edge and drives
its outputs for the new cycle; in the ReadOnly phase of the same cycle it
samples the master's outputs to find the handshakes of the next edge.
"""
from dataclasses import dataclass, field
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

OKAY, EXOKAY, SLVERR, DECERR = 0, 1, 2, 3


class SparseMem:
    """Sparse little-endian byte memory, 4 KB pages, zero-filled."""

    PAGE = 4096

    def __init__(self):
        self.pages = {}

    def _page(self, pn, create):
        p = self.pages.get(pn)
        if p is None and create:
            p = self.pages[pn] = bytearray(self.PAGE)
        return p

    def write(self, addr, data):
        addr &= 0xFFFF_FFFF
        i = 0
        while i < len(data):
            a = (addr + i) & 0xFFFF_FFFF
            pn, off = divmod(a, self.PAGE)
            n = min(self.PAGE - off, len(data) - i)
            self._page(pn, True)[off:off + n] = data[i:i + n]
            i += n

    def read(self, addr, n):
        addr &= 0xFFFF_FFFF
        out = bytearray()
        while len(out) < n:
            a = (addr + len(out)) & 0xFFFF_FFFF
            pn, off = divmod(a, self.PAGE)
            k = min(self.PAGE - off, n - len(out))
            p = self._page(pn, False)
            out += p[off:off + k] if p is not None else bytes(k)
        return bytes(out)

    def read64(self, addr):
        return int.from_bytes(self.read(addr, 8), "little")

    def write64(self, addr, v):
        self.write(addr, (v & (2**64 - 1)).to_bytes(8, "little"))

    def read32(self, addr):
        return int.from_bytes(self.read(addr, 4), "little")

    def write32(self, addr, v):
        self.write(addr, (v & 0xFFFF_FFFF).to_bytes(4, "little"))

    def snapshot(self):
        return {pn: bytes(p) for pn, p in self.pages.items()}

    @staticmethod
    def diff(before, after):
        """Byte addresses whose value differs between two snapshots."""
        out = []
        for pn in set(before) | set(after):
            a = before.get(pn, bytes(4096))
            b = after.get(pn, bytes(4096))
            if a != b:
                out += [pn * 4096 + i for i in range(4096) if a[i] != b[i]]
        return sorted(out)


@dataclass
class MemConfig:
    latency: int = 8            # cycles from address handshake to first data / B
    jitter: int = 0             # extra 0..jitter cycles per transaction
    max_rd: int = 32            # reads accepted and not yet completed
    max_wr: int = 32
    p_arready: float = 1.0
    p_awready: float = 1.0
    p_wready: float = 1.0
    p_rvalid: float = 1.0
    p_bvalid: float = 1.0
    ooo: bool = False           # random order across IDs (and R interleave)
    rd_err: list = field(default_factory=list)   # [(lo, hi, resp)] inclusive lo, exclusive hi
    wr_err: list = field(default_factory=list)


class AxiMemModel:
    def __init__(self, dut, mem, cfg, rng, max_burst=16, valid_ids=None, prefix="m_axi_"):
        self.dut, self.mem, self.cfg, self.rng = dut, mem, cfg, rng
        self.max_burst = max_burst
        self.valid_ids = valid_ids
        self.s = {n: getattr(dut, prefix + n) for n in (
            "arvalid arready arid araddr arlen arsize arburst rvalid rready rid rdata rresp rlast "
            "awvalid awready awid awaddr awlen awsize awburst wvalid wready wdata wstrb wlast "
            "bvalid bready bid bresp").split()}
        self.errors = []
        self.cycle = 0
        self.ar_log = []   # (cycle, id, addr, beats)
        self.aw_log = []
        self.r_beats = 0
        self.w_beats = 0
        self._driven = {}
        self._seq = 0
        self.rd = []       # outstanding reads
        self.aw_q = []     # AWs waiting for W data
        self.b_q = []      # pending B responses
        self.r_cur = None  # beat currently presented on R
        self.b_cur = None
        self._task = None
        # last sampled master state (for stability checks)
        self._prev = {}

    # ------------------------------------------------------------ helpers
    def _err(self, msg):
        if len(self.errors) < 50:
            self.errors.append(f"cycle {self.cycle}: {msg}")
            self.dut._log.error(f"axi_mem: {msg}")

    def _drive(self, name, v):
        if self._driven.get(name) != v:
            self.s[name].value = v
            self._driven[name] = v

    def _resp_for(self, ranges, addr, nbytes):
        for lo, hi, resp in ranges:
            if addr < hi and addr + nbytes > lo:
                return resp
        return OKAY

    def _delay(self):
        return self.cfg.latency + (self.rng.randint(0, self.cfg.jitter) if self.cfg.jitter else 0)

    def check(self):
        if self.errors:
            raise AssertionError("AXI memory model errors:\n  " + "\n  ".join(self.errors))

    def start(self):
        for n in ("arready", "awready", "wready", "rvalid", "bvalid", "rlast"):
            self._drive(n, 0)
        self._drive("rid", 0); self._drive("rdata", 0); self._drive("rresp", 0)
        self._drive("bid", 0); self._drive("bresp", 0)
        self._task = cocotb.start_soon(self._run())

    def idle(self):
        """No transaction in flight in either direction."""
        return not (self.rd or self.aw_q or self.b_q or self.r_cur or self.b_cur)

    # ---------------------------------------------------- address checks
    def _check_addr(self, kind, aid, addr, alen, size, burst):
        beats = alen + 1
        if alen > self.max_burst - 1:
            self._err(f"{kind}LEN {alen} > MAX_BURST-1 ({self.max_burst - 1}) at {addr:#x}")
        if size != 3:
            self._err(f"{kind}SIZE {size} != 3 at {addr:#x}")
        if burst != 1:
            self._err(f"{kind}BURST {burst} != INCR at {addr:#x}")
        if addr % 8:
            self._err(f"{kind}ADDR {addr:#x} not 8-byte aligned")
        if (addr % 4096) + beats * 8 > 4096:
            self._err(f"{kind} burst {addr:#x} + {beats} beats crosses 4 KB")
        if self.valid_ids is not None and aid not in self.valid_ids[kind]:
            self._err(f"{kind}ID {aid} outside the ID map")

    # -------------------------------------------------------------- loop
    async def _run(self):
        clk = self.dut.clk
        hs = {}
        cfg, rng = self.cfg, self.rng
        while True:
            await RisingEdge(clk)
            self.cycle += 1
            # ---- apply handshakes that happened at this edge
            if hs.get("ar"):
                aid, addr, alen, size, burst = hs["ar"]
                self._check_addr("AR", aid, addr, alen, size, burst)
                beats = alen + 1
                self.ar_log.append((self.cycle, aid, addr, beats))
                self._seq += 1
                self.rd.append(dict(id=aid, addr=addr, beats=beats, idx=0, seq=self._seq,
                                    ready=self.cycle + self._delay(),
                                    resp=self._resp_for(cfg.rd_err, addr, beats * 8)))
            if hs.get("aw"):
                aid, addr, alen, size, burst = hs["aw"]
                self._check_addr("AW", aid, addr, alen, size, burst)
                self.aw_log.append((self.cycle, aid, addr, alen + 1))
                self.aw_q.append(dict(id=aid, addr=addr, beats=alen + 1, data=[]))
            if hs.get("w"):
                data, strb, last = hs["w"]
                self.w_beats += 1
                if not self.aw_q:
                    self._err("W beat with no outstanding AW (W before AW)")
                else:
                    t = self.aw_q[0]
                    t["data"].append((data, strb))
                    n = len(t["data"])
                    if bool(last) != (n == t["beats"]):
                        self._err(f"WLAST={last} on beat {n} of {t['beats']} (AW {t['addr']:#x} id {t['id']})")
                    if n == t["beats"] or last:
                        self.aw_q.pop(0)
                        resp = self._resp_for(cfg.wr_err, t["addr"], t["beats"] * 8)
                        if resp == OKAY:
                            for i, (d, sb) in enumerate(t["data"]):
                                a = t["addr"] + 8 * i
                                if sb == 0xFF:
                                    self.mem.write64(a, d)
                                else:
                                    for b in range(8):
                                        if sb >> b & 1:
                                            self.mem.write(a + b, bytes([(d >> (8 * b)) & 0xFF]))
                        self._seq += 1
                        self.b_q.append(dict(id=t["id"], resp=resp, seq=self._seq,
                                             ready=self.cycle + self._delay()))
            if hs.get("r"):
                t = self.r_cur["txn"]
                self.r_beats += 1
                t["idx"] += 1
                if t["idx"] == t["beats"]:
                    self.rd.remove(t)
                self.r_cur = None
            if hs.get("b"):
                self.b_q.remove(self.b_cur)
                self.b_cur = None

            # ---- drive outputs for this cycle
            self._drive("arready", int(len(self.rd) < cfg.max_rd and rng.random() < cfg.p_arready))
            n_wr = len(self.aw_q) + len(self.b_q)
            self._drive("awready", int(n_wr < cfg.max_wr and rng.random() < cfg.p_awready))
            self._drive("wready", int(rng.random() < cfg.p_wready))

            if self.r_cur is None and self.rd and rng.random() < cfg.p_rvalid:
                heads = {}
                for t in self.rd:   # oldest per ID only: in order within an ID
                    if t["id"] not in heads:
                        heads[t["id"]] = t
                ready = [t for t in heads.values() if t["ready"] <= self.cycle]
                if ready:
                    t = rng.choice(ready) if cfg.ooo else min(ready, key=lambda x: x["seq"])
                    # without ooo, finish the burst in progress before another
                    if not cfg.ooo:
                        started = [x for x in ready if x["idx"] > 0]
                        if started:
                            t = started[0]
                    a = t["addr"] + 8 * t["idx"]
                    self.r_cur = dict(txn=t)
                    self._drive("rid", t["id"])
                    self._drive("rdata", self.mem.read64(a))
                    self._drive("rresp", t["resp"])
                    self._drive("rlast", int(t["idx"] == t["beats"] - 1))
            self._drive("rvalid", int(self.r_cur is not None))

            if self.b_cur is None and self.b_q and rng.random() < cfg.p_bvalid:
                heads = {}
                for t in self.b_q:
                    if t["id"] not in heads:
                        heads[t["id"]] = t
                ready = [t for t in heads.values() if t["ready"] <= self.cycle]
                if ready:
                    t = rng.choice(ready) if cfg.ooo else min(ready, key=lambda x: x["seq"])
                    self.b_cur = t
                    self._drive("bid", t["id"])
                    self._drive("bresp", t["resp"])
            self._drive("bvalid", int(self.b_cur is not None))

            # ---- sample the master for the next edge
            await ReadOnly()
            s = self.s
            hs = {}
            prev = self._prev
            arvalid = int(s["arvalid"].value)
            if arvalid:
                ar = (int(s["arid"].value), int(s["araddr"].value), int(s["arlen"].value),
                      int(s["arsize"].value), int(s["arburst"].value))
                if prev.get("ar_wait") and prev["ar"] != ar:
                    self._err("AR payload changed while ARVALID && !ARREADY")
                if self._driven["arready"]:
                    hs["ar"] = ar
                prev["ar"] = ar
            elif prev.get("ar_wait"):
                self._err("ARVALID dropped before ARREADY")
            prev["ar_wait"] = arvalid and not self._driven["arready"]

            awvalid = int(s["awvalid"].value)
            if awvalid:
                aw = (int(s["awid"].value), int(s["awaddr"].value), int(s["awlen"].value),
                      int(s["awsize"].value), int(s["awburst"].value))
                if prev.get("aw_wait") and prev["aw"] != aw:
                    self._err("AW payload changed while AWVALID && !AWREADY")
                if self._driven["awready"]:
                    hs["aw"] = aw
                prev["aw"] = aw
            elif prev.get("aw_wait"):
                self._err("AWVALID dropped before AWREADY")
            prev["aw_wait"] = awvalid and not self._driven["awready"]

            wvalid = int(s["wvalid"].value)
            if wvalid:
                w = (int(s["wdata"].value), int(s["wstrb"].value), int(s["wlast"].value))
                if prev.get("w_wait") and prev["w"] != w:
                    self._err("W payload changed while WVALID && !WREADY")
                if self._driven["wready"]:
                    hs["w"] = w
                prev["w"] = w
            elif prev.get("w_wait"):
                self._err("WVALID dropped before WREADY")
            prev["w_wait"] = wvalid and not self._driven["wready"]

            if self.r_cur is not None and int(s["rready"].value):
                hs["r"] = True
            if self.b_cur is not None and int(s["bready"].value):
                hs["b"] = True
