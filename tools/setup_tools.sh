#!/usr/bin/env bash
# Installs the open-source toolchain used by this repo (Ubuntu 24.04).
# Re-runnable; skips anything already present. See docs/ENV.md.
set -euo pipefail
VERILATOR_TAG=v5.054
YICES_TAG=Yices-2.6.5
ABC_COMMIT=d0c5502
OPENSTA_COMMIT=39f9dec68a07d658b38d7d99a493479e077c1c30
CUDD_TAG=cudd-3.0.0
apt-get install -y -qq git autoconf flex bison help2man libfl-dev zlib1g-dev \
    make g++ perl z3 gperf libgmp-dev cmake tcl-dev swig libeigen3-dev libfmt-dev \
    libgtest-dev >/dev/null

mkdir -p /opt/src /opt/oss/bin
if [ ! -x /opt/verilator/bin/verilator ]; then
  rm -rf /opt/src/verilator
  git clone -q --depth 1 -b "$VERILATOR_TAG" https://github.com/verilator/verilator /opt/src/verilator
  (cd /opt/src/verilator && autoconf && ./configure --prefix=/opt/verilator && make -j"$(nproc)" && make install)
fi
if [ ! -x /opt/oss/bin/yosys-abc ]; then
  rm -rf /opt/src/yosys-abc
  git clone -q https://github.com/YosysHQ/abc /opt/src/yosys-abc
  (cd /opt/src/yosys-abc && git checkout -q "$ABC_COMMIT" && make -j"$(nproc)" ABC_USE_NO_READLINE=1 ABC_USE_PIC=1 abc && cp abc /opt/oss/bin/yosys-abc)
fi
if [ ! -x /opt/oss/bin/yices-smt2 ]; then
  rm -rf /opt/src/yices2
  git clone -q --depth 1 -b "$YICES_TAG" https://github.com/SRI-CSL/yices2 /opt/src/yices2
  (cd /opt/src/yices2 && autoconf && ./configure --prefix=/opt/oss && make -j2 && make install)
fi
if [ ! -x /opt/oss/bin/sta ]; then
  rm -rf /opt/src/cudd /opt/src/opensta
  git clone -q https://github.com/ivmai/cudd /opt/src/cudd
  (cd /opt/src/cudd && git checkout -q "$CUDD_TAG" && autoreconf -fi && ./configure --prefix=/opt/cudd \
     && make -j"$(nproc)" && make install)
  git clone -q https://github.com/The-OpenROAD-Project/OpenSTA /opt/src/opensta
  (cd /opt/src/opensta && git checkout -q "$OPENSTA_COMMIT" && mkdir -p build && cd build \
     && cmake .. -DCUDD_DIR=/opt/cudd -DCMAKE_INSTALL_PREFIX=/opt/oss && make -j"$(nproc)" && make install)
fi

python3 -m pip install -q cocotb==2.1.0 cocotbext-axi==0.1.28 pytest matplotlib numpy \
    yowasp-yosys==0.71.0.0.post1285 python-constraint2if [ ! -x /opt/oss/bin/yices-smt2 ]; then
  rm -rf /opt/src/yices2
  git clone -q --depth 1 -b "$YICES_TAG" https://github.com/SRI-CSL/yices2 /opt/src/yices2
  (cd /opt/src/yices2 && autoconf && ./configure --prefix=/opt/oss && make -j2 && make install)
fi

python3 -m pip install -q --no-deps cocotb-coverage==2.0

# SymbiYosys expects plain tool names; point them at the YoWASP builds.
for t in yosys yosys-smtbmc yosys-witness sby; do
  printf '#!/bin/sh\nexec yowasp-%s "$@"\n' "$t" > /opt/oss/bin/$t
  chmod +x /opt/oss/bin/$t
done
echo "tools ready; now: source tools/env.sh"
