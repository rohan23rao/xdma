#!/usr/bin/env bash
# make lint: Verilator -Wall (waivers in tools/lint/verilator_waivers.vlt,
# each justified) plus a full elaboration of the RTL and the assertion
# modules by a second front end (slang, through Yosys). Fails on any warning.
set -euo pipefail
cd "$(dirname "$0")/.."
RTL="rtl/xdma_pkg.sv $(ls rtl/xdma_*.sv | grep -v xdma_pkg | tr "\n" " ")"
echo "== Verilator -Wall (RTL, top xdma_top)"
out=$(verilator --lint-only -Wall -Isva -Irtl tools/lint/verilator_waivers.vlt $RTL --top-module xdma_top 2>&1 || true)
echo "$out" | grep -E "^%(Warning|Error)" && { echo "lint: FAIL (Verilator)"; exit 1; }
echo "   clean"
echo "== Verilator -Wall (RTL + assertions + testbench wrapper)"
out=$(verilator --lint-only -Wall -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Isva -Irtl tools/lint/verilator_waivers.vlt \
      $RTL sva/*_sva.sv sva/xdma_bind.sv tb/hdl/xdma_tb.sv --top-module xdma_tb 2>&1 || true)
echo "$out" | grep -E "^%(Warning|Error)" && { echo "lint: FAIL (Verilator, with assertions)"; exit 1; }
echo "   clean"
echo "== slang elaboration (Yosys read_slang, synthesis view)"
out=$(yosys -q -p "read_slang -j 1 --top xdma_top $RTL" 2>&1 || true)
echo "$out" | grep -iE "warning|error" && { echo "lint: FAIL (slang)"; exit 1; }
echo "   clean"
echo "lint: PASS"
