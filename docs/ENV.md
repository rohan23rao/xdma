# Environment

## Toolchain actually used

The original spec targets Synopsys VCS/Verdi, Cadence JasperGold and Synopsys
DC/PT. None of those were available on the build machine (no binaries, no
`module` system, no license variables), so the project owner chose a fully
open-source flow (DECISIONS.md D-001). Synthesis uses Yosys with the ASAP7
liberty and OpenSTA. Every number in this repo comes from the tools below.

| Purpose | Tool | Version |
|---|---|---|
| Simulator | Verilator (built from source) | 5.054 (rev v5.054) |
| Testbench | cocotb | 2.1.0 |
| AXI-Lite master, AXI-Stream source/sink | cocotbext-axi | 0.1.28 |
| Functional coverage | cocotb-coverage | 2.0 (with python-constraint2 2.9.0) |
| Formal front end | Yosys + yosys-slang (YoWASP build) | Yosys 0.71 (b4c586fd3), slang rev 2cfcfbe |
| Formal driver | SymbiYosys (YoWASP build) | bundled with yowasp-yosys 0.71.0.0.post1285 |
| SMT solver | Yices | 2.6.5 |
| SMT solver (fallback) | Z3 | 4.8.12 |
| PDR engine | Berkeley ABC (YosysHQ fork) | commit d0c5502 |
| Synthesis | Yosys (YoWASP build), ABC mapping to ASAP7 | Yosys 0.71 |
| Static timing and power | OpenSTA (built from source) | commit 39f9dec |
| BDD library for OpenSTA | CUDD | 3.0.0 |
| Standard cells | ASAP7 7.5-track RVT, TT corner, NLDM (from OpenROAD-flow-scripts) | fetched by `syn/yosys/fetch_asap7.sh` |
| Plots | matplotlib / numpy | 3.11.2 / 2.5.3 |
| Python | CPython | 3.13.18 |
| C++ compiler | g++ | 13.3.0 |
| OS | Ubuntu | 24.04.5 LTS |

## Setup

```sh
sudo ./tools/setup_tools.sh   # builds Verilator, Yices, ABC, CUDD, OpenSTA; pip-installs the rest
source tools/env.sh           # puts /opt/verilator/bin and /opt/oss/bin on PATH
```

`setup_tools.sh` creates small wrappers (`yosys`, `sby`, `yosys-smtbmc`, ...)
in `/opt/oss/bin` that call the YoWASP builds, because SymbiYosys looks for the
plain tool names.

## Notes

- yosys-slang is run with `-j 1`: the WebAssembly build cannot create threads.
- yosys-slang supports only boolean concurrent properties (`assert property
  (@(posedge clk) disable iff (...) expr)`) plus `$past`/`$stable`; it rejects
  `|->`, `|=>` and `##N`. All properties under `sva/` are written in that
  subset so the same files are used by simulation and formal
  (see DECISIONS.md, D-001).
- Z3 4.8.12 ran out of memory on the FIFO cover task; Yices is the default
  SMT solver for every target.
