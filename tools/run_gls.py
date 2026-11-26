#!/usr/bin/env python3
"""make gls: gate-level simulation (zero delay) of test_loopback_copy on the
synthesized netlists (spec 17.2). Writes tb/out/gls.json, read by
`make results`.

    python3 tools/run_gls.py [--netlists final_nocg,final_cg] [--seed 1]
"""
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--netlists", default="final_nocg")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    out = ROOT / "tb" / "out" / "gls.json"
    res = json.load(open(out)) if out.exists() else {}
    ok = True
    for tag in a.netlists.split(","):
        net = ROOT / "syn" / "out" / f"netlist_{tag}.v"
        env = dict(os.environ, XDMA_GLS_NETLIST=str(net))
        p = subprocess.run([sys.executable, str(ROOT / "tb" / "run.py"), "--test", "gls_loopback_copy",
                            "--seed", str(a.seed)], env=env, capture_output=True, text=True)
        passed = "1/1 tests passed" in p.stdout
        m = re.search(r"test_loopback_copy\.test_loopback_copy\s+PASS\s+([0-9.]+)", p.stdout)
        res[tag] = dict(passed=passed, seed=a.seed, sim_ns=float(m.group(1)) if m else None,
                        command=f"python3 tools/run_gls.py --netlists {tag} --seed {a.seed}")
        print(f"{tag}: {'PASS' if passed else 'FAIL'}")
        ok &= passed
    out.parent.mkdir(exist_ok=True)
    json.dump(res, open(out, "w"), indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
