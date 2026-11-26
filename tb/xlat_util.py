"""Helpers for tests that run with address translation on."""
from env import GCTRL, PT_ROOT
from models.desc_builder import Allocator, DescBuilder
from models.page_table import PageTables
from models.ref_model import Translator

VA_LO, VA_HI = 0x4000_0000, 0xF000_0000   # virtual data buffers
PA_LO, PA_HI = 0x0010_0000, 0x3000_0000   # physical: descriptors, tables, pages


class XlatSetup:
    def __init__(self, env, rng, max_bytes=2048):
        self.env = env
        self.palloc = Allocator(rng, PA_LO, PA_HI)
        self.valloc = Allocator(rng, VA_LO, VA_HI)
        self.pt = PageTables(env.mem, self.palloc, rng)
        self.builder = DescBuilder(rng, self.valloc, max_bytes=max_bytes, dalloc=self.palloc)
        self.xlat = Translator(env.mem.read32, self.pt.root_ppn)

    async def enable(self):
        await self.env.wr(PT_ROOT, self.pt.root_ppn)
        await self.env.wr(GCTRL, 1)

    async def flush(self):
        await self.env.wr(GCTRL, 0x3)       # keep TRANSLATE_EN, pulse TLB_FLUSH

    def map_chain(self, chain, perm=None):
        """Map every page any line of the chain touches."""
        from models.page_table import R, W
        perm = perm if perm is not None else R | W
        for _, d in chain:
            for la in d.line_addrs():
                self.pt.map_range(la, d.line_bytes, perm)

    def chain(self, write, n, **kw):
        ch = self.builder.chain(write, n, **kw)
        self.map_chain(ch)
        return ch
