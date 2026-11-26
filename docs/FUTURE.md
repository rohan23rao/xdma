# Future work

Ideas deliberately left out of v1 (spec section 1 non-goals, review findings
and measured limits). Each entry says why it would help.

## Overlap the next descriptor with the status writeback

Decouple completion from execution: once the current descriptor's data is
done, start the next (already prefetched) descriptor's address generation
and reads while the writeback AW/W/B is still in flight, and report DONE
for the old descriptor when its B returns. Today the 7.1 state machine
serializes them (DECISIONS.md D-045), so every descriptor boundary costs one
writeback round trip plus one first-read latency.

Motivation (measured): see the descriptor-overhead experiment in
RESULTS.md (64 x 64 B descriptors vs. one 4 KB descriptor).

What it needs: per-descriptor tags on data-path completion and byte counts,
a small completion queue in the controller, and error handling that can
attribute a failure to the right descriptor while two are live.

## Pseudo-LRU TLB replacement

The TLB uses a round-robin replacement pointer (spec 8.2). Tree pseudo-LRU
keeps hot pages (for example the page tables of several channels' streams)
resident longer when more than eight pages are live.

## Spec non-goals

Weighted / QoS arbitration, unaligned addresses and byte-granular lengths,
superpages, several outstanding page walks, several AXI IDs per channel for
data with a reorder buffer, multiple clock domains, data-width conversion,
interrupt coalescing. None are needed for the v1 use cases; each is listed
in SPEC.md section 1.

## Pipelined translation stage

With translation on, the one-entry translation stage holds a burst through
LOOKUP -> RESP -> READY, so one channel issues at most one burst every three
cycles even on TLB hits. Long bursts hide this (the data takes 16 cycles),
but 1-beat bursts (8-byte lines) are capped near 33%. A two-entry stage, or
a lookup issued for the next burst while the current one waits for its
AR/AW grant, would remove it. The translation-cost experiment in
RESULTS.md shows the cost for linear and page-per-line access.
