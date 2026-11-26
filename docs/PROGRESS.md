# Progress

Tracks the implementation order in SPEC.md section 19.

| Step | Content | State |
|---|---|---|
| 1 | Skeleton, package, FIFO, skid, RR arbiter + formal | done |
| 2 | addr_gen + formal + unit test | done |
| 3 | Memory model, reference model, CSR, test_csr | done |
| 4 | MM2S path, descriptor fetcher, 1D/2D/4K tests | done |
| 5 | S2MM path, W mux, writeback, loopback, chain | done |
| 6 | All channels, arbiters, assertions, review | done |
| 7 | Errors, abort, interrupts | done |
| 8 | TLB + walker, translation tests, soak, second review | done |
| 10 | Performance sweeps and plots | done |
| 11 | Lint, Yosys + OpenSTA synthesis on ASAP7, review | next (lint done) |
| 12 | Docs: DESIGN_WALKTHROUGH first, RESULTS, README, DECISIONS bug stories, FUTURE | |
| 9 | Coverage closure, parameter variants | |

The owner reordered the remaining steps to 7, 8, 10, 11, 12, 9 so that
RESULTS.md gets real bandwidth, clock, area and power numbers before
coverage closure.

## Open issues

