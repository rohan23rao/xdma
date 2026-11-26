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
| 7 | Errors, abort, interrupts | next |
| 8 | TLB + walker, translation tests, soak, review | |
| 9 | Coverage closure, parameter variants | |
| 10 | Performance sweeps and plots | |
| 11 | Lint, Yosys + OpenSTA synthesis on ASAP7, review | |
| 12 | RESULTS, README, DECISIONS, FUTURE, walkthrough | |

## Open issues

- Prefetch bubble (DECISIONS.md, "OPEN (review 4)"): the spec's state machine
  and its "no bubble beyond writeback latency" requirement cannot both hold;
  waiting on the owner.
- Error, abort and translation paths are implemented but not yet tested
  (steps 7 and 8).
