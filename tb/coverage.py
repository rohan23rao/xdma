#!/usr/bin/env python3
"""make cov: code, assertion and functional coverage (spec 14.4).

1. Runs the regression with coverage builds (Verilator --coverage-line,
   --coverage-toggle, --coverage-user) at default parameters and at the
   small-parameter set, writing one coverage.dat and one func_cov.json per
   run.
2. Merges the .dat files (verilator_coverage) and reports, for rtl/ only:
   line and toggle coverage; for all assertion modules: cover-property hits.
3. Merges the functional bins (tb/fcov.py) across runs.
Writes tb/out/coverage.json, tb/out/coverage.md and an annotated source
tree in tb/out/cov_annotate/.

    python3 tb/coverage.py [--seeds 10] [--jobs 4]
"""
import argparse, collections, json, pathlib, re, subprocess, sys

TB = pathlib.Path(__file__).resolve().parent
ROOT = TB.parent
OUT = TB / "out"
sys.path.insert(0, str(TB))
from fcov import BINS  # noqa: E402

SMALL = "FIFO_DEPTH=32,MAX_OUT_CH=4,MAX_BURST=8"


def run_regress(seeds, jobs, params="", tag="cov"):
    cmd = [sys.executable, str(TB / "regress.py"), "--seeds", str(seeds), "--jobs", str(jobs),
           "--cov", "--tag", tag]
    if params:
        cmd += ["--params", params]
    tests = [t for t in __import__("run").TESTS if t.startswith("test_") and t != "test_addr_gen"]
    cmd += ["--tests", ",".join(tests)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    print("\n".join(p.stdout.splitlines()[-6:]))
    return p.returncode == 0


def parse_dat(path):
    """{(page, file, line, col, name/comment): count} from a coverage.dat."""
    pts = {}
    for line in open(path, errors="replace"):
        if not line.startswith("C '"):
            continue
        key, cnt = line[3:].rsplit("' ", 1)
        fields = dict(f.split("\x02", 1) for f in key.split("\x01") if "\x02" in f)
        k = (fields.get("page", ""), fields.get("f", ""), fields.get("l", ""), fields.get("n", ""),
             fields.get("o", ""), fields.get("h", ""))
        pts[k] = pts.get(k, 0) + int(cnt)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-run", action="store_true", help="only merge existing results")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    ok = True
    if not a.no_run:
        ok &= run_regress(a.seeds, a.jobs)
        ok &= run_regress(max(2, a.seeds // 2), a.jobs, SMALL, "cov_small")

    dats = sorted(ROOT.glob("tb/build/*/runs/*/coverage.dat"))
    funcs = sorted(ROOT.glob("tb/build/*/runs/*/func_cov.json"))
    merged = OUT / "coverage_merged.dat"
    subprocess.run(["verilator_coverage", "--write", str(merged)] + [str(d) for d in dats],
                   check=True, capture_output=True)
    subprocess.run(["verilator_coverage", "--annotate", str(OUT / "cov_annotate"), str(merged)],
                   capture_output=True)
    pts = parse_dat(merged)

    def frac(page_prefix, file_filter):
        sel = [(k, v) for k, v in pts.items() if k[0].startswith(page_prefix) and file_filter(k[1])]
        hit = sum(1 for _, v in sel if v > 0)
        return hit, len(sel), [k for k, v in sel if v == 0]

    is_rtl = lambda f: "/rtl/" in f or f.startswith("rtl/")
    lh, lt, lmiss = frac("v_line", is_rtl)
    bh, bt, bmiss = frac("v_branch", is_rtl)
    th, tt, tmiss = frac("v_toggle", is_rtl)
    ch, ct, cmiss = frac("v_user", lambda f: True)

    hits = collections.defaultdict(set)
    for fpath in funcs:
        for k, v in json.load(open(fpath)).items():
            hits[k] |= set(v)
    func_total = sum(len(v) for v in BINS.values())
    func_hit = sum(len(hits[k] & {str(b) for b in v}) for k, v in BINS.items())
    missing = {k: sorted({str(b) for b in v} - hits[k]) for k, v in BINS.items()}
    missing = {k: v for k, v in missing.items() if v}

    md = ["| Coverage | Hit | Total | % |", "|---|---|---|---|"]
    for name, h, t in (("RTL lines", lh, lt), ("RTL branches", bh, bt), ("RTL toggles", th, tt),
                       ("Assertion cover properties", ch, ct), ("Functional bins (spec 14.4)", func_hit, func_total)):
        if t:
            md.append(f"| {name} | {h} | {t} | {100 * h / t:.1f}% |")
    md += ["", f"Runs merged: {len(dats)} code-coverage databases, {len(funcs)} functional-coverage files "
           f"(default parameters and {SMALL})."]
    md += ["", "Functional coverpoints:", "", "| Coverpoint | Bins hit | Missing |", "|---|---|---|"]
    for k, v in BINS.items():
        hv = hits[k] & {str(b) for b in v}
        md.append(f"| {k} | {len(hv)}/{len(v)} | {', '.join(missing.get(k, [])) or '-'} |")
    if cmiss:
        md += ["", "Cover properties never hit:", ""]
        md += [f"- `{k[3] or k[4]}` ({pathlib.Path(k[1]).name}:{k[2]})" for k in cmiss]
    res = dict(func_hit=func_hit, func_total=func_total, func_missing=missing,
               line=f"{100 * lh / lt:.1f}%" if lt else "n/a", branch=f"{100 * bh / bt:.1f}%" if bt else "n/a",
               toggle=f"{100 * th / tt:.1f}%" if tt else "n/a", covers_hit=ch, covers_total=ct,
               line_missed=[f"{pathlib.Path(k[1]).name}:{k[2]}" for k in lmiss],
               branch_missed=[f"{pathlib.Path(k[1]).name}:{k[2]}" for k in bmiss],
               covers_missed=[k[3] or k[4] for k in cmiss], markdown="\n".join(md))
    json.dump(res, open(OUT / "coverage.json", "w"), indent=1)
    (OUT / "coverage.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
