"""Reference model for XDMA (spec sections 6, 7, 8, 10).

Pure Python, no simulator dependencies. Given memory contents, page tables
and descriptor chains it predicts what the hardware must do.
"""
from dataclasses import dataclass, field

MASK32 = 0xFFFF_FFFF

# Error codes (section 10)
ERR_NONE, ERR_DESC_INVALID, ERR_DESC_FETCH, ERR_RD_RESP, ERR_WR_RESP, \
    ERR_XLAT_FAULT, ERR_STREAM_LEN, ERR_ABORTED = range(8)
ERR_NAMES = ["NONE", "DESC_INVALID", "DESC_FETCH", "RD_RESP", "WR_RESP",
             "XLAT_FAULT", "STREAM_LEN", "ABORTED"]


def split_bursts(addr, line_bytes, lines, stride, max_burst=16):
    """Section 7.2 burst splitter, byte for byte. Yields (va, beats, last)."""
    out = []
    line_addr = addr & MASK32
    for y in range(lines):
        off = 0
        while off < line_bytes:
            a = (line_addr + off) & MASK32
            to_4k = 4096 - (a % 4096)
            nbytes = min(max_burst * 8, line_bytes - off, to_4k)
            last = (y == lines - 1) and (off + nbytes == line_bytes)
            out.append((a, nbytes // 8, last))
            off += nbytes
        line_addr = (line_addr + stride) & MASK32
    return out


def to_signed32(v):
    v &= MASK32
    return v - (1 << 32) if v & 0x8000_0000 else v


@dataclass
class Desc:
    """One descriptor (section 6). stride is a Python int (may be negative)."""
    addr: int
    line_bytes: int
    lines: int = 1
    stride: int = 0
    nxt: int = 0
    last: bool = True
    irq: bool = False
    eop: bool = True

    def words(self):
        """The four 64-bit words as stored in memory (status word zero)."""
        w0 = (self.nxt & MASK32) | ((self.addr & MASK32) << 32)
        w1 = (self.line_bytes & 0xFFFF) | ((self.lines & 0xFFFF) << 16) | ((self.stride & MASK32) << 32)
        w2 = int(self.last) | (int(self.irq) << 1) | (int(self.eop) << 2)
        return [w0, w1, w2, 0]

    def to_bytes(self):
        return b"".join(w.to_bytes(8, "little") for w in self.words())

    def valid(self):
        return (self.addr % 8 == 0 and self.line_bytes % 8 == 0 and self.stride % 8 == 0
                and self.line_bytes != 0 and self.lines != 0
                and (self.last or self.nxt % 32 == 0))

    def nbytes(self):
        return self.line_bytes * self.lines

    def line_addrs(self):
        a = self.addr
        for _ in range(self.lines):
            yield a & MASK32
            a = (a + self.stride) & MASK32

    def bursts(self, max_burst=16):
        return split_bursts(self.addr, self.line_bytes, self.lines, self.stride, max_burst)


def status_word(done, err, nbytes):
    """Descriptor word 3 as written back by the engine."""
    return int(done) | ((err & 7) << 1) | ((nbytes & MASK32) << 32)


def decode_desc(raw):
    """Parse 32 bytes of memory into a Desc."""
    w = [int.from_bytes(raw[i * 8:(i + 1) * 8], "little") for i in range(4)]
    return Desc(addr=w[0] >> 32, nxt=w[0] & MASK32, line_bytes=w[1] & 0xFFFF,
                lines=(w[1] >> 16) & 0xFFFF, stride=to_signed32(w[1] >> 32),
                last=bool(w[2] & 1), irq=bool(w[2] & 2), eop=bool(w[2] & 4))


class Translator:
    """Sv32-style two-level walk (section 8.1) over a read32(pa) callback."""

    def __init__(self, read32, pt_root):
        self.read32 = read32
        self.pt_root = pt_root

    def translate(self, va, write):
        """Returns (pa, None) or (None, reason)."""
        vpn1, vpn0, off = (va >> 22) & 0x3FF, (va >> 12) & 0x3FF, va & 0xFFF
        pte1 = self.read32(((self.pt_root << 12) + vpn1 * 4) & MASK32)
        if not pte1 & 1:
            return None, "l1_invalid"
        if pte1 & 0xE:
            return None, "megapage"
        ppn1 = (pte1 >> 10) & 0xFFFFF
        pte0 = self.read32(((ppn1 << 12) + vpn0 * 4) & MASK32)
        if not pte0 & 1:
            return None, "l0_invalid"
        if write and not pte0 & 4:
            return None, "perm"
        if not write and not pte0 & 2:
            return None, "perm"
        return ((((pte0 >> 10) & 0xFFFFF) << 12) | off), None


@dataclass
class ChainResult:
    """Expected outcome of running one descriptor chain on one channel."""
    stream: list = field(default_factory=list)       # MM2S: list of (data, tlast)
    writes: dict = field(default_factory=dict)        # S2MM: pa -> byte
    status: dict = field(default_factory=dict)        # desc addr -> word3
    done_irq: bool = False
    err: int = ERR_NONE
    err_addr: int = 0
    descs_done: int = 0
    nbytes: int = 0


def walk_chain(read_bytes, head, limit=4096):
    """Follow NEXT pointers from head. Returns [(addr, Desc)] up to and
    including the first LAST or invalid descriptor."""
    out = []
    a = head
    while len(out) < limit:
        d = decode_desc(read_bytes(a, 32))
        out.append((a, d))
        if not d.valid() or d.last:
            break
        a = d.nxt
    return out


def predict_mm2s(read_bytes, chain, xlat=None, max_burst=16):
    """Expected MM2S result for a chain with no AXI errors.
    xlat: Translator or None (TRANSLATE_EN = 0)."""
    res = ChainResult()
    for daddr, d in chain:
        if not d.valid():
            res.err, res.err_addr = ERR_DESC_INVALID, daddr
            return res
        beats = []
        for va, nb, _ in d.bursts(max_burst):
            pa = va
            if xlat is not None:
                pa, why = xlat.translate(va, write=False)
                if pa is None:
                    res.err, res.err_addr = ERR_XLAT_FAULT, va
                    return res
            data = read_bytes(pa, nb * 8)
            beats += [int.from_bytes(data[i:i + 8], "little") for i in range(0, nb * 8, 8)]
        for i, b in enumerate(beats):
            res.stream.append((b, int(d.eop and i == len(beats) - 1)))
        res.status[daddr] = status_word(1, ERR_NONE, d.nbytes())
        res.done_irq |= d.irq
        res.descs_done += 1
        res.nbytes += d.nbytes()
    return res


def predict_s2mm(chain, stream_bytes, xlat=None, max_burst=16):
    """Expected S2MM result: the incoming bytes are written in burst order.
    Returns ChainResult with .writes = {pa: byte}."""
    res = ChainResult()
    pos = 0
    for daddr, d in chain:
        if not d.valid():
            res.err, res.err_addr = ERR_DESC_INVALID, daddr
            return res
        for va, nb, _ in d.bursts(max_burst):
            pa = va
            if xlat is not None:
                pa, why = xlat.translate(va, write=True)
                if pa is None:
                    res.err, res.err_addr = ERR_XLAT_FAULT, va
                    return res
            for i in range(nb * 8):
                res.writes[(pa + i) & MASK32] = stream_bytes[pos + i]
            pos += nb * 8
        res.status[daddr] = status_word(1, ERR_NONE, d.nbytes())
        res.done_irq |= d.irq
        res.descs_done += 1
        res.nbytes += d.nbytes()
    return res
