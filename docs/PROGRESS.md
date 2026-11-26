# Progress

Tracks the implementation order in SPEC.md section 19.

| Step | Content | State |
|---|---|---|
| 1 | Skeleton, package, FIFO, skid, RR arbiter + formal | done |
| 2 | addr_gen + formal + unit test | done |
| 3 | Memory model, reference model, CSR, test_csr | done |
| 4 | MM2S path, descriptor fetcher, 1D/2D/4K tests | done |
| 5 | S2MM path, W mux, writeback, loopback, chain | done |
| 6 | All channels, arbiters, assertions, review | next |
| 7 | Errors, abort, interrupts | |
| 8 | TLB + walker, translation tests, soak, review | |
| 9 | Coverage closure, parameter variants | |
| 10 | Performance sweeps and plots | |
| 11 | Lint, review (synthesis out of scope, D-001) | |
| 12 | RESULTS, README, DECISIONS, FUTURE, walkthrough | |

## Open issues

None.
