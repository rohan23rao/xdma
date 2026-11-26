#!/usr/bin/env python3
"""Build and run cocotb tests on Verilator.

    python3 tb/run.py                         # every test, default seed
    python3 tb/run.py --test test_csr --seed 7 --waves
    python3 tb/run.py --test test_chain --params FIFO_DEPTH=32,MAX_OUT_CH=4,MAX_BURST=8

Each parameter set gets its own build directory under tb/build/, so builds
are reused across tests and seeds. Waves are FST (open with GTKWave or
Surfer). A failing test prints its seed and the command to reproduce it.
"""
import argparse, hashlib, os, pathlib, random, sys, xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
TB = ROOT / "tb"
sys.path.insert(0, str(TB))

from cocotb_tools.runner import get_runner  # noqa: E402

# name -> toplevel, whether it is randomized (runs across seeds in regress)
TESTS = {
    "test_addr_gen":        dict(top="xdma_addr_gen", random=True),
    "test_csr":             dict(top="xdma_tb", random=False),
    "test_mm2s_1d":         dict(top="xdma_tb", random=True),
    "test_2d":              dict(top="xdma_tb", random=True),
    "test_boundary_4k":     dict(top="xdma_tb", random=True),
    "test_s2mm_1d":         dict(top="xdma_tb", random=True),
    "test_loopback_copy":   dict(top="xdma_tb", random=True),
    "test_chain":           dict(top="xdma_tb", random=True),
    "test_all_channels":    dict(top="xdma_tb", random=True),
    "test_axi_errors":      dict(top="xdma_tb", random=True),
    "test_stream_len":      dict(top="xdma_tb", random=True),
    "test_abort":           dict(top="xdma_tb", random=True),
    "test_irq":             dict(top="xdma_tb", random=True),
    "test_translate_basic": dict(top="xdma_tb", random=True),
    "test_translate_fault": dict(top="xdma_tb", random=True),
    "test_tlb_flush":       dict(top="xdma_tb", random=True),
    "test_soak":            dict(top="xdma_tb", random=True),
    # performance tests (spec 16); also regressions with pass thresholds
    "perf_latency":         dict(top="xdma_tb", random=False),
    "perf_saturate":        dict(top="xdma_tb", random=False),
    "perf_desc":            dict(top="xdma_tb", random=False),
    "perf_xlat":            dict(top="xdma_tb", random=False),
    "perf_idle":            dict(top="xdma_tb", random=False),
    # gate-level simulation (zero delay) of the synthesized netlist
    "gls_loopback_copy":    dict(top="xdma_tb", random=True, module="test_loopback_copy", gls=True),
}

GLS_NETLIST = os.environ.get("XDMA_GLS_NETLIST", str(ROOT / "syn" / "out" / "netlist_final_nocg.v"))


def gls_sources():
    """ASAP7 combinational cell models, functional sequential models, the
    netlist and the testbench wrapper (no RTL, no assertions)."""
    lib = ROOT / "syn" / "lib" / "asap7" / "verilog"
    cells = [lib / f for f in ("asap7sc7p5t_AO_RVT_TT_201020.v", "asap7sc7p5t_INVBUF_RVT_TT_201020.v",
                               "asap7sc7p5t_OA_RVT_TT_201020.v", "asap7sc7p5t_SIMPLE_RVT_TT_201020.v")]
    return cells + [ROOT / "syn" / "gls" / "asap7_seq_models.v", pathlib.Path(GLS_NETLIST),
                    TB / "hdl" / "xdma_tb.sv"]

RTL_ORDER = ["xdma_pkg.sv"]


def sources():
    rtl = sorted((ROOT / "rtl").glob("*.sv"), key=lambda p: (p.name not in RTL_ORDER, p.name))
    sva = sorted((ROOT / "sva").glob("*_sva.sv"))
    binds = [ROOT / "sva" / "xdma_bind.sv"]
    return rtl + sva + [b for b in binds if b.exists()] + sorted((TB / "hdl").glob("*.sv"))


def parse_params(text):
    params = {}
    for kv in filter(None, (text or "").split(",")):
        k, v = kv.split("=")
        params[k.strip()] = int(v)
    return params


_RUNNERS = {}


