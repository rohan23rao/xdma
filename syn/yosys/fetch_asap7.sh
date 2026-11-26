#!/usr/bin/env bash
# Fetch the ASAP7 7.5-track standard-cell collateral used by `make syn`:
# RVT, typical corner (TT), NLDM liberty, plus the cell Verilog models for
# gate-level simulation. Source: OpenROAD-flow-scripts (pinned commit),
# sparse checkout. Files land in syn/lib/asap7/ (gitignored, never committed).
set -euo pipefail
ORFS_COMMIT=7aee0ab06c2d77b794ed8ae8f21883af676ce8d0
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DST=$ROOT/syn/lib/asap7
[ -f "$DST/.done" ] && { echo "ASAP7 already in $DST"; exit 0; }
TMP=$(mktemp -d)
git clone -q --filter=blob:none --sparse https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts "$TMP/orfs"
cd "$TMP/orfs"
git checkout -q "$ORFS_COMMIT"
git sparse-checkout set flow/platforms/asap7/lib/NLDM flow/platforms/asap7/verilog/stdcell flow/platforms/asap7/yoSys
mkdir -p "$DST"
P=flow/platforms/asap7
for f in $P/lib/NLDM/asap7sc7p5t_{AO,INVBUF,OA,SIMPLE}_RVT_TT_nldm_*.lib.gz $P/lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_*.lib*; do
  b=$(basename "$f")
  case "$b" in *.gz) gunzip -c "$f" > "$DST/${b%.gz}" ;; *) cp "$f" "$DST/$b" ;; esac
done
cp -r $P/verilog/stdcell "$DST/verilog"
cp $P/yoSys/cells_clkgate_R.v "$DST/"
echo "$ORFS_COMMIT" > "$DST/.done"
rm -rf "$TMP"
ls "$DST"
