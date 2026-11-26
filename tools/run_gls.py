#!/usr/bin/env python3
"""make gls: gate-level simulation (zero delay) of test_loopback_copy and
test_all_channels on the synthesized netlists, with and without clock
gating (spec 17.2). Writes tb/out/gls.json, read by `make results`.

    python3 tools/run_gls.py [--netlists final_nocg,final_cg] [--tests ...] [--seed 1]
"""
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--netlists", default="final_nocg,final_cg")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tests", default="loopback_copy,all_channels")
    a = ap.parse_args()
    out = ROOT / "tb" / "out" / "gls.json"
    res = json.load(open(out)) if out.exists() else {}
    ok = True
    for tag in a.netlists.split(","):
        net = ROOT / "syn" / "out" / f"netlist_{tag}.v"
        env = dict(os.environ, XDMA_GLS_NETLIST=str(net))
        for test in a.tests.split(","):
            p = subprocess.run([sys.executable, str(ROOT / "tb" / "run.py"), "--test", f"gls_{test}",
                                "--seed", str(a.seed)], env=env, capture_output=True, text=True)
            passed = "1/1 tests passed" in p.stdout
            m = re.search(rf"test_{test}\.test_{test}\s+(PASS|FAIL)\s+([0-9.]+)", p.stdout)
            res[f"{tag}/{test}"] = dict(netlist=tag, test=f"test_{test}", passed=passed, seed=a.seed,
                                        sim_ns=float(m.group(2)) if m else None,
                                        command=f"python3 tools/run_gls.py --netlists {tag} --tests {test} --seed {a.seed}")
            if not passed:
                (ROOT / "tb" / "out").mkdir(exist_ok=True)
                (ROOT / "tb" / "out" / f"gls_{tag}_{test}.log").write_text(p.stdout[-20000:])
            print(f"{tag} {test}: {'PASS' if passed else 'FAIL'}", flush=True)
            ok &= passed
    out.parent.mkdir(exist_ok=True)
    json.dump(res, open(out, "w"), indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
