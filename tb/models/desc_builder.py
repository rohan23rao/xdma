"""Constrained-random descriptor generator (spec 14.1).

Allocator hands out non-overlapping memory regions so sources, destinations
and descriptors never collide. DescBuilder draws descriptor shapes with
weighted corner cases:
- start addresses within 128 B below a 4 KB boundary;
- STRIDE = LINE_BYTES (contiguous), > LINE_BYTES, negative, 0 (MM2S only);
- LINES = 1; LINE_BYTES = 8 and large;
- chains of 1..64 descriptors.
"""
from .ref_model import Desc


class Allocator:
    """Bump allocator over [lo, hi) with random gaps; 8-byte granules."""

    def __init__(self, rng, lo=0x0010_0000, hi=0xF000_0000):
        self.rng = rng
        # start anywhere in the lower half of the range, so across seeds
        # buffers and descriptors exercise high and low address bits
        self.next = lo + (rng.randrange(0, (hi - lo) // 2) & ~0xFFF)
        self.hi = hi

    def alloc(self, size, align=8, near_4k=False):
        a = (self.next + self.rng.randrange(0, 4096, 8) + align - 1) // align * align
        if near_4k:
            # start within 128 B below a 4 KB boundary
            a = (a // 4096 + 1) * 4096 - 8 * self.rng.randint(1, 16)
            a = (a + align - 1) // align * align
        if a + size > self.hi:
            raise RuntimeError("allocator out of space")
        self.next = a + size + 64
        return a

    def page(self):
        a = (self.next + 4095) // 4096 * 4096
        self.next = a + 4096
        return a


class DescBuilder:
    def __init__(self, rng, alloc, max_bytes=16384, dalloc=None):
        """alloc places data buffers (virtual addresses when translation is
        on); dalloc places descriptors, which are always physical."""
        self.rng = rng
        self.alloc = alloc
        self.dalloc = dalloc or alloc
        self.max_bytes = max_bytes

    def shape(self, write, kind=None):
        """Returns (line_bytes, lines, stride)."""
        r = self.rng
        lb = r.choice([8, 8, 8 * r.randint(1, 16), 8 * r.randint(2, 64), 8 * r.randint(16, 512),
                       8 * r.randint(512, 1024)])
        lines = r.choice([1, 1, 1, r.randint(2, 4), r.randint(2, 16)])
        lines = max(1, min(lines, self.max_bytes // lb))
        kinds = ["contig", "gap", "neg"] + ([] if write else ["zero"])
        kind = kind or r.choice(kinds)
        if lines == 1:
            stride = r.choice([0, lb, 8 * r.randint(-100, 100)])
        elif kind == "contig":
            stride = lb
        elif kind == "gap":
            stride = lb + 8 * r.randint(1, 600)
        elif kind == "neg":
            stride = -(lb + 8 * r.randint(0, 600))
        else:
            stride = 0
        return lb, lines, stride

    def place(self, lb, lines, stride, near_4k=None):
        """Allocate the footprint of a 2D region; returns the line-0 address."""
        near_4k = self.rng.random() < 0.3 if near_4k is None else near_4k
        span = abs(stride) * (lines - 1) + lb
        base = self.alloc.alloc(span, near_4k=near_4k and stride >= 0)
        return base if stride >= 0 else base + abs(stride) * (lines - 1)

    def desc(self, write, eop=None, irq=None, kind=None):
        lb, lines, stride = self.shape(write, kind)
        d = Desc(addr=self.place(lb, lines, stride), line_bytes=lb, lines=lines, stride=stride,
                 eop=self.rng.random() < 0.7 if eop is None else eop,
                 irq=self.rng.random() < 0.3 if irq is None else irq)
        return d

    def chain(self, write, n, **kw):
        """n descriptors linked by NEXT, LAST on the final one, at random
        32-byte aligned addresses. Returns [(addr, Desc)]."""
        descs = [self.desc(write, **kw) for _ in range(n)]
        addrs = [self.dalloc.alloc(32, align=32) for _ in range(n)]
        for i, d in enumerate(descs):
            d.last = (i == n - 1)
            d.nxt = addrs[i + 1] if i + 1 < n else self.rng.choice([0, addrs[0], 0xDEAD_BEE0])
        return list(zip(addrs, descs))
