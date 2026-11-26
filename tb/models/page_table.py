"""Sv32-style page-table builder (spec 8.1, 14.1).

Builds two-level page tables in the testbench's SparseMem: maps VA pages to
scattered physical pages, and can mark pages invalid or read/write-only,
turn a level-1 entry into an (unsupported) megapage, or invalidate a whole
level-1 entry, to create every translation fault.

PTE: [31:10] PPN (only [29:10] used), [3] X, [2] W, [1] R, [0] V.
"""
V, R, W, X = 1, 2, 4, 8


class PageTables:
    def __init__(self, mem, palloc, rng):
        self.mem, self.palloc, self.rng = mem, palloc, rng
        self.root = palloc.page()               # level-1 table, 4 KB
        self.mem.write(self.root, bytes(4096))
        self.l0 = {}                             # vpn1 -> level-0 table PA
        self.maps = {}                           # va page -> pa page

    @property
    def root_ppn(self):
        return self.root >> 12

    def _l1_addr(self, vpn1):
        return self.root + 4 * vpn1

    def _l0_table(self, vpn1):
        if vpn1 not in self.l0:
            t = self.palloc.page()
            self.mem.write(t, bytes(4096))
            self.l0[vpn1] = t
            self.mem.write32(self._l1_addr(vpn1), ((t >> 12) << 10) | V)
        return self.l0[vpn1]

    def pte_addr(self, va):
        """Physical address of the level-0 PTE that maps va."""
        vpn1, vpn0 = (va >> 22) & 0x3FF, (va >> 12) & 0x3FF
        return self._l0_table(vpn1) + 4 * vpn0

    def map(self, va_page, pa_page=None, perm=R | W):
        va_page &= ~0xFFF
        if pa_page is None:
            pa_page = self.palloc.page()
        self.mem.write32(self.pte_addr(va_page), ((pa_page >> 12) << 10) | perm | V)
        self.maps[va_page] = pa_page
        return pa_page

    def map_range(self, va, size, perm=R | W):
        """Map every page touched by [va, va+size) to a fresh random page."""
        first, last = va & ~0xFFF, (va + size - 1) & ~0xFFF
        for pg in range(first, last + 4096, 4096):
            if pg not in self.maps:
                self.map(pg, perm=perm)

    def set_perm(self, va_page, perm):
        self.mem.write32(self.pte_addr(va_page), ((self.maps[va_page] >> 12) << 10) | perm)

    def invalidate_l0(self, va_page):
        a = self.pte_addr(va_page)
        self.mem.write32(a, self.mem.read32(a) & ~V)

    def invalidate_l1(self, va):
        vpn1 = (va >> 22) & 0x3FF
        a = self._l1_addr(vpn1)
        self.mem.write32(a, self.mem.read32(a) & ~V)

    def make_megapage(self, va):
        vpn1 = (va >> 22) & 0x3FF
        a = self._l1_addr(vpn1)
        self.mem.write32(a, self.mem.read32(a) | R)

    def l1_pte_addr(self, va):
        return self._l1_addr((va >> 22) & 0x3FF)
