"""Channel jobs and scoreboard.

A ChannelJob runs one descriptor chain on one channel and checks it against
the reference model:
- MM2S: every stream beat (data and TLAST) in order, as it arrives;
- S2MM: the memory image of every destination byte;
- both: descriptor status words, STATUS / BYTES / CUR_DESC registers, the
  DONE interrupt bit, and that the chain did not write any byte outside its
  destinations and status words (checked globally by check_memory()).
"""
import cocotb

from common import fail
from env import BYTES, CUR_DESC, IRQ_STATUS, STATUS
from models.ref_model import (ERR_NAMES, ERR_NONE, predict_mm2s, predict_s2mm, status_word)


class ChannelJob:
    def __init__(self, env, c, chain, xlat=None, src_fill=True):
        self.env, self.c, self.chain, self.xlat = env, c, chain, xlat
        p = env.p
        self.write = c >= p["NUM_MM2S"]
        self.max_burst = p["MAX_BURST"]
        mem, rng = env.mem, env.rng
        for a, d in chain:
            mem.write(a, d.to_bytes())
        if self.write:
            total = sum(d.nbytes() for _, d in chain)
            self.stream_bytes = bytes(rng.getrandbits(8) for _ in range(total))
            self.exp = predict_s2mm(chain, self.stream_bytes, xlat, self.max_burst)
        else:
            if src_fill:
                for _, d in chain:
                    for la in d.line_addrs():
                        pa = la
                        if xlat is not None:
                            pa, _ = xlat.translate(la, write=False)
                            if pa is None:
                                continue
                        mem.write(pa, bytes(rng.getrandbits(8) for _ in range(d.line_bytes)))
            self.exp = predict_mm2s(mem.read, chain, xlat, self.max_burst)
        self.mon_start = len(env.mons[c].beats) if not self.write else 0

    def stream_beats(self):
        """S2MM input beats: TLAST at the end of each EOP descriptor."""
        out, pos = [], 0
        for _, d in self.chain:
            n = d.nbytes() // 8
            for i in range(n):
                data = int.from_bytes(self.stream_bytes[pos:pos + 8], "little")
                out.append((data, 0xFF, int(d.eop and i == n - 1)))
                pos += 8
        return out

    async def run(self, timeout=400000):
        env, c = self.env, self.c
        await env.start_chain(c, self.chain[0][0])
        if self.write:
            await env.send_beats(c - env.p["NUM_MM2S"], self.stream_beats())
        self.st = await env.wait_idle(c, timeout)

    async def check(self):
        env, c, exp = self.env, self.c, self.exp
        tag = f"ch{c}"
        err = (self.st >> 1) & 7
        if err != exp.err:
            fail(f"{tag}: STATUS.ERR={ERR_NAMES[err]} expected {ERR_NAMES[exp.err]}")
        ndone = self.st >> 8
        if ndone != exp.descs_done % (1 << 24):
            fail(f"{tag}: STATUS descriptors completed {ndone}, expected {exp.descs_done}")
        nbytes = await env.ch_rd(c, BYTES)
        if exp.err == ERR_NONE and nbytes != exp.nbytes & 0xFFFF_FFFF:
            fail(f"{tag}: BYTES={nbytes}, expected {exp.nbytes}")
        cur = await env.ch_rd(c, CUR_DESC)
        if exp.err == ERR_NONE and cur != self.chain[-1][0]:
            fail(f"{tag}: CUR_DESC={cur:#x}, expected {self.chain[-1][0]:#x}")
        for a, word in exp.status.items():
            got = env.mem.read64(a + 0x18)
            if got != word:
                fail(f"{tag}: status word of descriptor {a:#x} = {got:#018x}, expected {word:#018x}")
        if exp.done_irq:
            irqs = await env.rd(IRQ_STATUS)
            if not irqs >> c & 1:
                fail(f"{tag}: DONE interrupt bit not set")
        if not self.write:
            got = env.mons[c].beats[self.mon_start:]
            if len(got) != len(exp.stream):
                fail(f"{tag}: {len(got)} stream beats, expected {len(exp.stream)}")
            for i, ((_, d, l), (ed, el)) in enumerate(zip(got, exp.stream)):
                if d != ed or l != el:
                    fail(f"{tag}: beat {i}: data {d:#018x} tlast {l}, expected {ed:#018x} tlast {el}")
        else:
            for a, b in exp.writes.items():
                got = env.mem.read(a, 1)[0]
                if got != b:
                    fail(f"{tag}: memory byte {a:#x} = {got:#04x}, expected {b:#04x}")

    def allowed_writes(self):
        """Byte addresses this job may modify."""
        s = set(self.exp.writes) if self.write else set()
        for a, _ in self.chain:
            s.update(range(a + 0x18, a + 0x20))
        return s


def check_memory(env, before, jobs):
    """No byte outside the jobs' destinations and status words changed."""
    allowed = set()
    for j in jobs:
        allowed |= j.allowed_writes()
    after = env.mem.snapshot()
    bad = [a for a in env.mem.diff(before, after) if a not in allowed]
    if bad:
        fail(f"{len(bad)} bytes changed outside any destination, first at {bad[0]:#x}")


async def run_jobs(env, jobs):
    """Run jobs concurrently, then check every one of them."""
    tasks = [cocotb.start_soon(j.run()) for j in jobs]
    for t in tasks:
        await t
    await env.quiesce()
    for j in jobs:
        await j.check()
