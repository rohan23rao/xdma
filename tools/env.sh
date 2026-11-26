# Source this before running any make target:  source tools/env.sh
# Puts the locally built open-source tools on PATH (see docs/ENV.md).
export VERILATOR_ROOT=${VERILATOR_ROOT:-/opt/verilator/share/verilator}
export PATH=/opt/verilator/bin:/opt/oss/bin:$PATH