def build(top, params, cov=False, waves=False, defines=None, gls=False):
    """Compile one (top, params) configuration; returns (runner, build dir)."""
    defines = dict(defines or {})
    if gls:
        defines["XDMA_GLS"] = 1
    saif = bool(os.environ.get("XDMA_SAIF"))
    key = f"{top}|{sorted(params.items())}|cov={cov}|waves={waves}|saif={saif}|{sorted(defines.items())}"
    if gls:
        key += f"|gls={GLS_NETLIST}"
    tag = hashlib.sha1(key.encode()).hexdigest()[:10]
    bdir = TB / "build" / f"{top}_{tag}"
    args = ["--assert", "-Wno-fatal", "-Wno-lint", "-Wno-style",
            "--x-assign", "unique", "--x-initial", "unique",
            "-j", os.environ.get("VERILATOR_JOBS", "4")]
    if cov:
        args += ["--coverage-line", "--coverage-toggle", "--coverage-user"]
    if os.environ.get("XDMA_SAIF"):
        args += ["--trace-saif"]            # switching activity for power analysis
    elif waves:
        args += ["--trace-fst", "--trace-structs"]
    if bdir in _RUNNERS:
        return _RUNNERS[bdir], bdir
    runner = get_runner("verilator")
    if os.environ.get("XDMA_SKIP_BUILD") and (bdir / "Vtop.mk").exists():
        # Parallel regression workers: the parent already built this config;
        # set up the runner's state without running any build command.
        runner._execute = lambda *a, **k: None
    # the cocotb runner passes VM_TRACE=<waves> to make, so SAIF needs waves on
    if gls:
        # a ~100k-cell netlist: split the generated C++ and compile it
        # without optimization, or cc1plus needs more than 7 GB per file
        args = args + ["-Wno-SPECIFYIGN", "--no-timing", "--output-split", "5000",
                       "--output-split-cfuncs", "500", "-MAKEFLAGS", "OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0"]
    runner.build(sources=gls_sources() if gls else sources(), includes=[ROOT / "sva", ROOT / "rtl"],
                 hdl_toplevel=top, parameters=params, defines=defines,
                 build_args=args, build_dir=bdir, waves=waves or saif,
                 log_file=None)
    runner.__dict__.pop("_execute", None)
    _RUNNERS[bdir] = runner
    return runner, bdir


def build_for(test, params=None, cov=False, waves=False):
    t = TESTS[test]
    return build(t["top"], dict(params or {}), cov=cov, waves=waves, gls=t.get("gls", False))


def run_one(test, seed, params=None, cov=False, waves=False, quiet=False, extra_env=None):
    """Run one test with one seed. Returns (passed, log_path)."""
    params = dict(params or {})
    spec = TESTS[test]
    runner, bdir = build(spec["top"], params, cov=cov, waves=waves, gls=spec.get("gls", False))
    tag = f"{test}_s{seed}" + ("_" + "_".join(f"{k}{v}" for k, v in sorted(params.items())) if params else "")
    # runs that differ only in a perf knob (memory latency, size) need their
    # own directory too, or parallel runs race on results.xml
    tag += "".join(f"_{k[10:].lower()}{v}" for k, v in sorted(os.environ.items()) if k.startswith("XDMA_PERF_")
                   and k != "XDMA_PERF_OUT")
    tdir = bdir / "runs" / tag
    tdir.mkdir(parents=True, exist_ok=True)
    env = {"XDMA_SEED": str(seed), "XDMA_PARAMS": ",".join(f"{k}={v}" for k, v in params.items())}
    env.update(extra_env or {})
    if cov:
        env["XDMA_COV_FILE"] = str(tdir / "func_cov.json")
    log = tdir / "sim.log"
    plus = [f"+verilator+seed+{seed % 2**31 + 1}", "+verilator+rand+reset+2"]
    if cov:
        plus.append(f"+verilator+coverage+file+{tdir / 'coverage.dat'}")
    try:
        targs = ["--trace", "--trace-file", os.environ["XDMA_SAIF"]] if os.environ.get("XDMA_SAIF") else []
        xml = runner.test(test_module=spec.get("module", test), hdl_toplevel=spec["top"], seed=seed,
                          build_dir=bdir, test_dir=tdir, plusargs=plus, test_args=targs,
                          extra_env=env, waves=waves, log_file=log if quiet else None,
                          results_xml=str(tdir / "results.xml"))
        ok = results_ok(xml)
    except SystemExit:
        ok = False
    except Exception as e:  # build or runner failure
        print(f"runner error: {e}")
        ok = False
    if not ok:
        p = ",".join(f"{k}={v}" for k, v in params.items())
        print(f"FAIL {test} seed={seed}. Reproduce: make sim TEST={test} SEED={seed}"
              + (f" PARAMS={p}" if p else "") + f"  (log: {log})", flush=True)
    return ok, log


def results_ok(xml):
    root = ET.parse(xml).getroot()
    cases = list(root.iter("testcase"))
    if not cases:
        return False
    return all(c.find("failure") is None and c.find("error") is None for c in cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", help="comma-separated test names (default: all)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--params", default="")
    ap.add_argument("--waves", action="store_true")
    ap.add_argument("--cov", action="store_true")
    a = ap.parse_args()
    tests = a.test.split(",") if a.test else list(TESTS)
    seed = a.seed if a.seed is not None else int(os.environ.get("SEED", 1))
    params = parse_params(a.params)
    bad = [t for t in tests if not run_one(t, seed, params, cov=a.cov, waves=a.waves)[0]]
    print(f"\n{len(tests) - len(bad)}/{len(tests)} tests passed (seed {seed})")
    if bad:
        print("failed:", ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
