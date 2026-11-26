#!/usr/bin/env python3
"""Regression: every test, random tests across N seeds, in parallel.

    python3 tb/regress.py --seeds 200 --jobs 4 [--params K=V,..] [--tests a,b]

Directed tests run once (seed 1). Results go to tb/out/regress_<tag>.csv and
a one-line summary per test; the exit status is non-zero on any failure.
Each failure prints its seed and the exact command to reproduce it.
"""
import argparse, csv, os, pathlib, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

TB = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TB))
import run  # noqa: E402


def one(test, seed, params, cov):
    cmd = [sys.executable, str(TB / "run.py"), "--test", test, "--seed", str(seed)]
    if params:
        cmd += ["--params", params]
    if cov:
        cmd += ["--cov"]
    env = dict(os.environ, XDMA_SKIP_BUILD="1", VERILATOR_JOBS="1")
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    ok = p.returncode == 0 and "1/1 tests passed" in p.stdout
    if not ok:
        tail = "\n".join(p.stdout.splitlines()[-25:])
        print(f"\n=== FAIL {test} seed={seed} params={params or 'default'}\n{tail}\n"
              f"reproduce: make sim TEST={test} SEED={seed}" + (f" PARAMS={params}" if params else ""),
              flush=True)
    return dict(test=test, seed=seed, params=params or "default", ok=int(ok),
                seconds=round(time.time() - t0, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--params", default="")
    ap.add_argument("--tests", default="")
    ap.add_argument("--cov", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    # perf_* tests run under `make perf`, gate-level sims under `make gls`
    tests = a.tests.split(",") if a.tests else [t for t, v in run.TESTS.items()
                                                 if not t.startswith("perf_") and not v.get("gls")]
    params = run.parse_params(a.params)
    if params and not a.tests:
        # engine parameters only apply to tests of the full engine
        tests = [t for t in tests if run.TESTS[t]["top"] == "xdma_tb"]
    # Build every configuration once, serially.
    for t in tests:
        run.build_for(t, params, cov=a.cov)
    work = []
    for t in tests:
        seeds = range(a.first_seed, a.first_seed + a.seeds) if run.TESTS[t]["random"] else [1]
        work += [(t, s) for s in seeds]
    t0 = time.time()
    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(lambda w: one(w[0], w[1], a.params, a.cov), work))
    out = TB / "out"
    out.mkdir(exist_ok=True)
    tag = a.tag or (a.params.replace(",", "_").replace("=", "") if a.params else "default")
    with open(out / f"regress_{tag}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"\n{'test':24s} {'runs':>5s} {'pass':>5s}")
    for t in tests:
        rs = [r for r in rows if r["test"] == t]
        print(f"{t:24s} {len(rs):5d} {sum(r['ok'] for r in rs):5d}")
    npass = sum(r["ok"] for r in rows)
    print(f"\n{npass}/{len(rows)} runs passed in {time.time() - t0:.0f}s (params: {a.params or 'default'})")
    return 0 if npass == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
